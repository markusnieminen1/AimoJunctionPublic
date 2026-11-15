"""Authentication endpoints for human users and delegated agents."""

from fastapi import APIRouter, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app import clock
from app.audit import record_audit
from app.auth import (
    CurrentPrincipal,
    DbSession,
    agent_to_out,
    build_agent_principal,
    create_agent_token,
    create_user_token,
    hash_secret,
    is_agent_active,
    verify_secret,
)
from app.config import settings
from app.errors import AppError
from app.ids import new_id
from app.models import Agent, Company, User
from app.schemas import (
    AgentAuthTokenRequest,
    AgentAuthTokenResponse,
    CompanyOut,
    DemoPersona,
    DemoPersonasResponse,
    LoginRequest,
    LoginResponse,
    MeResponse,
    Principal,
    RegisterRequest,
    RegisterResponse,
)

router = APIRouter(tags=["auth"])


@router.post(
    "/auth/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
)
def register(payload: RegisterRequest, db: DbSession) -> RegisterResponse:
    """Register a new company and its initial human user."""
    existing = db.scalar(select(User).where(User.email == payload.email))
    if existing is not None:
        raise AppError("EMAIL_TAKEN", 409, "An account with this email already exists")

    now = clock.now()
    company = Company(
        id=new_id("cmp_"),
        name=payload.company_name,
        business_id=payload.business_id,
        created_at=now,
    )
    user = User(
        id=new_id("usr_"),
        company_id=company.id,
        name=payload.user_name,
        email=payload.email,
        password_hash=hash_secret(payload.password),
        created_at=now,
    )
    db.add(company)
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise AppError("EMAIL_TAKEN", 409, "An account with this email already exists") from exc

    user_principal = Principal(
        kind="user",
        id=user.id,
        name=user.name,
        company_id=company.id,
        delegated_by=None,
    )
    record_audit(
        db,
        company_id=company.id,
        actor=user_principal,
        action="user.registered",
        entity_type="user",
        entity_id=user.id,
        outcome="ok",
        details={"email": user.email, "company_id": company.id, "company_name": company.name},
    )
    db.commit()

    return RegisterResponse(
        token=create_user_token(user),
        user=user_principal,
        company=CompanyOut(id=company.id, name=company.name),
    )


LEGACY_DEMO_EMAIL_ALIASES: dict[str, str] = {
    "demo@aimoannos.test": "anna.korhonen@hotel.fi",
    "mikko@bistrosatama.test": "mikko.laine@restaurant.fi",
    "sari@nordiccampus.test": "sari.virtanen@festival.fi",
}


@router.post("/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: DbSession) -> LoginResponse:
    """Authenticate a human user with email and password (supporting legacy .test aliases)."""
    normalized_input = payload.email.strip().lower()
    mapped_email = LEGACY_DEMO_EMAIL_ALIASES.get(normalized_input, payload.email)
    user = db.scalar(select(User).where(User.email == mapped_email))
    if user is None and mapped_email != payload.email:
        user = db.scalar(select(User).where(User.email == payload.email))
    if user is None or not verify_secret(payload.password, user.password_hash):
        raise AppError("UNAUTHENTICATED", 401, "Invalid email or password")

    user_principal = Principal(
        kind="user",
        id=user.id,
        name=user.name,
        company_id=user.company_id,
        delegated_by=None,
    )
    return LoginResponse(token=create_user_token(user), user=user_principal)


@router.get("/auth/demo-personas", response_model=DemoPersonasResponse)
def list_demo_personas(db: DbSession) -> DemoPersonasResponse:
    """Return seeded F&B dummy personas in dev mode (404 outside dev; zero passwords leaked)."""
    if settings.app_env != "dev":
        raise AppError("NOT_FOUND", 404, "Endpoint not available outside dev environment")

    defaults = [
        DemoPersona(
            email="anna.korhonen@hotel.fi",
            name="Anna Korhonen",
            company_name="Hotel Aurora Oy",
            group="demo",
            role_label="Hotel F&B Buyer",
        ),
        DemoPersona(
            email="mikko.laine@restaurant.fi",
            name="Mikko Laine",
            company_name="Bistro Satama Oy",
            group="demo",
            role_label="Head Chef",
        ),
        DemoPersona(
            email="sari.virtanen@festival.fi",
            name="Sari Virtanen",
            company_name="Nordic Campus Catering Oy",
            group="demo",
            role_label="Event Producer",
        ),
    ]
    canonical_emails = tuple(d.email for d in defaults)
    rows = db.execute(
        select(User, Company)
        .join(Company, User.company_id == Company.id)
        .where(User.email.in_(canonical_emails))
    ).all()
    db_by_email: dict[str, tuple[str, str]] = {
        u.email: (u.name, c.name) for u, c in rows
    }

    items: list[DemoPersona] = []
    for d in defaults:
        hydrated = db_by_email.get(d.email)
        if hydrated is not None:
            items.append(
                DemoPersona(
                    email=d.email,
                    name=hydrated[0],
                    company_name=hydrated[1],
                    group=d.group,
                    role_label=d.role_label,
                )
            )
        else:
            items.append(d)
    return DemoPersonasResponse(items=items)


@router.get("/me", response_model=MeResponse)
def get_me(principal: CurrentPrincipal, db: DbSession) -> MeResponse:
    """Return the authenticated caller's principal, company, and agent profile if applicable."""
    company = db.get(Company, principal.company_id)
    if company is None:
        raise AppError("UNAUTHENTICATED", 401, "Company not found for principal")

    agent_out = None
    if principal.kind == "agent":
        agent = db.get(Agent, principal.id)
        if agent is None:
            raise AppError("UNAUTHENTICATED", 401, "Authenticated agent not found")
        agent_out = agent_to_out(db, agent)

    return MeResponse(
        principal=principal,
        company=CompanyOut(id=company.id, name=company.name),
        agent=agent_out,
    )


@router.post("/agent-auth/token", response_model=AgentAuthTokenResponse)
def exchange_agent_token(
    payload: AgentAuthTokenRequest,
    db: DbSession,
) -> AgentAuthTokenResponse:
    """Exchange agent_id and agent_secret for a short-lived 60-minute agent JWT."""
    agent = db.get(Agent, payload.agent_id)
    if agent is None or not verify_secret(payload.agent_secret, agent.secret_hash):
        raise AppError("UNAUTHENTICATED", 401, "Invalid agent credentials")

    now = clock.now()
    if not is_agent_active(agent, at=now):
        raise AppError("AGENT_DISABLED", 403, "Agent is disabled or expired")

    agent.last_used_at = now
    delegator = db.get(User, agent.delegated_by_user_id)
    agent_principal = build_agent_principal(agent, delegator)

    record_audit(
        db,
        company_id=agent.company_id,
        actor=agent_principal,
        action="agent.token_issued",
        entity_type="agent",
        entity_id=agent.id,
        outcome="ok",
        details={"delegated_by_user_id": agent.delegated_by_user_id},
    )
    db.commit()

    token, token_expires_at = create_agent_token(agent)
    return AgentAuthTokenResponse(
        token=token,
        expires_at=token_expires_at,
        agent=agent_to_out(db, agent, delegator=delegator),
    )
