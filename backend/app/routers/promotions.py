"""Management promotions / recommendations endpoints (write operations human-only)."""

from fastapi import APIRouter, status

from app.auth import CurrentPrincipal, CurrentUser, DbSession
from app.schemas import (
    PromotionCreateRequest,
    PromotionDeleteResponse,
    PromotionListResponse,
    PromotionOut,
    PromotionPatchRequest,
)
from app.services.promotions import (
    create_promotion,
    delete_promotion,
    list_promotions,
    update_promotion,
)

router = APIRouter(prefix="/promotions", tags=["promotions"])


@router.get("", response_model=PromotionListResponse)
def get_promotions(
    principal: CurrentPrincipal,
    db: DbSession,
    active_only: bool = False,
) -> PromotionListResponse:
    """List management promotion rules for the caller's company."""
    return list_promotions(db, principal.company_id, active_only=active_only)


@router.post("", response_model=PromotionOut, status_code=status.HTTP_201_CREATED)
def post_promotion(
    payload: PromotionCreateRequest,
    principal: CurrentUser,
    db: DbSession,
) -> PromotionOut:
    """Create a management promotion rule (human user only; agents receive 403)."""
    return create_promotion(db, principal, payload)


@router.patch("/{promotion_id}", response_model=PromotionOut)
def patch_promotion(
    promotion_id: str,
    payload: PromotionPatchRequest,
    principal: CurrentUser,
    db: DbSession,
) -> PromotionOut:
    """Update a management promotion rule (human user only; 404 cross-company)."""
    return update_promotion(db, principal, promotion_id, payload)


@router.delete("/{promotion_id}", response_model=PromotionDeleteResponse)
def remove_promotion(
    promotion_id: str,
    principal: CurrentUser,
    db: DbSession,
) -> PromotionDeleteResponse:
    """Delete a management promotion rule (human user only; 404 cross-company)."""
    return delete_promotion(db, principal, promotion_id)
