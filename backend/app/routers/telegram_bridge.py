"""Local Telegram Bridge endpoints guarded strictly by `X-Telegram-Bridge-Secret` HMAC-SHA256."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query

from app.aimo.client import AimoClient, get_aimo_client
from app.auth import DbSession
from app.llm.decisions import DecisionProvider, get_decision_provider
from app.llm.provider import LLMProvider, get_llm_provider
from app.schemas import (
    TelegramCancelOrderOut,
    TelegramCancelOrderRequest,
    TelegramConnectedSessionsResponse,
    TelegramCustomerChatOut,
    TelegramCustomerChatRequest,
    TelegramDecideOrderOut,
    TelegramDecideOrderRequest,
    TelegramLinkClaimOut,
    TelegramLinkClaimRequest,
    TelegramOrderNotificationsResponse,
    TelegramPlaceQuoteOut,
    TelegramPlaceQuoteRequest,
    TelegramSelectBranchOut,
    TelegramSelectBranchRequest,
    TelegramUnlinkOut,
    TelegramUnlinkRequest,
)
from app.services.order_gateway import OrderGateway, get_order_gateway
from app.services.telegram_customer import (
    cancel_customer_order,
    claim_link_token,
    decide_customer_order,
    handle_customer_chat,
    list_connected_sessions,
    list_order_notifications,
    place_customer_quote,
    select_customer_branch,
    unlink_customer_telegram,
    verify_bridge_secret,
)

router = APIRouter(prefix="/telegram", tags=["telegram"])


def _require_bridge_secret(
    x_telegram_bridge_secret: Annotated[
        str | None, Header(alias="X-Telegram-Bridge-Secret")
    ] = None,
) -> None:
    verify_bridge_secret(x_telegram_bridge_secret)


BridgeAuth = Annotated[None, Depends(_require_bridge_secret)]


@router.post("/link-claim", response_model=TelegramLinkClaimOut)
def post_telegram_link_claim(
    payload: TelegramLinkClaimRequest,
    _auth: BridgeAuth,
    db: DbSession,
) -> TelegramLinkClaimOut:
    """Consume a one-time `/start link_<tok>` pairing token and bind private Telegram chat."""
    return claim_link_token(db, payload)


@router.post("/select-branch", response_model=TelegramSelectBranchOut)
def post_telegram_select_branch(
    payload: TelegramSelectBranchRequest,
    _auth: BridgeAuth,
    db: DbSession,
    aimo_client: Annotated[AimoClient, Depends(get_aimo_client)],
    gateway: Annotated[OrderGateway, Depends(get_order_gateway)],
    llm_provider: Annotated[LLMProvider, Depends(get_llm_provider)],
    decision_provider: Annotated[DecisionProvider, Depends(get_decision_provider)],
) -> TelegramSelectBranchOut:
    """Select active kitchen branch for a linked Telegram session and auto-replay pending turn."""
    return select_customer_branch(
        db,
        aimo_client=aimo_client,
        gateway=gateway,
        llm_provider=llm_provider,
        decision_provider=decision_provider,
        req=payload,
    )


@router.post("/customer-chat", response_model=TelegramCustomerChatOut)
def post_telegram_customer_chat(
    payload: TelegramCustomerChatRequest,
    _auth: BridgeAuth,
    db: DbSession,
    aimo_client: Annotated[AimoClient, Depends(get_aimo_client)],
    gateway: Annotated[OrderGateway, Depends(get_order_gateway)],
    llm_provider: Annotated[LLMProvider, Depends(get_llm_provider)],
    decision_provider: Annotated[DecisionProvider, Depends(get_decision_provider)],
) -> TelegramCustomerChatOut:
    """Run governed buyer assistant chat (or branch disambiguation) for a linked Telegram user."""
    return handle_customer_chat(
        db,
        aimo_client=aimo_client,
        gateway=gateway,
        llm_provider=llm_provider,
        decision_provider=decision_provider,
        req=payload,
    )


@router.post("/place-quote", response_model=TelegramPlaceQuoteOut, status_code=201)
def post_telegram_place_quote(
    payload: TelegramPlaceQuoteRequest,
    _auth: BridgeAuth,
    db: DbSession,
    aimo_client: Annotated[AimoClient, Depends(get_aimo_client)],
    gateway: Annotated[OrderGateway, Depends(get_order_gateway)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TelegramPlaceQuoteOut:
    """Place a quote via 1-tap Telegram button under the linked User Principal."""
    return place_customer_quote(
        db,
        aimo_client=aimo_client,
        gateway=gateway,
        req=payload,
        header_idempotency_key=idempotency_key,
    )


@router.post("/unlink", response_model=TelegramUnlinkOut)
def post_telegram_unlink(
    payload: TelegramUnlinkRequest,
    _auth: BridgeAuth,
    db: DbSession,
) -> TelegramUnlinkOut:
    """Disconnect a linked Telegram chat from `/unlink` and sync web opt-in state."""
    return unlink_customer_telegram(db, payload)


@router.post("/cancel-order", response_model=TelegramCancelOrderOut)
def post_telegram_cancel_order(
    payload: TelegramCancelOrderRequest,
    _auth: BridgeAuth,
    db: DbSession,
) -> TelegramCancelOrderOut:
    """Cancel a PENDING_APPROVAL or pre-cutoff PLACED order via 1-tap Telegram Undo."""
    return cancel_customer_order(db, payload)


@router.post("/decide-order", response_model=TelegramDecideOrderOut)
def post_telegram_decide_order(
    payload: TelegramDecideOrderRequest,
    _auth: BridgeAuth,
    db: DbSession,
    aimo_client: Annotated[AimoClient, Depends(get_aimo_client)],
    gateway: Annotated[OrderGateway, Depends(get_order_gateway)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TelegramDecideOrderOut:
    """Approve or reject a company PENDING_APPROVAL order from a connected human Telegram chat."""
    return decide_customer_order(
        db,
        aimo_client=aimo_client,
        gateway=gateway,
        req=payload,
        header_idempotency_key=idempotency_key,
    )


@router.get("/connected-sessions", response_model=TelegramConnectedSessionsResponse)
def get_telegram_connected_sessions(
    _auth: BridgeAuth,
    db: DbSession,
) -> TelegramConnectedSessionsResponse:
    """List all active connected private Telegram user sessions across all tenant companies."""
    return list_connected_sessions(db)


@router.get("/order-notifications", response_model=TelegramOrderNotificationsResponse)
def get_telegram_order_notifications(
    _auth: BridgeAuth,
    db: DbSession,
    since: str | None = None,
    include_same_chat_origin: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> TelegramOrderNotificationsResponse:
    """Fetch order confirmations & pending-approval alerts for connected Telegram users."""
    return list_order_notifications(
        db,
        since=since,
        include_same_chat_origin=include_same_chat_origin,
        limit=limit,
    )

