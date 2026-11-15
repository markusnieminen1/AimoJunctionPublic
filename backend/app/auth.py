"""Authentication, JWT tokens, Argon2 password/secret hashing, and principal dependencies."""

from datetime import datetime, time, timedelta
from typing import Annotated, Any, Literal, cast

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import clock
from app.config import settings
from app.db import get_db
from app.errors import AppError
from app.models import Agent, Order, User
from app.schemas import AgentOut, Principal, PrincipalRef

JWT_AUDIENCE = "aimoannos-api"
USER_TOKEN_TTL = timedelta(hours=12)
AGENT_TOKEN_TTL = timedelta(minutes=60)
_SPEND_STATUSES = ("PLACED", "SUBMITTING", "SUBMISSION_UNKNOWN", "PENDING_APPROVAL")

_password_hash = PasswordHash.recommended()
_bearer_scheme = HTTPBearer(auto_error=False)


def hash_secret(plain: str) -> str:
    """Hash a password or agent secret with Argon2."""
    return _password_hash.hash(plain)


def verify_secret(plain: str, hashed: str) -> bool:
    """Verify a plaintext password or agent secret against a stored Argon2 hash."""
    try:
        return _password_hash.verify(plain, hashed)
    except (UnknownHashError, ValueError):
        return False


def is_agent_active(agent: Agent, *, at: datetime | None = None) -> bool:
    """Return True if the agent is active and not past its expires_at timestamp."""
    if agent.status != "active":
        return False
    if agent.expires_at is None:
        return True
    now = at or clock.now()
    return clock.to_helsinki(agent.expires_at) > now


def create_user_token(user: User) -> str:
    """Issue a 12-hour HS256 JWT for a human user."""
    now = clock.now()
    exp = now + USER_TOKEN_TTL
    payload = {
        "sub": user.id,
        "kind": "user",
        "company_id": user.company_id,
        "aud": JWT_AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def create_agent_token(agent: Agent) -> tuple[str, datetime]:
    """Issue a 60-minute HS256 JWT for an active agent and return (token, expires_at)."""
    now = clock.now()
    exp = now + AGENT_TOKEN_TTL
    payload = {
        "sub": agent.id,
        "kind": "agent",
        "company_id": agent.company_id,
        "delegated_by": agent.delegated_by_user_id,
        "aud": JWT_AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    return token, exp


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate an HS256 JWT against settings.jwt_secret, aud, and clock.now()."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            audience=JWT_AUDIENCE,
            options={"verify_exp": False, "verify_iat": False},
        )
    except jwt.PyJWTError as exc:
        raise AppError("UNAUTHENTICATED", 401, "Invalid authentication token") from exc

    exp = payload.get("exp")
    if not isinstance(exp, (int, float)) or isinstance(exp, bool):
        raise AppError("UNAUTHENTICATED", 401, "Invalid authentication token")
    if float(exp) <= clock.now().timestamp():
        raise AppError("UNAUTHENTICATED", 401, "Authentication token has expired")

    sub = payload.get("sub")
    kind = payload.get("kind")
    company_id = payload.get("company_id")
    if not isinstance(sub, str) or not sub or not isinstance(company_id, str) or not company_id:
        raise AppError("UNAUTHENTICATED", 401, "Invalid authentication token")
    if kind not in ("user", "agent"):
        raise AppError("UNAUTHENTICATED", 401, "Invalid authentication token")

    return payload


def build_agent_principal(agent: Agent, delegator: User | None) -> Principal:
    """Build a Principal object for an agent and its delegating user."""
    delegated_by = (
        PrincipalRef(id=delegator.id, name=delegator.name)
        if delegator is not None
        else PrincipalRef(id=agent.delegated_by_user_id, name="Unknown user")
    )
    return Principal(
        kind="agent",
        id=agent.id,
        name=agent.name,
        company_id=agent.company_id,
        delegated_by=delegated_by,
    )


def compute_agent_spent_today(db: Session, agent_id: str, company_id: str) -> float:
    """Sum today's (Europe/Helsinki) committed/pending order totals for an agent."""
    today = clock.now().date()
    start_of_day = datetime.combine(today, time.min, tzinfo=clock.HELSINKI_TZ)
    end_of_day = start_of_day + timedelta(days=1)
    total = db.scalar(
        select(func.coalesce(func.sum(Order.total_excl_vat_eur), 0.0)).where(
            Order.company_id == company_id,
            Order.created_by_kind == "agent",
            Order.created_by_id == agent_id,
            Order.status.in_(_SPEND_STATUSES),
            Order.created_at >= start_of_day,
            Order.created_at < end_of_day,
        )
    )
    return round(float(total or 0.0), 2)


def agent_to_out(
    db: Session,
    agent: Agent,
    *,
    delegator: User | None = None,
    spent_today_eur: float | None = None,
) -> AgentOut:
    """Serialize an Agent ORM row to AgentOut with delegated_by and spent_today_eur."""
    resolved_delegator = (
        delegator if delegator is not None else db.get(User, agent.delegated_by_user_id)
    )
    delegated_by = (
        PrincipalRef(id=resolved_delegator.id, name=resolved_delegator.name)
        if resolved_delegator is not None
        else PrincipalRef(id=agent.delegated_by_user_id, name="Unknown user")
    )
    spent = (
        spent_today_eur
        if spent_today_eur is not None
        else compute_agent_spent_today(db, agent.id, agent.company_id)
    )
    status = cast(
        Literal["active", "disabled", "quarantined"],
        agent.status if agent.status in ("active", "disabled", "quarantined") else "disabled",
    )
    return AgentOut(
        id=agent.id,
        name=agent.name,
        description=agent.description,
        status=status,
        auto_approve_limit_eur=agent.auto_approve_limit_eur,
        max_order_value_eur=agent.max_order_value_eur,
        daily_spend_limit_eur=agent.daily_spend_limit_eur,
        allowed_categories=list(agent.allowed_categories or []),
        max_quantity_per_line=agent.max_quantity_per_line,
        can_place_orders=agent.can_place_orders,
        delegated_by=delegated_by,
        expires_at=agent.expires_at,
        created_at=agent.created_at,
        last_used_at=agent.last_used_at,
        spent_today_eur=spent,
    )


def get_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> Principal:
    """Resolve and validate the caller's Bearer token on every request."""
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise AppError("UNAUTHENTICATED", 401, "Missing or invalid Bearer token")

    payload = decode_access_token(credentials.credentials)
    kind = payload["kind"]
    sub = payload["sub"]
    company_id = payload["company_id"]

    if kind == "user":
        user = db.get(User, sub)
        if user is None or user.company_id != company_id:
            raise AppError("UNAUTHENTICATED", 401, "Authenticated user not found")
        return Principal(
            kind="user",
            id=user.id,
            name=user.name,
            company_id=user.company_id,
            delegated_by=None,
        )

    # Agent tokens must reload the Agent row on every request for immediate kill-switch.
    agent = db.get(Agent, sub)
    if agent is None or agent.company_id != company_id:
        raise AppError("UNAUTHENTICATED", 401, "Authenticated agent not found")

    now = clock.now()
    if not is_agent_active(agent, at=now):
        raise AppError("AGENT_DISABLED", 403, "Agent is disabled or expired")

    agent.last_used_at = now
    db.commit()

    delegator = db.get(User, agent.delegated_by_user_id)
    return build_agent_principal(agent, delegator)


def require_principal(
    principal: Annotated[Principal, Depends(get_principal)],
) -> Principal:
    """Require any authenticated principal (user or active agent)."""
    return principal


def require_user(
    principal: Annotated[Principal, Depends(get_principal)],
) -> Principal:
    """Require an authenticated human user; agents receive 403 FORBIDDEN."""
    if principal.kind != "user":
        raise AppError("FORBIDDEN", 403, "User authentication required")
    return principal


DbSession = Annotated[Session, Depends(get_db)]
CurrentPrincipal = Annotated[Principal, Depends(require_principal)]
CurrentUser = Annotated[Principal, Depends(require_user)]
