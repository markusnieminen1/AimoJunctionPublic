"""Admin Aimo platform campaigns, company brand preferences, and negotiation settings routers."""

from fastapi import APIRouter, status

from app.auth import CurrentPrincipal, CurrentUser, DbSession
from app.schemas import (
    AimoCampaignCreateRequest,
    AimoCampaignDeleteResponse,
    AimoCampaignListResponse,
    AimoCampaignOut,
    AimoCampaignPatchRequest,
    AssistantSettingsOut,
    AssistantSettingsUpdateRequest,
    BrandPreferenceCreateRequest,
    BrandPreferenceDeleteResponse,
    BrandPreferenceListResponse,
    BrandPreferenceOut,
    BrandPreferencePatchRequest,
    CompanyNegotiationAdminListResponse,
    CompanyNegotiationAdminOut,
    NegotiationSettingsOut,
    NegotiationSettingsUpdateRequest,
)
from app.services.admin_ranking import (
    create_aimo_campaign,
    create_brand_preference,
    delete_aimo_campaign,
    delete_brand_preference,
    list_aimo_campaigns,
    list_brand_preferences,
    update_aimo_campaign,
    update_brand_preference,
)
from app.services.assistant import get_assistant_settings, update_assistant_settings
from app.services.negotiation import (
    get_negotiation_settings,
    list_company_negotiations_admin,
    update_company_negotiation_admin,
    update_negotiation_settings,
)

admin_campaigns_router = APIRouter(prefix="/admin/aimo-campaigns", tags=["admin"])
admin_company_negotiations_router = APIRouter(prefix="/admin/company-negotiations", tags=["admin"])
brand_preferences_router = APIRouter(prefix="/brand-preferences", tags=["brand-preferences"])
account_settings_router = APIRouter(prefix="/account", tags=["account"])


@admin_campaigns_router.get("", response_model=AimoCampaignListResponse)
def get_aimo_campaigns(
    _principal: CurrentUser,
    db: DbSession,
    active_only: bool = False,
) -> AimoCampaignListResponse:
    """List platform Aimo campaigns (strictly human user only; agents receive 403)."""
    return list_aimo_campaigns(db, active_only=active_only)


@admin_campaigns_router.post(
    "",
    response_model=AimoCampaignOut,
    status_code=status.HTTP_201_CREATED,
)
def post_aimo_campaign(
    payload: AimoCampaignCreateRequest,
    principal: CurrentUser,
    db: DbSession,
) -> AimoCampaignOut:
    """Create an invisible platform Aimo campaign (human user only; 403 for agents)."""
    return create_aimo_campaign(db, principal, payload)


@admin_campaigns_router.patch("/{campaign_id}", response_model=AimoCampaignOut)
def patch_aimo_campaign(
    campaign_id: str,
    payload: AimoCampaignPatchRequest,
    principal: CurrentUser,
    db: DbSession,
) -> AimoCampaignOut:
    """Update an invisible platform Aimo campaign (human user only; 403 for agents)."""
    return update_aimo_campaign(db, principal, campaign_id, payload)


@admin_campaigns_router.delete("/{campaign_id}", response_model=AimoCampaignDeleteResponse)
def remove_aimo_campaign(
    campaign_id: str,
    principal: CurrentUser,
    db: DbSession,
) -> AimoCampaignDeleteResponse:
    """Delete an invisible platform Aimo campaign (human user only; 403 for agents)."""
    return delete_aimo_campaign(db, principal, campaign_id)


@admin_company_negotiations_router.get("", response_model=CompanyNegotiationAdminListResponse)
def get_admin_company_negotiations(
    principal: CurrentUser,
    db: DbSession,
) -> CompanyNegotiationAdminListResponse:
    """List per-company price negotiation toggles in Admin panel (human user only; 403 agents)."""
    return list_company_negotiations_admin(db, principal)


@admin_company_negotiations_router.put(
    "/{company_id}",
    response_model=CompanyNegotiationAdminOut,
)
def put_admin_company_negotiation(
    company_id: str,
    payload: NegotiationSettingsUpdateRequest,
    principal: CurrentUser,
    db: DbSession,
) -> CompanyNegotiationAdminOut:
    """Toggle price negotiation for a specific company in Admin panel (human user only; 403)."""
    return update_company_negotiation_admin(db, principal, company_id, payload)


@brand_preferences_router.get("", response_model=BrandPreferenceListResponse)
def get_brand_preferences(
    principal: CurrentPrincipal,
    db: DbSession,
    active_only: bool = False,
) -> BrandPreferenceListResponse:
    """List company-scoped ordered category brand preferences."""
    return list_brand_preferences(db, principal.company_id, active_only=active_only)


@brand_preferences_router.post(
    "",
    response_model=BrandPreferenceOut,
    status_code=status.HTTP_201_CREATED,
)
def post_brand_preference(
    payload: BrandPreferenceCreateRequest,
    principal: CurrentUser,
    db: DbSession,
) -> BrandPreferenceOut:
    """Create a company brand preference (human user only; 403 for agents)."""
    return create_brand_preference(db, principal, payload)


@brand_preferences_router.patch("/{preference_id}", response_model=BrandPreferenceOut)
def patch_brand_preference(
    preference_id: str,
    payload: BrandPreferencePatchRequest,
    principal: CurrentUser,
    db: DbSession,
) -> BrandPreferenceOut:
    """Update a company brand preference (human user only; 404 cross-company)."""
    return update_brand_preference(db, principal, preference_id, payload)


@brand_preferences_router.delete(
    "/{preference_id}",
    response_model=BrandPreferenceDeleteResponse,
)
def remove_brand_preference(
    preference_id: str,
    principal: CurrentUser,
    db: DbSession,
) -> BrandPreferenceDeleteResponse:
    """Delete a company brand preference (human user only; 404 cross-company)."""
    return delete_brand_preference(db, principal, preference_id)


@account_settings_router.get("/negotiation-settings", response_model=NegotiationSettingsOut)
def get_account_negotiation_settings(
    principal: CurrentPrincipal,
    db: DbSession,
) -> NegotiationSettingsOut:
    """Return company negotiation policy toggle and optional discount cap."""
    return get_negotiation_settings(db, principal.company_id)


@account_settings_router.put("/negotiation-settings", response_model=NegotiationSettingsOut)
def put_account_negotiation_settings(
    payload: NegotiationSettingsUpdateRequest,
    principal: CurrentUser,
    db: DbSession,
) -> NegotiationSettingsOut:
    """Update company negotiation policy toggle (human user only; 403 for agents)."""
    return update_negotiation_settings(db, principal, payload)


@account_settings_router.get("/assistant-settings", response_model=AssistantSettingsOut)
def get_account_assistant_settings(
    principal: CurrentPrincipal,
    db: DbSession,
) -> AssistantSettingsOut:
    """Return company Order Assurance score threshold (50..100, default 80)."""
    return get_assistant_settings(db, principal.company_id)


@account_settings_router.put("/assistant-settings", response_model=AssistantSettingsOut)
def put_account_assistant_settings(
    payload: AssistantSettingsUpdateRequest,
    principal: CurrentUser,
    db: DbSession,
) -> AssistantSettingsOut:
    """Update company Order Assurance score threshold (human user only; 403 for agents)."""
    return update_assistant_settings(db, principal, payload)
