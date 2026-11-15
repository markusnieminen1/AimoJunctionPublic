"""Admin Aimo platform campaigns (strictly invisible) and company brand preferences."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import clock
from app.audit import record_audit
from app.errors import AppError
from app.ids import new_id
from app.models import AimoPlatformCampaign, CompanyBrandPreference
from app.schemas import (
    AimoCampaignCreateRequest,
    AimoCampaignDeleteResponse,
    AimoCampaignListResponse,
    AimoCampaignOut,
    AimoCampaignPatchRequest,
    BrandPreferenceCreateRequest,
    BrandPreferenceDeleteResponse,
    BrandPreferenceListResponse,
    BrandPreferenceOut,
    BrandPreferencePatchRequest,
    Principal,
)


def _clean_opt_str(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _clean_product_ids(raw_ids: Sequence[str] | None) -> list[str]:
    if not raw_ids:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in raw_ids:
        pid = str(raw).strip()
        if pid and pid not in seen:
            seen.add(pid)
            out.append(pid)
    return out


def aimo_campaign_to_out(row: AimoPlatformCampaign) -> AimoCampaignOut:
    """Serialize an AimoPlatformCampaign ORM row for admin-only inspection."""
    return AimoCampaignOut(
        id=row.id,
        title=row.title,
        brand_filter=row.brand_filter,
        category_prefix=row.category_prefix,
        product_ids=[str(pid) for pid in (row.product_ids or [])],
        boost_weight=float(row.boost_weight),
        active=bool(row.active),
        created_at=row.created_at,
    )


def brand_preference_to_out(row: CompanyBrandPreference) -> BrandPreferenceOut:
    """Serialize a CompanyBrandPreference ORM row."""
    return BrandPreferenceOut(
        id=row.id,
        company_id=row.company_id,
        category_prefix=row.category_prefix,
        category_label=row.category_label,
        preferred_brands=[str(b) for b in (row.preferred_brands or [])],
        active=bool(row.active),
        updated_at=row.updated_at,
    )


def get_active_aimo_campaigns(db: Session) -> list[AimoPlatformCampaign]:
    """Return active Aimo platform campaigns ordered by boost weight."""
    return list(
        db.scalars(
            select(AimoPlatformCampaign)
            .where(AimoPlatformCampaign.active.is_(True))
            .order_by(
                AimoPlatformCampaign.boost_weight.desc(),
                AimoPlatformCampaign.created_at.desc(),
            )
        ).all()
    )


def get_active_brand_preferences(
    db: Session,
    company_id: str | None,
) -> list[CompanyBrandPreference]:
    """Return active company brand preferences ordered by specificity (longer prefix first)."""
    if not company_id:
        return []
    rows = list(
        db.scalars(
            select(CompanyBrandPreference).where(
                CompanyBrandPreference.company_id == company_id,
                CompanyBrandPreference.active.is_(True),
            )
        ).all()
    )
    rows.sort(key=lambda r: (-len(r.category_prefix or ""), r.id))
    return rows


def evaluate_aimo_campaign_score(
    *,
    product_id: str,
    brand: str | None,
    category: str,
    campaigns: Sequence[AimoPlatformCampaign],
) -> float:
    """Compute strictly invisible numeric ranking score from active Aimo platform campaigns.

    Never returns titles, labels, or reasons — strictly used as an internal tie-break score
    after diet PASS, allergen, stock, and cut-off filters.
    """
    if not campaigns:
        return 0.0
    score = 0.0
    brand_lower = (brand or "").lower()
    for camp in campaigns:
        if not camp.active:
            continue
        has_criterion = bool(camp.brand_filter or camp.category_prefix or camp.product_ids)
        if not has_criterion:
            continue
        if camp.brand_filter and camp.brand_filter.lower() not in brand_lower:
            continue
        if camp.category_prefix and not (category or "").startswith(camp.category_prefix):
            continue
        if camp.product_ids and product_id not in camp.product_ids:
            continue
        score += float(camp.boost_weight)
    return round(score, 4)


def evaluate_brand_preference_score(
    *,
    brand: str | None,
    category: str,
    preferences: Sequence[CompanyBrandPreference],
) -> float:
    """Compute ordered brand preference score using longest-prefix specificity override."""
    if not preferences or not brand:
        return 0.0
    brand_lower = brand.lower()
    cat = category or ""
    matching = [
        pref
        for pref in preferences
        if pref.active and cat.startswith(pref.category_prefix or "")
    ]
    if not matching:
        return 0.0
    max_prefix_len = max(len(pref.category_prefix or "") for pref in matching)
    best = 0.0
    for pref in matching:
        if len(pref.category_prefix or "") != max_prefix_len:
            continue
        for idx, pref_brand in enumerate(pref.preferred_brands or []):
            pb = str(pref_brand).strip().lower()
            if pb and pb in brand_lower:
                rank_boost = max(0.3, 2.0 - idx * 0.4)
                if rank_boost > best:
                    best = rank_boost
                break
    return round(best, 4)


# ---------- Admin Aimo Campaigns CRUD (Human-Only) ----------


def list_aimo_campaigns(db: Session, *, active_only: bool = False) -> AimoCampaignListResponse:
    """List platform-wide Aimo campaigns (admin human users only)."""
    stmt = select(AimoPlatformCampaign)
    if active_only:
        stmt = stmt.where(AimoPlatformCampaign.active.is_(True))
    stmt = stmt.order_by(
        AimoPlatformCampaign.active.desc(),
        AimoPlatformCampaign.boost_weight.desc(),
        AimoPlatformCampaign.created_at.desc(),
    )
    rows = db.scalars(stmt).all()
    return AimoCampaignListResponse(
        items=[aimo_campaign_to_out(r) for r in rows],
        total=len(rows),
    )


def _validate_campaign_criteria(
    brand_filter: str | None,
    category_prefix: str | None,
    product_ids: list[str],
) -> None:
    if not (brand_filter or category_prefix or product_ids):
        raise AppError(
            "VALIDATION_ERROR",
            422,
            "Campaign requires at least one of brand_filter, category_prefix, or product_ids",
        )


def create_aimo_campaign(
    db: Session,
    principal: Principal,
    req: AimoCampaignCreateRequest,
) -> AimoCampaignOut:
    """Create a platform Aimo campaign (human user only) and record admin audit."""
    brand_filter = _clean_opt_str(req.brand_filter)
    category_prefix = _clean_opt_str(req.category_prefix)
    product_ids = _clean_product_ids(req.product_ids)
    _validate_campaign_criteria(brand_filter, category_prefix, product_ids)

    now = clock.now()
    row = AimoPlatformCampaign(
        id=new_id("acmp_"),
        title=req.title.strip(),
        brand_filter=brand_filter,
        category_prefix=category_prefix,
        product_ids=product_ids,
        boost_weight=float(req.boost_weight),
        active=bool(req.active),
        created_by_user_id=principal.id,
        created_at=now,
    )
    db.add(row)
    db.flush()

    record_audit(
        db,
        company_id=principal.company_id,
        actor=principal,
        action="admin.aimo_campaign.create",
        entity_type="aimo_campaign",
        entity_id=row.id,
        outcome="ok",
        details={
            "title": row.title,
            "brand_filter": row.brand_filter,
            "category_prefix": row.category_prefix,
            "product_ids": row.product_ids,
            "boost_weight": row.boost_weight,
            "active": row.active,
        },
    )
    db.commit()
    db.refresh(row)
    return aimo_campaign_to_out(row)


def update_aimo_campaign(
    db: Session,
    principal: Principal,
    campaign_id: str,
    req: AimoCampaignPatchRequest,
) -> AimoCampaignOut:
    """Update a platform Aimo campaign (human user only)."""
    row = db.get(AimoPlatformCampaign, campaign_id)
    if row is None:
        raise AppError("NOT_FOUND", 404, "Aimo campaign not found")

    data = req.model_dump(exclude_unset=True)
    changed: dict[str, object] = {}
    if "title" in data and data["title"] is not None:
        row.title = str(data["title"]).strip()
        changed["title"] = row.title
    if "brand_filter" in data:
        row.brand_filter = _clean_opt_str(data["brand_filter"])
        changed["brand_filter"] = row.brand_filter
    if "category_prefix" in data:
        row.category_prefix = _clean_opt_str(data["category_prefix"])
        changed["category_prefix"] = row.category_prefix
    if "product_ids" in data and data["product_ids"] is not None:
        row.product_ids = _clean_product_ids(data["product_ids"])
        changed["product_ids"] = row.product_ids
    if "boost_weight" in data and data["boost_weight"] is not None:
        row.boost_weight = float(data["boost_weight"])
        changed["boost_weight"] = row.boost_weight
    if "active" in data and data["active"] is not None:
        row.active = bool(data["active"])
        changed["active"] = row.active

    _validate_campaign_criteria(
        row.brand_filter,
        row.category_prefix,
        list(row.product_ids or []),
    )
    db.flush()

    record_audit(
        db,
        company_id=principal.company_id,
        actor=principal,
        action="admin.aimo_campaign.update",
        entity_type="aimo_campaign",
        entity_id=row.id,
        outcome="ok",
        details={"changed": changed},
    )
    db.commit()
    db.refresh(row)
    return aimo_campaign_to_out(row)


def delete_aimo_campaign(
    db: Session,
    principal: Principal,
    campaign_id: str,
) -> AimoCampaignDeleteResponse:
    """Delete a platform Aimo campaign (human user only)."""
    row = db.get(AimoPlatformCampaign, campaign_id)
    if row is None:
        raise AppError("NOT_FOUND", 404, "Aimo campaign not found")
    title = row.title
    db.delete(row)
    db.flush()

    record_audit(
        db,
        company_id=principal.company_id,
        actor=principal,
        action="admin.aimo_campaign.delete",
        entity_type="aimo_campaign",
        entity_id=campaign_id,
        outcome="ok",
        details={"title": title},
    )
    db.commit()
    return AimoCampaignDeleteResponse(id=campaign_id, deleted=True)


# ---------- Company Brand Preferences CRUD ----------


def list_brand_preferences(
    db: Session,
    company_id: str,
    *,
    active_only: bool = False,
) -> BrandPreferenceListResponse:
    """List company-scoped category brand preferences."""
    stmt = select(CompanyBrandPreference).where(
        CompanyBrandPreference.company_id == company_id
    )
    if active_only:
        stmt = stmt.where(CompanyBrandPreference.active.is_(True))
    stmt = stmt.order_by(
        CompanyBrandPreference.active.desc(),
        CompanyBrandPreference.category_prefix.asc(),
        CompanyBrandPreference.updated_at.desc(),
    )
    rows = db.scalars(stmt).all()
    return BrandPreferenceListResponse(
        items=[brand_preference_to_out(r) for r in rows],
        total=len(rows),
    )


def _load_company_brand_preference(
    db: Session,
    preference_id: str,
    company_id: str,
) -> CompanyBrandPreference:
    row = db.scalar(
        select(CompanyBrandPreference).where(
            CompanyBrandPreference.id == preference_id,
            CompanyBrandPreference.company_id == company_id,
        )
    )
    if row is None:
        raise AppError("NOT_FOUND", 404, "Brand preference not found")
    return row


def create_brand_preference(
    db: Session,
    principal: Principal,
    req: BrandPreferenceCreateRequest,
) -> BrandPreferenceOut:
    """Create a company brand preference (human user only) and record audit."""
    now = clock.now()
    row = CompanyBrandPreference(
        id=new_id("bprf_"),
        company_id=principal.company_id,
        category_prefix=req.category_prefix.strip(),
        category_label=req.category_label.strip(),
        preferred_brands=list(req.preferred_brands),
        active=bool(req.active),
        updated_at=now,
    )
    db.add(row)
    db.flush()

    record_audit(
        db,
        company_id=principal.company_id,
        actor=principal,
        action="brand_preference.create",
        entity_type="brand_preference",
        entity_id=row.id,
        outcome="ok",
        details={
            "category_prefix": row.category_prefix,
            "category_label": row.category_label,
            "preferred_brands": row.preferred_brands,
            "active": row.active,
        },
    )
    db.commit()
    db.refresh(row)
    return brand_preference_to_out(row)


def update_brand_preference(
    db: Session,
    principal: Principal,
    preference_id: str,
    req: BrandPreferencePatchRequest,
) -> BrandPreferenceOut:
    """Update a company brand preference (human user only; 404 cross-company)."""
    row = _load_company_brand_preference(db, preference_id, principal.company_id)
    data = req.model_dump(exclude_unset=True)
    changed: dict[str, object] = {}

    if "category_prefix" in data and data["category_prefix"] is not None:
        row.category_prefix = str(data["category_prefix"]).strip()
        changed["category_prefix"] = row.category_prefix
    if "category_label" in data and data["category_label"] is not None:
        row.category_label = str(data["category_label"]).strip()
        changed["category_label"] = row.category_label
    if "preferred_brands" in data and data["preferred_brands"] is not None:
        row.preferred_brands = list(data["preferred_brands"])
        changed["preferred_brands"] = row.preferred_brands
    if "active" in data and data["active"] is not None:
        row.active = bool(data["active"])
        changed["active"] = row.active

    row.updated_at = clock.now()
    db.flush()

    record_audit(
        db,
        company_id=principal.company_id,
        actor=principal,
        action="brand_preference.update",
        entity_type="brand_preference",
        entity_id=row.id,
        outcome="ok",
        details={"changed": changed},
    )
    db.commit()
    db.refresh(row)
    return brand_preference_to_out(row)


def delete_brand_preference(
    db: Session,
    principal: Principal,
    preference_id: str,
) -> BrandPreferenceDeleteResponse:
    """Delete a company brand preference (human user only; 404 cross-company)."""
    row = _load_company_brand_preference(db, preference_id, principal.company_id)
    cat_label = row.category_label
    db.delete(row)
    db.flush()

    record_audit(
        db,
        company_id=principal.company_id,
        actor=principal,
        action="brand_preference.delete",
        entity_type="brand_preference",
        entity_id=preference_id,
        outcome="ok",
        details={"category_label": cat_label},
    )
    db.commit()
    return BrandPreferenceDeleteResponse(id=preference_id, deleted=True)
