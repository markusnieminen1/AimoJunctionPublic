"""Per-user customer Telegram service: QR/deep-link pairing, multi-branch & buyer chat."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from app import clock
from app.aimo.client import AimoClient
from app.audit import record_audit
from app.config import settings
from app.errors import AppError, request_id_var
from app.llm.decisions import DecisionProvider
from app.llm.prep_list import default_delivery_date, parse_prep_list
from app.llm.provider import FakeProvider, LLMProvider
from app.models import (
    Company,
    CompanyBranch,
    Order,
    Quote,
    TelegramCustomerSession,
    TelegramLinkToken,
    User,
    UserTelegramSetting,
)
from app.schemas import (
    A2ANegotiationTurn,
    AssistantA2ANegotiationAction,
    AssistantChatRequest,
    AssistantClarificationAction,
    AssistantDealsAction,
    AssistantMenuPlanAction,
    AssistantOrderAction,
    AssistantQuoteAction,
    AssistantRecipeSanityWarningAction,
    AssistantSearchResultsAction,
    CompanyBranchOut,
    DealsResponse,
    MenuPlanResponse,
    OrderApproveRequest,
    OrderCreateRequest,
    OrderDecisionRequest,
    OrderOut,
    Principal,
    ProductOut,
    QuoteCreateRequest,
    QuoteLineInput,
    QuoteOut,
    RecipeSanityWarning,
    TelegramCancelOrderOut,
    TelegramCancelOrderRequest,
    TelegramConnectedSessionOut,
    TelegramConnectedSessionsResponse,
    TelegramCustomerChatOut,
    TelegramCustomerChatRequest,
    TelegramDecideOrderOut,
    TelegramDecideOrderRequest,
    TelegramLinkClaimOut,
    TelegramLinkClaimRequest,
    TelegramLinkTokenOut,
    TelegramOptInOut,
    TelegramOrderNotificationEventType,
    TelegramOrderNotificationOut,
    TelegramOrderNotificationsResponse,
    TelegramPlaceQuoteOut,
    TelegramPlaceQuoteRequest,
    TelegramSelectBranchOut,
    TelegramSelectBranchRequest,
    TelegramUnlinkOut,
    TelegramUnlinkRequest,
)
from app.services import assistant, orders, quotes
from app.services.order_gateway import OrderGateway
from app.services.qr_svg import generate_qr_svg

BRIDGE_SECRET_MESSAGE = b"aimo-tg-bridge-v1"
_POSITIVE_INT_RE = re.compile(r"^[1-9]\d{0,19}$")
_WORD_RE = re.compile(r"[a-z0-9äöå]{4,}", re.I)


def compute_bridge_secret(bot_token: str) -> str:
    """Compute `X-Telegram-Bridge-Secret` HMAC-SHA256 matching `scripts/telegram_bridge.py`."""
    return hmac.new(
        bot_token.encode("utf-8"),
        BRIDGE_SECRET_MESSAGE,
        hashlib.sha256,
    ).hexdigest()


def verify_bridge_secret(provided_secret: str | None) -> None:
    """Fail-closed guard requiring valid `X-Telegram-Bridge-Secret` header."""
    bot_token = (settings.telegram_bot_token or "").strip()
    if not bot_token or not provided_secret:
        raise AppError(
            "FORBIDDEN",
            403,
            "Invalid or missing X-Telegram-Bridge-Secret",
        )
    expected = compute_bridge_secret(bot_token)
    if not hmac.compare_digest(provided_secret.strip(), expected):
        raise AppError(
            "FORBIDDEN",
            403,
            "Invalid or missing X-Telegram-Bridge-Secret",
        )


def _normalize_username(raw: str | None) -> str | None:
    if not raw:
        return None
    cleaned = raw.strip().lstrip("@")
    return cleaned or None


def _validate_private_numeric_id(raw: str, field_name: str) -> str:
    val = raw.strip()
    if not _POSITIVE_INT_RE.match(val):
        raise AppError(
            "VALIDATION_ERROR",
            422,
            f"{field_name} must be a positive private numeric Telegram ID.",
            details={"field": field_name},
        )
    return val


def branch_to_out(row: CompanyBranch) -> CompanyBranchOut:
    """Convert a CompanyBranch ORM row into API schema."""
    return CompanyBranchOut(
        id=row.id,
        name=row.name,
        short_name=row.short_name,
        address=row.address,
    )


def list_company_branches(db: Session, company_id: str) -> list[CompanyBranch]:
    """Return active kitchen/hotel branches for a company in stable display order."""
    return list(
        db.scalars(
            select(CompanyBranch)
            .where(
                CompanyBranch.company_id == company_id,
                CompanyBranch.active.is_(True),
            )
            .order_by(CompanyBranch.created_at.asc(), CompanyBranch.id.asc())
        ).all()
    )


def build_telegram_opt_in_out(
    db: Session,
    *,
    user_id: str,
    company_id: str,
    row: UserTelegramSetting | None,
) -> TelegramOptInOut:
    """Serialize TelegramOptInOut enriched with QR link status and company branches."""
    branches = list_company_branches(db, company_id)
    branches_out = [branch_to_out(b) for b in branches]
    session = db.get(TelegramCustomerSession, user_id)
    active_branch = (
        next((b for b in branches if session and b.id == session.active_branch_id), None)
        if session
        else None
    )
    linked_via_qr = bool(
        row is not None
        and row.enabled
        and session is not None
        and bool(session.telegram_chat_id)
    )
    bot_username = (settings.telegram_bot_username or "your_aimo_bot").strip().lstrip("@")
    if row is None:
        return TelegramOptInOut(
            user_id=user_id,
            company_id=company_id,
            telegram_username=None,
            telegram_chat_id=None,
            enabled=False,
            bot_username=bot_username,
            consented_at=None,
            updated_at=None,
            linked_via_qr=False,
            branches=branches_out,
            active_branch_id=active_branch.id if active_branch else None,
            active_branch_name=active_branch.short_name if active_branch else None,
        )
    return TelegramOptInOut(
        user_id=row.user_id,
        company_id=row.company_id,
        telegram_username=row.telegram_username,
        telegram_chat_id=row.telegram_chat_id,
        enabled=bool(row.enabled),
        bot_username=bot_username,
        consented_at=row.consented_at,
        updated_at=row.updated_at,
        linked_via_qr=linked_via_qr,
        branches=branches_out,
        active_branch_id=active_branch.id if active_branch else None,
        active_branch_name=active_branch.short_name if active_branch else None,
    )


def create_link_token(db: Session, principal: Principal) -> TelegramLinkTokenOut:
    """Create a 15-minute SHA-256 hashed pairing token + ISO 18004 QR SVG for a web user."""
    raw_token = secrets.token_urlsafe(16)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    now = clock.now()
    expires_at = now + timedelta(minutes=15)

    tok_row = TelegramLinkToken(
        token_hash=token_hash,
        user_id=principal.id,
        company_id=principal.company_id,
        expires_at=expires_at,
        consumed_at=None,
        created_at=now,
    )
    db.add(tok_row)
    db.flush()

    bot_username = (settings.telegram_bot_username or "your_aimo_bot").strip().lstrip("@")
    deep_link_url = f"https://t.me/{bot_username}?start=link_{raw_token}"
    tg_app_url = f"tg://resolve?domain={bot_username}&start=link_{raw_token}"
    qr_svg = generate_qr_svg(deep_link_url)

    record_audit(
        db,
        company_id=principal.company_id,
        actor=principal,
        action="user.telegram_link_token_created",
        entity_type="user",
        entity_id=principal.id,
        outcome="ok",
        details={
            "request_id": request_id_var.get() or None,
            "expires_at": clock.format_helsinki_iso(expires_at),
        },
    )
    db.commit()
    return TelegramLinkTokenOut(
        token=raw_token,
        bot_username=bot_username,
        deep_link_url=deep_link_url,
        tg_app_url=tg_app_url,
        expires_at=expires_at,
        qr_svg=qr_svg,
    )


def set_web_user_branch(
    db: Session,
    principal: Principal,
    branch_id: str,
) -> TelegramOptInOut:
    """Validate branch ownership, update active_branch_id, and reset conversation."""
    clean_bid = branch_id.strip()
    branch = db.scalar(
        select(CompanyBranch).where(
            CompanyBranch.id == clean_bid,
            CompanyBranch.company_id == principal.company_id,
            CompanyBranch.active.is_(True),
        )
    )
    if branch is None:
        raise AppError("NOT_FOUND", 404, "Company branch not found")

    now = clock.now()
    session = db.get(TelegramCustomerSession, principal.id)
    if session is None:
        session = TelegramCustomerSession(
            user_id=principal.id,
            company_id=principal.company_id,
            telegram_user_id=None,
            telegram_chat_id=None,
            telegram_username=None,
            active_branch_id=branch.id,
            active_conversation_id=None,
            pending_message=None,
            updated_at=now,
        )
        db.add(session)
    else:
        session.active_branch_id = branch.id
        session.active_conversation_id = None
        session.updated_at = now
    db.flush()

    record_audit(
        db,
        company_id=principal.company_id,
        actor=principal,
        action="user.telegram_branch_selected",
        entity_type="user",
        entity_id=principal.id,
        outcome="ok",
        details={
            "request_id": request_id_var.get() or None,
            "branch_id": branch.id,
            "branch_name": branch.short_name,
        },
    )
    db.commit()
    opt_row = db.get(UserTelegramSetting, principal.id)
    return build_telegram_opt_in_out(
        db,
        user_id=principal.id,
        company_id=principal.company_id,
        row=opt_row,
    )


def revoke_user_telegram(db: Session, principal: Principal) -> TelegramOptInOut:
    """Revoke Telegram opt-in AND delete customer session + unconsumed link tokens."""
    now = clock.now()
    row = db.get(UserTelegramSetting, principal.id)
    if row is None:
        row = UserTelegramSetting(
            user_id=principal.id,
            company_id=principal.company_id,
            telegram_username=None,
            telegram_chat_id=None,
            enabled=False,
            consented_at=None,
            updated_at=now,
        )
        db.add(row)
    else:
        row.telegram_username = None
        row.telegram_chat_id = None
        row.enabled = False
        row.consented_at = None
        row.updated_at = now

    db.execute(
        delete(TelegramCustomerSession).where(TelegramCustomerSession.user_id == principal.id)
    )
    open_tokens = db.scalars(
        select(TelegramLinkToken).where(
            TelegramLinkToken.user_id == principal.id,
            TelegramLinkToken.consumed_at.is_(None),
        )
    ).all()
    for tok in open_tokens:
        tok.consumed_at = now
    db.flush()

    record_audit(
        db,
        company_id=principal.company_id,
        actor=principal,
        action="user.telegram_opt_in_revoked",
        entity_type="user",
        entity_id=principal.id,
        outcome="ok",
        details={"request_id": request_id_var.get() or None, "enabled": False},
    )
    db.commit()
    db.refresh(row)
    return build_telegram_opt_in_out(
        db,
        user_id=principal.id,
        company_id=principal.company_id,
        row=row,
    )


def claim_link_token(db: Session, req: TelegramLinkClaimRequest) -> TelegramLinkClaimOut:
    """Consume a one-time pairing token from `/start link_<tok>` and bind private chat."""
    clean_chat_id = _validate_private_numeric_id(req.telegram_chat_id, "telegram_chat_id")
    clean_user_id = _validate_private_numeric_id(req.telegram_user_id, "telegram_user_id")
    clean_username = _normalize_username(req.telegram_username)

    raw_token = req.token.strip()
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    now = clock.now()

    tok_row = db.get(TelegramLinkToken, token_hash)
    if (
        tok_row is None
        or tok_row.consumed_at is not None
        or clock.to_helsinki(tok_row.expires_at) <= clock.to_helsinki(now)
    ):
        raise AppError(
            "NOT_FOUND",
            404,
            "Telegram pairing link is invalid, expired, or already used",
        )

    user = db.get(User, tok_row.user_id)
    company = db.get(Company, tok_row.company_id)
    if user is None or company is None:
        raise AppError("NOT_FOUND", 404, "Linked user or company not found")

    tok_row.consumed_at = now

    # Unbind any stale other-user session sharing the same private chat_id
    stale_sessions = db.scalars(
        select(TelegramCustomerSession).where(
            TelegramCustomerSession.telegram_chat_id == clean_chat_id,
            TelegramCustomerSession.user_id != user.id,
        )
    ).all()
    for stale in stale_sessions:
        db.delete(stale)

    stale_settings = db.scalars(
        select(UserTelegramSetting).where(
            UserTelegramSetting.telegram_chat_id == clean_chat_id,
            UserTelegramSetting.user_id != user.id,
        )
    ).all()
    for stale_opt in stale_settings:
        stale_opt.telegram_chat_id = None
        stale_opt.enabled = False
        stale_opt.updated_at = now

    branches = list_company_branches(db, company.id)
    branches_out = [branch_to_out(b) for b in branches]
    default_branch = branches[0] if len(branches) == 1 else None

    opt_row = db.get(UserTelegramSetting, user.id)
    if opt_row is None:
        opt_row = UserTelegramSetting(
            user_id=user.id,
            company_id=company.id,
            telegram_username=clean_username,
            telegram_chat_id=clean_chat_id,
            enabled=True,
            consented_at=now,
            updated_at=now,
        )
        db.add(opt_row)
    else:
        opt_row.company_id = company.id
        if clean_username:
            opt_row.telegram_username = clean_username
        opt_row.telegram_chat_id = clean_chat_id
        opt_row.enabled = True
        opt_row.consented_at = opt_row.consented_at or now
        opt_row.updated_at = now

    session = db.get(TelegramCustomerSession, user.id)
    if session is None:
        session = TelegramCustomerSession(
            user_id=user.id,
            company_id=company.id,
            telegram_user_id=clean_user_id,
            telegram_chat_id=clean_chat_id,
            telegram_username=clean_username,
            active_branch_id=default_branch.id if default_branch else None,
            active_conversation_id=None,
            pending_message=None,
            updated_at=now,
        )
        db.add(session)
    else:
        session.company_id = company.id
        session.telegram_user_id = clean_user_id
        session.telegram_chat_id = clean_chat_id
        session.telegram_username = clean_username or session.telegram_username
        session.active_branch_id = default_branch.id if default_branch else None
        session.active_conversation_id = None
        session.pending_message = None
        session.updated_at = now
    db.flush()

    actor = Principal(
        kind="user",
        id=user.id,
        name=user.name,
        company_id=company.id,
        email=user.email,
    )
    record_audit(
        db,
        company_id=company.id,
        actor=actor,
        action="user.telegram_qr_linked",
        entity_type="user",
        entity_id=user.id,
        outcome="ok",
        details={
            "request_id": request_id_var.get() or None,
            "telegram_chat_id": clean_chat_id,
            "branch_count": len(branches),
        },
    )
    db.commit()

    active_branch_out = branch_to_out(default_branch) if default_branch else None
    return TelegramLinkClaimOut(
        user_id=user.id,
        user_name=user.name,
        user_email=user.email,
        company_id=company.id,
        company_name=company.name,
        branches=branches_out,
        active_branch=active_branch_out,
        needs_branch_selection=len(branches) > 1 and active_branch_out is None,
    )


def _format_branch_choice(branches: list[CompanyBranch]) -> str:
    names = [b.short_name or b.name for b in branches]
    if not names:
        return "your kitchen"
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} or {names[1]}"
    return f"{', '.join(names[:-1])}, or {names[-1]}"


def _match_unambiguous_branch(
    message: str,
    branches: list[CompanyBranch],
) -> CompanyBranch | None:
    if not branches:
        return None
    msg_l = message.lower()

    full_hits = [
        b
        for b in branches
        if b.short_name.lower() in msg_l or b.name.lower() in msg_l
    ]
    if len(full_hits) == 1:
        return full_hits[0]

    branch_tokens: dict[str, set[str]] = {}
    token_counts: dict[str, int] = {}
    for b in branches:
        toks = set(_WORD_RE.findall(f"{b.name.lower()} {b.short_name.lower()}"))
        expanded = set(toks)
        for tok in toks:
            if len(tok) >= 10:
                expanded.add(tok[:9])
        branch_tokens[b.id] = expanded
        for tok in expanded:
            token_counts[tok] = token_counts.get(tok, 0) + 1

    unique_hits: list[CompanyBranch] = []
    for b in branches:
        distinctive = {t for t in branch_tokens[b.id] if token_counts.get(t, 0) == 1}
        if any(re.search(rf"\b{re.escape(t)}", msg_l) for t in distinctive):
            unique_hits.append(b)

    if len(unique_hits) == 1:
        return unique_hits[0]
    return None


def ensure_customer_session_for_opt_in(
    db: Session,
    *,
    opt: UserTelegramSetting,
    telegram_user_id: str | None = None,
    telegram_username: str | None = None,
) -> TelegramCustomerSession | None:
    """Ensure a conversational TelegramCustomerSession exists for a verified enabled opt-in."""
    raw_chat = (opt.telegram_chat_id or "").strip()
    if not opt.enabled or not _POSITIVE_INT_RE.match(raw_chat):
        return None
    raw_uid = (telegram_user_id or "").strip()
    eff_uid = raw_uid if _POSITIVE_INT_RE.match(raw_uid) else raw_chat
    clean_uname = (telegram_username or opt.telegram_username or "").strip().lstrip("@") or None
    branches = list_company_branches(db, opt.company_id)
    default_branch_id = branches[0].id if len(branches) == 1 else None
    now = clock.now()

    sess = db.get(TelegramCustomerSession, opt.user_id)
    if sess is None:
        # Remove any stale session bound to the same private chat_id for a different user
        stale = db.scalar(
            select(TelegramCustomerSession).where(
                TelegramCustomerSession.telegram_chat_id == raw_chat,
                TelegramCustomerSession.user_id != opt.user_id,
            )
        )
        if stale is not None:
            db.delete(stale)
            db.flush()
        sess = TelegramCustomerSession(
            user_id=opt.user_id,
            company_id=opt.company_id,
            telegram_user_id=eff_uid,
            telegram_chat_id=raw_chat,
            telegram_username=clean_uname,
            active_branch_id=default_branch_id,
            active_conversation_id=None,
            pending_message=None,
            updated_at=now,
        )
        db.add(sess)
    else:
        sess.company_id = opt.company_id
        sess.telegram_user_id = eff_uid
        sess.telegram_chat_id = raw_chat
        if clean_uname:
            sess.telegram_username = clean_uname
        if sess.active_branch_id is None and default_branch_id is not None:
            sess.active_branch_id = default_branch_id
        sess.updated_at = now
    db.flush()
    return sess


def _load_linked_session_and_user(
    db: Session,
    *,
    telegram_chat_id: str,
    telegram_user_id: str,
    telegram_username: str | None = None,
) -> tuple[TelegramCustomerSession, User, Company]:
    clean_chat_id = _validate_private_numeric_id(telegram_chat_id, "telegram_chat_id")
    clean_user_id = _validate_private_numeric_id(telegram_user_id, "telegram_user_id")
    norm_username = (telegram_username or "").strip().lstrip("@").lower()

    session = db.scalar(
        select(TelegramCustomerSession).where(
            TelegramCustomerSession.telegram_chat_id == clean_chat_id
        )
    )
    if session is None:
        # Hydrate seamlessly from an enabled website opt-in (`UserTelegramSetting.enabled=True`)
        matched_opt = db.scalar(
            select(UserTelegramSetting).where(
                UserTelegramSetting.enabled.is_(True),
                UserTelegramSetting.telegram_chat_id == clean_chat_id,
            )
        )
        if matched_opt is None and norm_username:
            matched_opt = db.scalar(
                select(UserTelegramSetting).where(
                    UserTelegramSetting.enabled.is_(True),
                    UserTelegramSetting.telegram_chat_id.is_(None),
                    func.lower(UserTelegramSetting.telegram_username) == norm_username,
                )
            )
            if matched_opt is not None:
                matched_opt.telegram_chat_id = clean_chat_id
                matched_opt.updated_at = clock.now()
                db.flush()
        if matched_opt is not None:
            session = ensure_customer_session_for_opt_in(
                db,
                opt=matched_opt,
                telegram_user_id=clean_user_id,
                telegram_username=telegram_username,
            )

    if session is None or session.telegram_user_id != clean_user_id:
        raise AppError(
            "NOT_FOUND",
            404,
            "Telegram chat is not linked to an active customer session",
        )

    opt = db.get(UserTelegramSetting, session.user_id)
    if opt is None or not opt.enabled or opt.telegram_chat_id != clean_chat_id:
        raise AppError("NOT_FOUND", 404, "Telegram chat is not linked or opt-in is disabled")

    user = db.get(User, session.user_id)
    company = db.get(Company, session.company_id)
    if user is None or company is None:
        raise AppError("NOT_FOUND", 404, "Linked user or company not found")

    return session, user, company


def _execute_governed_buyer_turn(
    db: Session,
    *,
    aimo_client: AimoClient,
    gateway: OrderGateway,
    llm_provider: LLMProvider,
    decision_provider: DecisionProvider,
    session: TelegramCustomerSession,
    user: User,
    branches: list[CompanyBranch],
    active_branch: CompanyBranch | None,
    raw_message: str,
) -> TelegramCustomerChatOut:
    principal = Principal(
        kind="user",
        id=user.id,
        name=user.name,
        company_id=user.company_id,
        email=user.email,
    )
    chat_req = AssistantChatRequest(
        conversation_id=session.active_conversation_id,
        message=raw_message,
    )
    branch_id_val = active_branch.id if active_branch is not None else None
    branch_name_val = active_branch.short_name if active_branch is not None else None
    try:
        chat_res = assistant.chat(
            db=db,
            aimo_client=aimo_client,
            gateway=gateway,
            llm_provider=llm_provider,
            decision_provider=decision_provider,
            principal=principal,
            req=chat_req,
            channel="telegram",
            branch_id=branch_id_val,
            branch_name=branch_name_val,
            telegram_chat_id=session.telegram_chat_id,
        )
    except AppError as exc:
        if exc.code != "LLM_UNAVAILABLE":
            raise
        chat_res = assistant.chat(
            db=db,
            aimo_client=aimo_client,
            gateway=gateway,
            llm_provider=FakeProvider(),
            decision_provider=decision_provider,
            principal=principal,
            req=chat_req,
            channel="telegram",
            branch_id=branch_id_val,
            branch_name=branch_name_val,
            telegram_chat_id=session.telegram_chat_id,
        )

    session.active_conversation_id = chat_res.conversation_id
    session.pending_message = None
    session.updated_at = clock.now()

    quote_out: QuoteOut | None = None
    order_out: OrderOut | None = None
    menu_plan_out: MenuPlanResponse | None = None
    deals_out: DealsResponse | None = None
    a2a_turns_out: list[A2ANegotiationTurn] = []
    recipe_warn_out: RecipeSanityWarning | None = None
    clarification_options: list[str] = []
    search_products_found: list[ProductOut] = []

    for act in chat_res.actions:
        if isinstance(act, AssistantQuoteAction):
            quote_out = act.quote
        elif isinstance(act, AssistantOrderAction):
            order_out = act.order
        elif isinstance(act, AssistantMenuPlanAction):
            menu_plan_out = act.menu_plan
        elif isinstance(act, AssistantDealsAction):
            deals_out = act.deals
        elif isinstance(act, AssistantA2ANegotiationAction):
            a2a_turns_out = list(act.turns)
            if act.order is not None and order_out is None:
                order_out = act.order
        elif isinstance(act, AssistantRecipeSanityWarningAction):
            recipe_warn_out = act.warning
        elif isinstance(act, AssistantClarificationAction):
            clarification_options = list(act.options)
        elif isinstance(act, AssistantSearchResultsAction):
            search_products_found = list(act.products)

    reply_text = chat_res.reply
    assurance_score = chat_res.assurance_score
    assurance_threshold = chat_res.assurance_threshold
    auto_executed = chat_res.auto_executed

    # Clean catalog -> quote fallback in fake LLM mode when search matched a single clear item+qty
    if (
        settings.llm_provider == "fake"
        and quote_out is None
        and order_out is None
        and not clarification_options
        and search_products_found
        and not assistant.is_inquiry_or_quote_only_message(raw_message)
    ):
        parsed = parse_prep_list(raw_message)
        first_qty = (
            float(parsed.items[0].quantity)
            if parsed.items and parsed.items[0].quantity is not None
            else None
        )
        if first_qty is not None and first_qty > 0:
            top_prod = search_products_found[0]
            diet_str = ",".join(parsed.global_diets) if parsed.global_diets else None
            delivery_date = parsed.requested_delivery_date or default_delivery_date()
            tg_quote_ctx = {
                "conversation_id": chat_res.conversation_id,
                "channel": "telegram",
                "telegram_chat_id": session.telegram_chat_id,
                "branch_id": branch_id_val,
            }
            with_quote = quotes.create_quote(
                db=db,
                aimo_client=aimo_client,
                principal=principal,
                req=QuoteCreateRequest(
                    requested_delivery_date=delivery_date,
                    lines=[QuoteLineInput(product_id=top_prod.id, quantity=first_qty)],
                    diet=diet_str,
                ),
                source_context=tg_quote_ctx,
            )
            if parsed.requested_delivery_date is None and any(
                ln.delivery_status != "NOT_DELIVERABLE"
                and (
                    (ln.available_date and ln.available_date > with_quote.requested_delivery_date)
                    or ln.delivery_status == "LATE"
                    or "LATE" in ln.flags
                )
                for ln in with_quote.lines
            ):
                adv_date = orders.compute_last_available_delivery_date(
                    with_quote.requested_delivery_date,
                    with_quote.lines,
                )
                if adv_date > with_quote.requested_delivery_date:
                    old_q = db.get(Quote, with_quote.id)
                    if old_q is not None and old_q.status == "OPEN":
                        old_q.status = "EXPIRED"
                        db.flush()
                    delivery_date = adv_date
                    with_quote = quotes.create_quote(
                        db=db,
                        aimo_client=aimo_client,
                        principal=principal,
                        req=QuoteCreateRequest(
                            requested_delivery_date=delivery_date,
                            lines=[QuoteLineInput(product_id=top_prod.id, quantity=first_qty)],
                            diet=diet_str,
                        ),
                        source_context=tg_quote_ctx,
                    )
            quote_out = with_quote
            reply_text = (
                f"Prepared quote {with_quote.id} for {first_qty:g} × {top_prod.name} "
                f"(€{with_quote.total_excl_vat_eur:.2f} excl. VAT, delivery {delivery_date})."
            )

    if order_out is not None and order_out.status == "PLACED":
        gw_txt = (
            f", gateway ref {order_out.gateway_reference}"
            if order_out.gateway_reference
            else ""
        )
        if "order confirmation:" not in reply_text.lower():
            reply_text = (
                f"{reply_text} ✅ Order confirmation: {order_out.id} "
                f"(PLACED, delivery {order_out.requested_delivery_date}{gw_txt})."
            ).strip()

    if active_branch is not None and active_branch.short_name not in reply_text:
        reply_text = f"[{active_branch.short_name}] {reply_text}"

    db.commit()
    return TelegramCustomerChatOut(
        status="ok",
        reply_text=reply_text,
        conversation_id=chat_res.conversation_id,
        branches=[branch_to_out(b) for b in branches],
        active_branch=branch_to_out(active_branch) if active_branch else None,
        quote=quote_out,
        order=order_out,
        menu_plan=menu_plan_out,
        deals=deals_out,
        products=search_products_found[:10],
        a2a_turns=a2a_turns_out,
        recipe_sanity_warning=recipe_warn_out,
        clarification_options=clarification_options,
        assurance_score=assurance_score,
        assurance_threshold=assurance_threshold,
        auto_executed=auto_executed,
        llm_mode=chat_res.llm_mode,
        fallback_used=chat_res.fallback_used,
        processing_warnings=list(chat_res.processing_warnings),
    )


def handle_customer_chat(
    db: Session,
    *,
    aimo_client: AimoClient,
    gateway: OrderGateway,
    llm_provider: LLMProvider,
    decision_provider: DecisionProvider,
    req: TelegramCustomerChatRequest,
) -> TelegramCustomerChatOut:
    """Handle inbound private Telegram buyer chat with multi-branch disambiguation."""
    session, user, _ = _load_linked_session_and_user(
        db,
        telegram_chat_id=req.telegram_chat_id,
        telegram_user_id=req.telegram_user_id,
        telegram_username=req.telegram_username,
    )
    branches = list_company_branches(db, user.company_id)
    branches_out = [branch_to_out(b) for b in branches]
    active_branch = next(
        (b for b in branches if b.id == session.active_branch_id),
        None,
    )

    clean_msg = req.message.strip()
    if clean_msg.lower() in ("/branch", "/branches", "switch branch", "switch kitchen"):
        if len(branches) > 1:
            return TelegramCustomerChatOut(
                status="branch_required",
                reply_text=f"Are you making an order for {_format_branch_choice(branches)}?",
                branches=branches_out,
                active_branch=branch_to_out(active_branch) if active_branch else None,
                quote=None,
                order=None,
                clarification_options=[],
            )

    if len(branches) > 1:
        inferred = _match_unambiguous_branch(clean_msg, branches)
        if inferred is not None:
            if active_branch is None or active_branch.id != inferred.id:
                if active_branch is not None and active_branch.id != inferred.id:
                    session.active_conversation_id = None
                active_branch = inferred
                session.active_branch_id = inferred.id
                session.updated_at = clock.now()
                db.flush()
        elif active_branch is None:
            session.pending_message = clean_msg
            session.updated_at = clock.now()
            db.commit()
            return TelegramCustomerChatOut(
                status="branch_required",
                reply_text=f"Are you making an order for {_format_branch_choice(branches)}?",
                branches=branches_out,
                active_branch=None,
                quote=None,
                order=None,
                clarification_options=[],
            )
    elif len(branches) == 1 and active_branch is None:
        active_branch = branches[0]
        session.active_branch_id = active_branch.id
        db.flush()

    return _execute_governed_buyer_turn(
        db,
        aimo_client=aimo_client,
        gateway=gateway,
        llm_provider=llm_provider,
        decision_provider=decision_provider,
        session=session,
        user=user,
        branches=branches,
        active_branch=active_branch,
        raw_message=clean_msg,
    )


def select_customer_branch(
    db: Session,
    *,
    aimo_client: AimoClient,
    gateway: OrderGateway,
    llm_provider: LLMProvider,
    decision_provider: DecisionProvider,
    req: TelegramSelectBranchRequest,
) -> TelegramSelectBranchOut:
    """Select active kitchen branch from Telegram inline button & auto-replay pending_message."""
    session, user, _ = _load_linked_session_and_user(
        db,
        telegram_chat_id=req.telegram_chat_id,
        telegram_user_id=req.telegram_user_id,
    )
    branches = list_company_branches(db, user.company_id)
    branch = next((b for b in branches if b.id == req.branch_id.strip()), None)
    if branch is None:
        raise AppError("NOT_FOUND", 404, "Company branch not found")

    pending = (session.pending_message or "").strip()
    now = clock.now()
    session.active_branch_id = branch.id
    session.active_conversation_id = None
    session.pending_message = None
    session.updated_at = now
    db.flush()

    actor = Principal(
        kind="user",
        id=user.id,
        name=user.name,
        company_id=user.company_id,
        email=user.email,
    )
    record_audit(
        db,
        company_id=user.company_id,
        actor=actor,
        action="telegram.branch_selected",
        entity_type="user",
        entity_id=user.id,
        outcome="ok",
        details={
            "request_id": request_id_var.get() or None,
            "branch_id": branch.id,
            "branch_name": branch.short_name,
            "replayed_pending_message": bool(pending),
        },
    )
    db.commit()

    auto_reply: TelegramCustomerChatOut | None = None
    if pending:
        auto_reply = _execute_governed_buyer_turn(
            db,
            aimo_client=aimo_client,
            gateway=gateway,
            llm_provider=llm_provider,
            decision_provider=decision_provider,
            session=session,
            user=user,
            branches=branches,
            active_branch=branch,
            raw_message=pending,
        )

    return TelegramSelectBranchOut(
        active_branch=branch_to_out(branch),
        branches=[branch_to_out(b) for b in branches],
        auto_reply=auto_reply,
    )


def place_customer_quote(
    db: Session,
    *,
    aimo_client: AimoClient,
    gateway: OrderGateway,
    req: TelegramPlaceQuoteRequest,
    header_idempotency_key: str | None = None,
) -> TelegramPlaceQuoteOut:
    """Place a quote via 1-tap Telegram button under the linked User Principal + branch note."""
    session, user, _ = _load_linked_session_and_user(
        db,
        telegram_chat_id=req.telegram_chat_id,
        telegram_user_id=req.telegram_user_id,
    )
    idem_key = (req.idempotency_key or header_idempotency_key or "").strip()
    if not idem_key:
        raise AppError(
            "VALIDATION_ERROR",
            422,
            "idempotency_key is required to place a Telegram quote.",
            details={"field": "idempotency_key"},
        )

    branches = list_company_branches(db, user.company_id)
    active_branch = next(
        (b for b in branches if b.id == session.active_branch_id),
        None,
    )
    if len(branches) > 1 and active_branch is None:
        raise AppError(
            "VALIDATION_ERROR",
            422,
            "Select a kitchen branch before placing an order.",
            details={"field": "branch_id"},
        )

    actor = Principal(
        kind="user",
        id=user.id,
        name=user.name,
        company_id=user.company_id,
        email=user.email,
    )
    branch_tag = f"[Branch: {active_branch.short_name}] " if active_branch else ""
    extra_note = f" — {req.note.strip()}" if req.note and req.note.strip() else ""
    full_note = f"{branch_tag}Placed via Telegram customer assistant{extra_note}"

    _, order_body = orders.place_order(
        db=db,
        aimo_client=aimo_client,
        principal=actor,
        req=OrderCreateRequest(
            quote_id=req.quote_id.strip(),
            note=full_note,
        ),
        raw_idempotency_key=idem_key,
        gateway=gateway,
        source_context={
            "channel": "telegram",
            "conversation_id": session.active_conversation_id,
            "telegram_chat_id": session.telegram_chat_id,
            "branch_id": active_branch.id if active_branch else None,
            "branch_name": active_branch.short_name if active_branch else None,
        },
    )
    order_out = OrderOut.model_validate(order_body)

    record_audit(
        db,
        company_id=user.company_id,
        actor=actor,
        action="telegram.quote_placed",
        entity_type="order",
        entity_id=order_out.id,
        outcome="ok",
        details={
            "request_id": request_id_var.get() or None,
            "quote_id": req.quote_id.strip(),
            "order_id": order_out.id,
            "status": order_out.status,
            "branch_id": active_branch.id if active_branch else None,
            "branch_name": active_branch.short_name if active_branch else None,
        },
    )
    db.commit()
    return TelegramPlaceQuoteOut(
        order=order_out,
        active_branch=branch_to_out(active_branch) if active_branch else None,
        assurance_score=order_out.assurance_score,
        assurance_threshold=(
            order_out.assurance_threshold
            if order_out.assurance_threshold is not None
            else assistant.get_assistant_settings(db, user.company_id).order_assurance_threshold
        ),
        auto_executed=order_out.auto_executed,
    )


def cancel_customer_order(
    db: Session,
    req: TelegramCancelOrderRequest,
) -> TelegramCancelOrderOut:
    """Cancel a PENDING_APPROVAL or pre-cutoff PLACED order via 1-tap Telegram Undo."""
    session, user, _ = _load_linked_session_and_user(
        db,
        telegram_chat_id=req.telegram_chat_id,
        telegram_user_id=req.telegram_user_id,
    )
    branches = list_company_branches(db, user.company_id)
    active_branch = next(
        (b for b in branches if b.id == session.active_branch_id),
        None,
    )
    actor = Principal(
        kind="user",
        id=user.id,
        name=user.name,
        company_id=user.company_id,
        email=user.email,
    )
    cancel_note = (
        req.note.strip()
        if req.note and req.note.strip()
        else "Cancelled via Telegram 1-tap undo"
    )
    cancelled_out = orders.cancel_order(
        db,
        actor,
        req.order_id.strip(),
        OrderDecisionRequest(note=cancel_note),
    )
    cancelled_orm = db.get(Order, cancelled_out.id)
    if cancelled_orm is not None:
        raw_pol = dict(cancelled_orm.policy or {})
        src_ctx = dict(raw_pol.get("_source_context") or {})
        src_ctx["decided_telegram_chat_id"] = session.telegram_chat_id
        raw_pol["_source_context"] = src_ctx
        cancelled_orm.policy = raw_pol
        db.flush()
    record_audit(
        db,
        company_id=user.company_id,
        actor=actor,
        action="telegram.order_cancelled",
        entity_type="order",
        entity_id=cancelled_out.id,
        outcome="ok",
        details={
            "request_id": request_id_var.get() or None,
            "order_id": cancelled_out.id,
            "status": cancelled_out.status,
            "branch_id": active_branch.id if active_branch else None,
            "branch_name": active_branch.short_name if active_branch else None,
        },
    )
    db.commit()
    return TelegramCancelOrderOut(
        order=cancelled_out,
        active_branch=branch_to_out(active_branch) if active_branch else None,
    )


def ensure_startup_demo_migrations(db: Session) -> None:
    """Idempotently migrate legacy .test demo emails and backfill Hilton branches on startup."""
    now = clock.now()
    email_migrations: tuple[tuple[str, str], ...] = (
        ("demo@aimoannos.test", "anna.korhonen@hotel.fi"),
        ("mikko@bistrosatama.test", "mikko.laine@restaurant.fi"),
        ("sari@nordiccampus.test", "sari.virtanen@festival.fi"),
    )
    dirty = False
    for old_email, new_email in email_migrations:
        old_user = db.scalar(select(User).where(User.email == old_email))
        if old_user is not None:
            existing_new = db.scalar(select(User).where(User.email == new_email))
            if existing_new is None:
                old_user.email = new_email
                dirty = True

    aurora = db.scalar(select(Company).where(Company.business_id == "1234567-8"))
    if aurora is not None:
        existing_branches = list_company_branches(db, aurora.id)
        if not existing_branches:
            branch_specs: tuple[tuple[str, str, str, str], ...] = (
                (
                    "brn_strand",
                    "Hilton Helsinki Strand",
                    "Hilton Strand",
                    "John Stenbergin ranta 4, 00530 Helsinki",
                ),
                (
                    "brn_kalastaja",
                    "Hilton Helsinki Kalastajantorppa",
                    "Hilton Kalastajantorppa",
                    "Kalastajatorpantie 1, 00330 Helsinki",
                ),
            )
            for idx, (b_id, b_name, b_short, b_addr) in enumerate(branch_specs):
                if db.get(CompanyBranch, b_id) is None:
                    db.add(
                        CompanyBranch(
                            id=b_id,
                            company_id=aurora.id,
                            name=b_name,
                            short_name=b_short,
                            address=b_addr,
                            active=True,
                            created_at=now + timedelta(seconds=idx),
                        )
                    )
                    dirty = True

    if dirty:
        db.commit()


def unlink_customer_telegram(
    db: Session,
    req: TelegramUnlinkRequest,
) -> TelegramUnlinkOut:
    """Bidirectional `/unlink` sync: disable UserTelegramSetting + delete customer session."""
    clean_chat_id = _validate_private_numeric_id(req.telegram_chat_id, "telegram_chat_id")
    clean_user_id = _validate_private_numeric_id(req.telegram_user_id, "telegram_user_id")
    now = clock.now()

    session = db.scalar(
        select(TelegramCustomerSession).where(
            TelegramCustomerSession.telegram_chat_id == clean_chat_id
        )
    )
    if session is not None and session.telegram_user_id != clean_user_id:
        raise AppError("FORBIDDEN", 403, "Telegram user does not match linked session")

    unlinked = False
    target_user_id: str | None = None

    if session is not None:
        target_user_id = session.user_id
        db.delete(session)
        unlinked = True

    matched_opts = list(
        db.scalars(
            select(UserTelegramSetting).where(
                UserTelegramSetting.telegram_chat_id == clean_chat_id
            )
        ).all()
    )
    if target_user_id is not None and not any(o.user_id == target_user_id for o in matched_opts):
        opt = db.get(UserTelegramSetting, target_user_id)
        if opt is not None:
            matched_opts.append(opt)

    for opt in matched_opts:
        if opt.enabled or opt.telegram_chat_id is not None:
            opt.enabled = False
            opt.telegram_chat_id = None
            opt.updated_at = now
            unlinked = True
            if target_user_id is None:
                target_user_id = opt.user_id

    if target_user_id is not None:
        open_tokens = db.scalars(
            select(TelegramLinkToken).where(
                TelegramLinkToken.user_id == target_user_id,
                TelegramLinkToken.consumed_at.is_(None),
            )
        ).all()
        for tok in open_tokens:
            tok.consumed_at = now

        user = db.get(User, target_user_id)
        if user is not None:
            record_audit(
                db,
                company_id=user.company_id,
                actor=Principal(
                    kind="user",
                    id=user.id,
                    name=user.name,
                    company_id=user.company_id,
                    email=user.email,
                ),
                action="telegram.unlinked",
                entity_type="user",
                entity_id=user.id,
                outcome="ok",
                details={
                    "request_id": request_id_var.get() or None,
                    "telegram_chat_id": clean_chat_id,
                    "source": "telegram_bridge_unlink",
                },
            )

    db.commit()
    return TelegramUnlinkOut(unlinked=unlinked, user_id=target_user_id)


def decide_customer_order(
    db: Session,
    *,
    aimo_client: AimoClient,
    gateway: OrderGateway,
    req: TelegramDecideOrderRequest,
    header_idempotency_key: str | None = None,
) -> TelegramDecideOrderOut:
    """Approve or reject a company PENDING_APPROVAL order from a connected human Telegram chat."""
    session, user, _ = _load_linked_session_and_user(
        db,
        telegram_chat_id=req.telegram_chat_id,
        telegram_user_id=req.telegram_user_id,
    )
    branches = list_company_branches(db, user.company_id)
    active_branch = next(
        (b for b in branches if b.id == session.active_branch_id),
        None,
    )
    actor = Principal(
        kind="user",
        id=user.id,
        name=user.name,
        company_id=user.company_id,
        email=user.email,
    )
    clean_order_id = req.order_id.strip()
    decision_note = (
        req.note.strip()
        if req.note and req.note.strip()
        else f"{req.decision.capitalize()}d via Telegram"
    )

    if req.decision == "approve":
        raw_hash = (req.content_hash or "").strip().lower()
        if len(raw_hash) < 12:
            raise AppError(
                "VALIDATION_ERROR",
                422,
                "content_hash (full SHA-256 or >=12-char prefix) is required to approve.",
                details={"field": "content_hash"},
            )
        target_order = orders.get_order(db, actor, clean_order_id)
        if not target_order.content_hash.lower().startswith(raw_hash):
            raise AppError(
                "APPROVAL_CONTENT_CHANGED",
                409,
                "Order content hash does not match current order state",
                details={"expected_content_hash": target_order.content_hash},
            )
        idem_key = (
            (req.idempotency_key or header_idempotency_key or "").strip()
            or f"tg_apr_{session.telegram_chat_id}_{clean_order_id}_{raw_hash[:16]}"
        )
        _, approved_body = orders.approve_order(
            db=db,
            aimo_client=aimo_client,
            principal=actor,
            order_id=clean_order_id,
            req=OrderApproveRequest(
                content_hash=target_order.content_hash,
                note=decision_note,
            ),
            raw_idempotency_key=idem_key,
            gateway=gateway,
        )
        decided_out = OrderOut.model_validate(approved_body)
    else:
        decided_out = orders.reject_order(
            db=db,
            principal=actor,
            order_id=clean_order_id,
            req=OrderDecisionRequest(note=decision_note),
        )

    orm_order = db.get(Order, decided_out.id)
    if orm_order is not None:
        raw_pol = dict(orm_order.policy or {})
        src_ctx = dict(raw_pol.get("_source_context") or {})
        src_ctx["decided_telegram_chat_id"] = session.telegram_chat_id
        raw_pol["_source_context"] = src_ctx
        orm_order.policy = raw_pol
        db.flush()

    record_audit(
        db,
        company_id=user.company_id,
        actor=actor,
        action="telegram.order_decided",
        entity_type="order",
        entity_id=decided_out.id,
        outcome="ok",
        details={
            "request_id": request_id_var.get() or None,
            "order_id": decided_out.id,
            "decision": req.decision,
            "status": decided_out.status,
            "telegram_chat_id": session.telegram_chat_id,
        },
    )
    db.commit()
    return TelegramDecideOrderOut(
        order=decided_out,
        decision=req.decision,
        active_branch=branch_to_out(active_branch) if active_branch else None,
    )


def list_connected_sessions(db: Session) -> TelegramConnectedSessionsResponse:
    """List all active private Telegram user connections across all tenant companies."""
    opt_rows = db.scalars(
        select(UserTelegramSetting)
        .where(
            UserTelegramSetting.enabled.is_(True),
            UserTelegramSetting.telegram_chat_id.is_not(None),
        )
        .order_by(UserTelegramSetting.updated_at.desc(), UserTelegramSetting.user_id.asc())
    ).all()

    items: list[TelegramConnectedSessionOut] = []
    seen_chats: set[str] = set()

    for opt in opt_rows:
        chat_id = (opt.telegram_chat_id or "").strip()
        if not _POSITIVE_INT_RE.match(chat_id) or chat_id in seen_chats:
            continue
        user = db.get(User, opt.user_id)
        if user is None:
            continue
        company = db.get(Company, user.company_id)
        company_name = company.name if company is not None else user.company_id
        sess = db.scalar(
            select(TelegramCustomerSession).where(
                TelegramCustomerSession.user_id == user.id
            )
        )
        branches = list_company_branches(db, user.company_id)
        active_branch = (
            next((b for b in branches if b.id == sess.active_branch_id), None)
            if sess is not None
            else (branches[0] if len(branches) == 1 else None)
        )
        seen_chats.add(chat_id)
        items.append(
            TelegramConnectedSessionOut(
                user_id=user.id,
                user_name=user.name,
                user_email=user.email,
                company_id=user.company_id,
                company_name=company_name,
                telegram_chat_id=chat_id,
                telegram_user_id=sess.telegram_user_id if sess is not None else chat_id,
                telegram_username=(
                    opt.telegram_username
                    or (sess.telegram_username if sess is not None else None)
                ),
                linked_via_qr=sess is not None,
                active_branch=branch_to_out(active_branch) if active_branch else None,
                branches=[branch_to_out(b) for b in branches],
            )
        )

    return TelegramConnectedSessionsResponse(items=items)


def _is_order_undoable_for_human(order: Order, now_hel: datetime) -> bool:
    if order.status == "PENDING_APPROVAL":
        return True
    if order.status != "PLACED":
        return False
    raw_sched = (order.policy or {}).get("_delivery_schedule")
    resched_set = (
        set(raw_sched.get("rescheduled_cutoff_product_ids") or [])
        if isinstance(raw_sched, dict)
        else set()
    )
    has_closing = False
    for ln in order.lines:
        if ln.product_id in resched_set or not ln.closing_time:
            continue
        try:
            closing_dt = clock.to_helsinki(datetime.fromisoformat(ln.closing_time))
            has_closing = True
        except ValueError:
            continue
        if now_hel >= closing_dt:
            return False
    if not has_closing and order.requested_delivery_date < now_hel.date().isoformat():
        return False
    return True


def _infer_origin_channel(order: Order) -> str:
    src = (order.policy or {}).get("_source_context")
    if isinstance(src, dict):
        explicit = str(src.get("channel") or "").strip().lower()
        if explicit:
            return explicit
        if src.get("telegram_chat_id"):
            return "telegram"
        if src.get("a2a_task_id"):
            return "a2a"
        if src.get("conversation_id"):
            return "web"
    return "web" if order.created_by_kind == "user" else "api"


def list_order_notifications(
    db: Session,
    *,
    since: str | None = None,
    include_same_chat_origin: bool = False,
    limit: int = 50,
) -> TelegramOrderNotificationsResponse:
    """Return order confirmations & approval notifications for all connected Telegram users."""
    orders.expire_pending_orders(db)
    now = clock.now()
    now_hel = clock.to_helsinki(now)

    since_dt: datetime | None = None
    if since and since.strip():
        clean_since = re.sub(r"\s+(\d{2}:\d{2})$", r"+\1", since.strip())
        try:
            since_dt = clock.to_helsinki(datetime.fromisoformat(clean_since))
        except ValueError as exc:
            raise AppError(
                "VALIDATION_ERROR",
                422,
                "since must be a valid ISO-8601 timestamp",
                details={"field": "since"},
            ) from exc

    connected = list_connected_sessions(db).items
    if not connected:
        return TelegramOrderNotificationsResponse(items=[], server_time=now)

    connected_by_company: dict[str, list[TelegramConnectedSessionOut]] = {}
    for sess_out in connected:
        connected_by_company.setdefault(sess_out.company_id, []).append(sess_out)

    stmt = (
        select(Order)
        .options(selectinload(Order.lines), selectinload(Order.events))
        .where(
            Order.company_id.in_(list(connected_by_company.keys())),
            Order.status.in_(("PLACED", "PENDING_APPROVAL", "CANCELLED", "REJECTED")),
        )
        .order_by(Order.updated_at.asc(), Order.id.asc())
    )
    candidates = db.scalars(stmt).all()

    status_to_event: dict[str, TelegramOrderNotificationEventType] = {
        "PLACED": "ORDER_CONFIRMED",
        "PENDING_APPROVAL": "ORDER_PENDING_APPROVAL",
        "CANCELLED": "ORDER_CANCELLED",
        "REJECTED": "ORDER_REJECTED",
    }

    notifications: list[TelegramOrderNotificationOut] = []
    for order in candidates:
        ev_at = clock.to_helsinki(order.updated_at or order.created_at)
        if since_dt is not None and ev_at < since_dt:
            continue
        event_type = status_to_event.get(order.status)
        if event_type is None:
            continue

        order_out = orders.order_to_out(order)
        src_ctx = (order.policy or {}).get("_source_context")
        src_dict = src_ctx if isinstance(src_ctx, dict) else {}
        src_chat_id = str(src_dict.get("telegram_chat_id") or "").strip()
        decided_chat_id = str(src_dict.get("decided_telegram_chat_id") or "").strip()
        origin_channel = _infer_origin_channel(order)
        can_undo = _is_order_undoable_for_human(order, now_hel)

        for target in connected_by_company.get(order.company_id, []):
            chat_id = target.telegram_chat_id
            if order.status == "PENDING_APPROVAL":
                same_chat = bool(src_chat_id and src_chat_id == chat_id)
            elif order.status == "PLACED":
                if order.decided_by_id is not None:
                    same_chat = bool(decided_chat_id and decided_chat_id == chat_id)
                else:
                    same_chat = bool(src_chat_id and src_chat_id == chat_id)
            else:
                same_chat = bool(decided_chat_id and decided_chat_id == chat_id)

            if same_chat and not include_same_chat_origin:
                continue

            gw_part = (
                f", ref {order_out.gateway_reference}"
                if order_out.gateway_reference
                else ""
            )
            if event_type == "ORDER_CONFIRMED":
                summary_text = (
                    f"✅ Order confirmed for {target.company_name}: {order_out.id} "
                    f"(€{order_out.total_excl_vat_eur:.2f} excl. VAT, "
                    f"{len(order_out.lines)} line(s), delivery {order_out.requested_delivery_date}"
                    f"{gw_part})."
                )
            elif event_type == "ORDER_PENDING_APPROVAL":
                summary_text = (
                    f"⏳ Approval needed for {target.company_name}: {order_out.id} "
                    f"(€{order_out.total_excl_vat_eur:.2f} excl. VAT, "
                    f"{len(order_out.lines)} line(s), "
                    f"delivery {order_out.requested_delivery_date})."
                )
            elif event_type == "ORDER_CANCELLED":
                summary_text = (
                    f"↩️ Order cancelled for {target.company_name}: {order_out.id} "
                    f"(€{order_out.total_excl_vat_eur:.2f} excl. VAT)."
                )
            else:
                summary_text = (
                    f"❌ Order rejected for {target.company_name}: {order_out.id} "
                    f"(€{order_out.total_excl_vat_eur:.2f} excl. VAT)."
                )

            notifications.append(
                TelegramOrderNotificationOut(
                    notification_id=f"ord_notif:{chat_id}:{order_out.id}:{event_type}",
                    event_type=event_type,
                    event_at=ev_at,
                    telegram_chat_id=chat_id,
                    telegram_user_id=target.telegram_user_id,
                    user_id=target.user_id,
                    user_name=target.user_name,
                    company_id=target.company_id,
                    company_name=target.company_name,
                    active_branch=target.active_branch,
                    origin_channel=origin_channel,
                    placed_in_same_telegram_chat=same_chat,
                    can_undo_in_telegram=can_undo,
                    summary_text=summary_text,
                    order=order_out,
                )
            )

    bounded = notifications[-max(1, min(limit, 200)) :]
    return TelegramOrderNotificationsResponse(items=bounded, server_time=now)

