"""Management promotions CRUD and product promotion scoring helpers."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import clock
from app.audit import record_audit
from app.errors import AppError
from app.ids import new_id
from app.models import Product, PromotionRule
from app.schemas import (
    Principal,
    PromotionCreateRequest,
    PromotionDeleteResponse,
    PromotionListResponse,
    PromotionOut,
    PromotionPatchRequest,
)


def promotion_to_out(rule: PromotionRule) -> PromotionOut:
    """Serialize a PromotionRule ORM row to PromotionOut."""
    return PromotionOut(
        id=rule.id,
        company_id=rule.company_id,
        title=rule.title,
        description=rule.description,
        brand_filter=rule.brand_filter,
        category_prefix=rule.category_prefix,
        product_ids=[str(pid) for pid in (rule.product_ids or [])],
        boost_weight=float(rule.boost_weight),
        prefer_on_sale=bool(rule.prefer_on_sale),
        active=bool(rule.active),
        created_by_user_id=rule.created_by_user_id,
        created_at=rule.created_at,
    )


def get_active_promotions(db: Session, company_id: str) -> list[PromotionRule]:
    """Return active management promotion rules for a company ordered by boost weight."""
    return list(
        db.scalars(
            select(PromotionRule)
            .where(PromotionRule.company_id == company_id, PromotionRule.active.is_(True))
            .order_by(PromotionRule.boost_weight.desc(), PromotionRule.created_at.desc())
        ).all()
    )


def evaluate_product_promotions(
    *,
    product_id: str,
    brand: str | None,
    category: str,
    rules: Sequence[PromotionRule],
    on_sale: bool = False,
) -> tuple[bool, list[str], float]:
    """Evaluate active PromotionRules and return (promoted, reasons, score)."""
    reasons: list[str] = []
    score = 0.0
    brand_lower = (brand or "").lower()

    for rule in rules:
        if not rule.active:
            continue
        has_criterion = bool(
            rule.brand_filter or rule.category_prefix or rule.product_ids or rule.prefer_on_sale
        )
        if not has_criterion:
            continue
        if rule.brand_filter and rule.brand_filter.lower() not in brand_lower:
            continue
        if rule.category_prefix and not category.startswith(rule.category_prefix):
            continue
        if rule.product_ids and product_id not in rule.product_ids:
            continue
        if rule.prefer_on_sale and not on_sale:
            continue

        reasons.append(rule.title)
        score += float(rule.boost_weight)

    return bool(reasons), reasons, round(score, 4)


def score_product_promotions(
    product: Product,
    rules: Sequence[PromotionRule],
    *,
    on_sale: bool = False,
) -> tuple[bool, list[str], float]:
    """Evaluate active PromotionRules for an ORM Product row."""
    return evaluate_product_promotions(
        product_id=product.id,
        brand=product.brand,
        category=product.category,
        rules=rules,
        on_sale=on_sale,
    )


def list_promotions(
    db: Session,
    company_id: str,
    *,
    active_only: bool = False,
) -> PromotionListResponse:
    """List company-scoped promotions ordered by active status and boost weight."""
    stmt = select(PromotionRule).where(PromotionRule.company_id == company_id)
    if active_only:
        stmt = stmt.where(PromotionRule.active.is_(True))
    stmt = stmt.order_by(
        PromotionRule.active.desc(),
        PromotionRule.boost_weight.desc(),
        PromotionRule.created_at.desc(),
    )
    rows = db.scalars(stmt).all()
    return PromotionListResponse(
        items=[promotion_to_out(row) for row in rows],
        total=len(rows),
    )


def _load_company_promotion(db: Session, promotion_id: str, company_id: str) -> PromotionRule:
    rule = db.scalar(
        select(PromotionRule).where(
            PromotionRule.id == promotion_id,
            PromotionRule.company_id == company_id,
        )
    )
    if rule is None:
        raise AppError("NOT_FOUND", 404, "Promotion not found")
    return rule


def create_promotion(
    db: Session,
    principal: Principal,
    req: PromotionCreateRequest,
) -> PromotionOut:
    """Create a management promotion rule (human user only) and record audit."""
    now = clock.now()
    rule = PromotionRule(
        id=new_id("prm_"),
        company_id=principal.company_id,
        title=req.title,
        description=req.description,
        brand_filter=req.brand_filter,
        category_prefix=req.category_prefix,
        product_ids=list(req.product_ids),
        boost_weight=float(req.boost_weight),
        prefer_on_sale=bool(req.prefer_on_sale),
        active=bool(req.active),
        created_by_user_id=principal.id,
        created_at=now,
    )
    db.add(rule)
    db.flush()

    record_audit(
        db,
        company_id=principal.company_id,
        actor=principal,
        action="promotion.create",
        entity_type="promotion",
        entity_id=rule.id,
        outcome="ok",
        details={
            "title": rule.title,
            "brand_filter": rule.brand_filter,
            "category_prefix": rule.category_prefix,
            "product_ids": rule.product_ids,
            "boost_weight": rule.boost_weight,
            "prefer_on_sale": rule.prefer_on_sale,
            "active": rule.active,
        },
    )
    db.commit()
    db.refresh(rule)
    return promotion_to_out(rule)


def update_promotion(
    db: Session,
    principal: Principal,
    promotion_id: str,
    req: PromotionPatchRequest,
) -> PromotionOut:
    """Update a company-scoped management promotion rule (human user only)."""
    rule = _load_company_promotion(db, promotion_id, principal.company_id)

    changed: dict[str, object] = {}
    for field in (
        "title",
        "description",
        "brand_filter",
        "category_prefix",
        "product_ids",
        "boost_weight",
        "prefer_on_sale",
        "active",
    ):
        if field in req.model_fields_set:
            val = getattr(req, field)
            setattr(rule, field, val)
            changed[field] = val

    db.flush()
    record_audit(
        db,
        company_id=principal.company_id,
        actor=principal,
        action="promotion.update",
        entity_type="promotion",
        entity_id=rule.id,
        outcome="ok",
        details={"updated_fields": changed},
    )
    db.commit()
    db.refresh(rule)
    return promotion_to_out(rule)


def delete_promotion(
    db: Session,
    principal: Principal,
    promotion_id: str,
) -> PromotionDeleteResponse:
    """Delete a company-scoped management promotion rule (human user only)."""
    rule = _load_company_promotion(db, promotion_id, principal.company_id)
    title = rule.title
    db.delete(rule)
    db.flush()

    record_audit(
        db,
        company_id=principal.company_id,
        actor=principal,
        action="promotion.delete",
        entity_type="promotion",
        entity_id=promotion_id,
        outcome="ok",
        details={"title": title},
    )
    db.commit()
    return PromotionDeleteResponse(deleted=True, id=promotion_id)
