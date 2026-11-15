"""Assistant router (POST /api/assistant/chat)."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.aimo.client import AimoClient, get_aimo_client
from app.auth import CurrentPrincipal, DbSession
from app.llm.decisions import DecisionProvider, get_decision_provider
from app.llm.provider import LLMProvider, get_llm_provider
from app.schemas import AssistantChatRequest, AssistantChatResponse
from app.services.assistant import chat
from app.services.order_gateway import OrderGateway, get_order_gateway

router = APIRouter(prefix="/assistant", tags=["assistant"])


@router.post("/chat", response_model=AssistantChatResponse)
def post_assistant_chat(
    payload: AssistantChatRequest,
    principal: CurrentPrincipal,
    db: DbSession,
    aimo_client: Annotated[AimoClient, Depends(get_aimo_client)],
    gateway: Annotated[OrderGateway, Depends(get_order_gateway)],
    llm_provider: Annotated[LLMProvider, Depends(get_llm_provider)],
    decision_provider: Annotated[DecisionProvider, Depends(get_decision_provider)],
) -> AssistantChatResponse:
    """Process a natural-language purchasing assistant turn for the caller's principal."""
    return chat(
        db,
        aimo_client=aimo_client,
        gateway=gateway,
        llm_provider=llm_provider,
        decision_provider=decision_provider,
        principal=principal,
        req=payload,
    )
