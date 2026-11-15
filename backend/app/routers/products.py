"""Product catalog search, detail, categories, live check, and substitute endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.aimo.client import AimoClient, get_aimo_client
from app.aimo.normalise import check_products_live
from app.auth import CurrentPrincipal, DbSession
from app.llm.decisions import DecisionProvider, get_decision_provider
from app.schemas import (
    CategoryListResponse,
    DealsResponse,
    ProductCheckRequest,
    ProductCheckResponse,
    ProductListResponse,
    ProductOut,
    SubstitutesRequest,
    SubstitutesResponse,
)
from app.services.catalog import get_product, list_categories, search_products
from app.services.deals import find_deals
from app.services.substitutes import find_substitutes

router = APIRouter(prefix="/products", tags=["products"])
categories_router = APIRouter(tags=["products"])


@categories_router.get("/categories", response_model=CategoryListResponse)
def get_categories(
    _principal: CurrentPrincipal,
    db: DbSession,
) -> CategoryListResponse:
    """List product categories with English/Finnish names and product counts."""
    return CategoryListResponse(items=list_categories(db))


@router.get("", response_model=ProductListResponse)
def list_products(
    _principal: CurrentPrincipal,
    db: DbSession,
    q: str | None = None,
    category: str | None = None,
    allergen_free: str | None = None,
    label: str | None = None,
    diet: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ProductListResponse:
    """Search and diet/allergen-filter catalog products using local DB only."""
    items, total = search_products(
        db,
        q=q,
        category=category,
        allergen_free=allergen_free,
        label=label,
        diet=diet,
        limit=limit,
        offset=offset,
        company_id=_principal.company_id,
    )
    return ProductListResponse(items=items, total=total)


@router.post("/check", response_model=ProductCheckResponse)
def check_products(
    payload: ProductCheckRequest,
    principal: CurrentPrincipal,
    db: DbSession,
    client: Annotated[AimoClient, Depends(get_aimo_client)],
) -> ProductCheckResponse:
    """Fetch live price and availability for a shortlist without storing a quote."""
    items = check_products_live(
        db=db,
        aimo_client=client,
        product_ids=payload.product_ids,
        quantities=payload.quantities,
        requested_delivery_date=payload.requested_delivery_date,
        customer_id=principal.company_id,
    )
    return ProductCheckResponse(items=items)


@router.post("/substitutes", response_model=SubstitutesResponse)
def suggest_substitutes(
    payload: SubstitutesRequest,
    principal: CurrentPrincipal,
    db: DbSession,
    client: Annotated[AimoClient, Depends(get_aimo_client)],
    decision_provider: Annotated[DecisionProvider, Depends(get_decision_provider)],
) -> SubstitutesResponse:
    """Suggest diet-safe, delivery-feasible substitutes with explicit trade-off notes."""
    return find_substitutes(
        db=db,
        aimo_client=client,
        product_id=payload.product_id,
        quantity=payload.quantity,
        requested_delivery_date=payload.requested_delivery_date,
        diet=payload.diet,
        customer_id=principal.company_id,
        decision_provider=decision_provider,
    )


@router.get("/deals", response_model=DealsResponse)
def get_product_deals(
    principal: CurrentPrincipal,
    db: DbSession,
    aimo_client: Annotated[AimoClient, Depends(get_aimo_client)],
    q: str | None = None,
    category: str | None = None,
    diet: str | None = None,
    valio_only: bool = False,
    on_sale_only: bool = True,
    requested_delivery_date: str | None = None,
    limit: int = Query(default=20, ge=1, le=50),
) -> DealsResponse:
    """Discover live on-sale (listPrice > catalogPrice) and management-promoted products."""
    return find_deals(
        db,
        aimo_client,
        principal,
        q=q,
        category=category,
        diet=diet,
        valio_only=valio_only,
        on_sale_only=on_sale_only,
        requested_delivery_date=requested_delivery_date,
        limit=limit,
    )


@router.get("/{product_id}", response_model=ProductOut)
def get_product_detail(
    product_id: str,
    _principal: CurrentPrincipal,
    db: DbSession,
) -> ProductOut:
    """Fetch a single product from the local catalog by ID."""
    return get_product(db, product_id)
