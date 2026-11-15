"""Website Clone Opt-In Telegram Preferences, QR Pairing & Branch Selection (`CurrentUser` only)."""

from __future__ import annotations

import re

from fastapi import APIRouter
from sqlalchemy import select

from app import clock
from app.audit import record_audit
from app.auth import CurrentUser, DbSession
from app.config import settings
from app.errors import AppError, request_id_var
from app.models import UserTelegramSetting
from app.schemas import (
    TelegramBranchUpdateRequest,
    TelegramLinkTokenOut,
    TelegramOptInOut,
    TelegramOptInUpdateRequest,
    TelegramRecipientsResponse,
    TelegramVerifyChatRequest,
)
from app.services.telegram_customer import (
    build_telegram_opt_in_out,
    create_link_token,
    ensure_customer_session_for_opt_in,
    revoke_user_telegram,
    set_web_user_branch,
)

router = APIRouter(prefix="/auth/telegram", tags=["auth"])

_NUMERIC_ID_RE = re.compile(r"^\d{5,16}$")
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,32}$")


def _normalize_handle(raw: str) -> str:
    return raw.strip().lstrip("@").lower()


@router.get("", response_model=TelegramOptInOut)
def get_telegram_opt_in(
    principal: CurrentUser,
    db: DbSession,
) -> TelegramOptInOut:
    """Return the authenticated human user's Telegram preferences, QR status, and branches."""
    row = db.get(UserTelegramSetting, principal.id)
    return build_telegram_opt_in_out(
        db,
        user_id=principal.id,
        company_id=principal.company_id,
        row=row,
    )


@router.put("", response_model=TelegramOptInOut)
def put_telegram_opt_in(
    payload: TelegramOptInUpdateRequest,
    principal: CurrentUser,
    db: DbSession,
) -> TelegramOptInOut:
    """Explicitly opt in or update personal Telegram notifications for the logged-in user."""
    raw = payload.telegram_id.strip()
    if not raw:
        raise AppError(
            "VALIDATION_ERROR",
            422,
            "Enter a @username or numeric private Telegram user ID.",
            details={"field": "telegram_id"},
        )
    if raw.startswith("-"):
        raise AppError(
            "VALIDATION_ERROR",
            422,
            (
                "Group chat IDs (negative numbers) are not permitted; "
                "use your private @username or numeric user ID."
            ),
            details={"field": "telegram_id"},
        )

    is_numeric = bool(_NUMERIC_ID_RE.match(raw))
    clean_handle = raw[1:] if raw.startswith("@") else raw
    is_username = not is_numeric and bool(_USERNAME_RE.match(clean_handle))
    if not is_numeric and not is_username:
        raise AppError(
            "VALIDATION_ERROR",
            422,
            "Telegram ID must be a @username (3-32 chars) or a 5-16 digit private numeric ID.",
            details={"field": "telegram_id"},
        )

    if payload.enabled and not payload.consent_confirmed:
        raise AppError(
            "VALIDATION_ERROR",
            422,
            "Explicit consent confirmation is required to enable Telegram notifications.",
            details={"field": "consent_confirmed"},
        )

    now = clock.now()
    row = db.get(UserTelegramSetting, principal.id)
    if row is None:
        row = UserTelegramSetting(
            user_id=principal.id,
            company_id=principal.company_id,
            telegram_username=clean_handle if is_username else None,
            telegram_chat_id=raw if is_numeric else None,
            enabled=bool(payload.enabled),
            consented_at=now if payload.enabled else None,
            updated_at=now,
        )
        db.add(row)
    else:
        row.company_id = principal.company_id
        if is_username:
            row.telegram_username = clean_handle
        if is_numeric:
            row.telegram_chat_id = raw
        row.enabled = bool(payload.enabled)
        row.consented_at = (row.consented_at or now) if payload.enabled else None
        row.updated_at = now
    db.flush()

    record_audit(
        db,
        company_id=principal.company_id,
        actor=principal,
        action="user.telegram_opt_in_updated",
        entity_type="user",
        entity_id=principal.id,
        outcome="ok",
        details={
            "request_id": request_id_var.get() or None,
            "enabled": bool(row.enabled),
            "has_username": bool(row.telegram_username),
            "has_chat_id": bool(row.telegram_chat_id),
        },
    )
    db.commit()
    db.refresh(row)
    return build_telegram_opt_in_out(
        db,
        user_id=principal.id,
        company_id=principal.company_id,
        row=row,
    )


@router.delete("", response_model=TelegramOptInOut)
def delete_telegram_opt_in(
    principal: CurrentUser,
    db: DbSession,
) -> TelegramOptInOut:
    """Revoke Telegram opt-in and delete any linked customer Telegram session."""
    return revoke_user_telegram(db, principal)


@router.post("/link-token", response_model=TelegramLinkTokenOut, status_code=201)
def post_telegram_link_token(
    principal: CurrentUser,
    db: DbSession,
) -> TelegramLinkTokenOut:
    """Generate a 15-minute one-time Telegram deep link + ISO 18004 scannable QR SVG."""
    return create_link_token(db, principal)


@router.put("/branch", response_model=TelegramOptInOut)
def put_telegram_branch(
    payload: TelegramBranchUpdateRequest,
    principal: CurrentUser,
    db: DbSession,
) -> TelegramOptInOut:
    """Select active kitchen/hotel branch for the logged-in user's Telegram ordering session."""
    return set_web_user_branch(db, principal, payload.branch_id)


@router.get("/recipients", response_model=TelegramRecipientsResponse)
def list_telegram_recipients(
    principal: CurrentUser,
    db: DbSession,
) -> TelegramRecipientsResponse:
    """Return ONLY enabled=True Telegram opt-in recipients in the caller's company."""
    rows = db.scalars(
        select(UserTelegramSetting)
        .where(
            UserTelegramSetting.company_id == principal.company_id,
            UserTelegramSetting.enabled.is_(True),
        )
        .order_by(UserTelegramSetting.updated_at.desc(), UserTelegramSetting.user_id.asc())
    ).all()
    bot_username = (settings.telegram_bot_username or "your_aimo_bot").strip().lstrip("@")
    return TelegramRecipientsResponse(
        items=[
            build_telegram_opt_in_out(
                db,
                user_id=r.user_id,
                company_id=r.company_id,
                row=r,
            ).model_copy(update={"bot_username": bot_username, "enabled": True})
            for r in rows
        ]
    )


@router.post("/verify-chat", response_model=TelegramOptInOut)
def verify_telegram_chat(
    payload: TelegramVerifyChatRequest,
    principal: CurrentUser,
    db: DbSession,
) -> TelegramOptInOut:
    """Bind verified private numeric chat_id onto an EXISTING enabled=True row in caller's company.

    Never auto-enables a disabled user or crosses company boundaries (`CRITIQUE.md` §10.8).
    """
    raw_chat = payload.telegram_chat_id.strip()
    if raw_chat.startswith("-") or not _NUMERIC_ID_RE.match(raw_chat):
        raise AppError(
            "VALIDATION_ERROR",
            422,
            "telegram_chat_id must be a positive numeric private chat ID.",
            details={"field": "telegram_chat_id"},
        )

    norm_username = _normalize_handle(payload.telegram_username)
    raw_uid = payload.telegram_user_id.strip()

    enabled_rows = db.scalars(
        select(UserTelegramSetting).where(
            UserTelegramSetting.company_id == principal.company_id,
            UserTelegramSetting.enabled.is_(True),
        )
    ).all()

    matched_row: UserTelegramSetting | None = None
    for row in enabled_rows:
        row_u = _normalize_handle(row.telegram_username or "")
        row_cid = (row.telegram_chat_id or "").strip()
        if norm_username and row_u and norm_username == row_u:
            matched_row = row
            break
        if raw_uid and row_cid and raw_uid == row_cid:
            matched_row = row
            break
        if raw_chat and row_cid and raw_chat == row_cid:
            matched_row = row
            break

    if matched_row is None:
        raise AppError(
            "NOT_FOUND",
            404,
            "No matching enabled Telegram opt-in record found in company",
        )

    now = clock.now()
    matched_row.telegram_chat_id = raw_chat
    if norm_username and not matched_row.telegram_username:
        matched_row.telegram_username = payload.telegram_username.strip().lstrip("@")
    matched_row.updated_at = now
    ensure_customer_session_for_opt_in(
        db,
        opt=matched_row,
        telegram_user_id=raw_uid or raw_chat,
        telegram_username=payload.telegram_username,
    )

    record_audit(
        db,
        company_id=principal.company_id,
        actor=principal,
        action="user.telegram_chat_verified",
        entity_type="user",
        entity_id=matched_row.user_id,
        outcome="ok",
        details={
            "request_id": request_id_var.get() or None,
            "user_id": matched_row.user_id,
        },
    )
    db.commit()
    db.refresh(matched_row)
    return build_telegram_opt_in_out(
        db,
        user_id=matched_row.user_id,
        company_id=matched_row.company_id,
        row=matched_row,
    )
