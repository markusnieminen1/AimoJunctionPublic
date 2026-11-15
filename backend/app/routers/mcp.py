"""MCP Streamable HTTP JSON-RPC 2.0 endpoint wrapping core purchasing services."""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.aimo.client import AimoClient, get_aimo_client
from app.aimo.normalise import check_products_live
from app.audit import record_audit
from app.auth import CurrentPrincipal, DbSession
from app.config import settings
from app.errors import AppError, get_request_id
from app.models import Company
from app.schemas import (
    MCPRpcRequest,
    MenuPlanRequest,
    OrderCreateRequest,
    ProductCheckRequest,
    QuoteCreateRequest,
    QuoteNegotiateRequest,
    QuoteOptimizeRequest,
)
from app.services import (
    catalog,
    deals,
    menu_planner,
    negotiation,
    optimizer,
    orders,
    promotions,
    quotes,
)
from app.services.order_gateway import OrderGateway, get_order_gateway

router = APIRouter(prefix="/mcp", tags=["mcp"])

MCP_PROTOCOL_VERSION = "2025-03-26"
_SUPPORTED_MCP_PROTOCOL_VERSIONS = frozenset(
    {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}
)

MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_products",
        "description": (
            "Search the Valio Aimo product catalog with optional Finnish menu/diet codes "
            "(L, VL, M, G, VEG, HEART) and allergen_free filters."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "Search query (fi/en/sv) or product ID"},
                "category": {"type": "string", "description": "Category code or prefix"},
                "allergen_free": {
                    "type": "string",
                    "description": "Comma-separated allergen-free filters (e.g. lactose,gluten)",
                },
                "label": {"type": "string", "description": "Comma-separated label filters"},
                "diet": {
                    "type": "string",
                    "description": "Comma-separated diet/menu codes (L, VL, M, G, VEG, HEART)",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
            },
        },
    },
    {
        "name": "check_products",
        "description": (
            "Fetch live price, availability, delivery probability, and CO2 basis for up to 50 "
            "products."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "product_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 50,
                },
                "quantities": {
                    "type": "object",
                    "additionalProperties": {"type": "number"},
                },
                "requested_delivery_date": {
                    "type": "string",
                    "description": "Delivery date YYYY-MM-DD",
                },
            },
            "required": ["product_ids", "requested_delivery_date"],
        },
    },
    {
        "name": "create_quote",
        "description": (
            "Create a 30-minute locked quote with live pricing, CO2 basis, and policy preview."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "requested_delivery_date": {"type": "string", "description": "YYYY-MM-DD"},
                "lines": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 100,
                    "items": {
                        "type": "object",
                        "properties": {
                            "product_id": {"type": "string"},
                            "quantity": {"type": "number", "exclusiveMinimum": 0},
                        },
                        "required": ["product_id", "quantity"],
                    },
                },
                "diet": {
                    "type": "string",
                    "description": "Optional diet/menu codes enforced on the quote",
                },
            },
            "required": ["requested_delivery_date", "lines"],
        },
    },
    {
        "name": "place_order",
        "description": (
            "Place an order from an OPEN quote ID through Layer 1 policy and gateway submission."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "quote_id": {"type": "string"},
                "note": {"type": "string"},
                "diet": {"type": "string"},
                "idempotency_key": {"type": "string"},
            },
            "required": ["quote_id"],
        },
    },
    {
        "name": "get_order",
        "description": "Fetch a single company- and agent-isolated order by ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "find_on_sale_products",
        "description": (
            "Discover live on-sale products (listPrice > catalogPrice) and management-promoted "
            "items with diet safety filtering."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "q": {"type": "string"},
                "category": {"type": "string"},
                "diet": {"type": "string"},
                "valio_only": {"type": "boolean", "default": False},
                "on_sale_only": {"type": "boolean", "default": True},
                "requested_delivery_date": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
            },
        },
    },
    {
        "name": "list_promotions",
        "description": "List management-configured promotions and culinary recommendations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "active_only": {"type": "boolean", "default": True},
            },
        },
    },
    {
        "name": "plan_menu",
        "description": (
            "Plan a diet-safe Nordic foodservice menu for N pax with Valio recipe pairings, "
            "deals optimization, quote draft lines, and Layer 1 policy preview."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "pax": {"type": "integer", "minimum": 1, "maximum": 5000},
                "meal_type": {"type": "string", "enum": ["breakfast", "lunch", "dinner"]},
                "requested_delivery_date": {"type": "string"},
                "diet": {"type": "string"},
                "prefer_on_sale": {"type": "boolean", "default": True},
                "include_valio_recipes": {"type": "boolean", "default": True},
                "max_budget_eur": {"type": "number", "exclusiveMinimum": 0},
            },
            "required": ["pax"],
        },
    },
    {
        "name": "optimize_quote",
        "description": (
            "Preview diet-safe, unit-normalized cart swaps for BALANCED, SAVE_MONEY, "
            "MAX_RELIABILITY, or LOW_CARBON before locking a quote (<=50 live check IDs)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "requested_delivery_date": {"type": "string", "description": "YYYY-MM-DD"},
                "lines": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 25,
                    "items": {
                        "type": "object",
                        "properties": {
                            "product_id": {"type": "string"},
                            "quantity": {"type": "number", "exclusiveMinimum": 0},
                        },
                        "required": ["product_id", "quantity"],
                    },
                },
                "diet": {"type": "string"},
                "objective": {
                    "type": "string",
                    "enum": ["BALANCED", "SAVE_MONEY", "MAX_RELIABILITY", "LOW_CARBON"],
                    "default": "BALANCED",
                },
                "allow_plant_based_swaps": {"type": "boolean", "default": False},
            },
            "required": ["requested_delivery_date", "lines"],
        },
    },
    {
        "name": "negotiate_quote",
        "description": (
            "Run company-gated preliminary A2A volume/bundle price negotiation (managed per "
            "company in the Admin panel /admin) after diet, stock, and cut-off checks, "
            "returning a locked 30-minute Quote."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "requested_delivery_date": {"type": "string", "description": "YYYY-MM-DD"},
                "lines": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 25,
                    "items": {
                        "type": "object",
                        "properties": {
                            "product_id": {"type": "string"},
                            "quantity": {"type": "number", "exclusiveMinimum": 0},
                        },
                        "required": ["product_id", "quantity"],
                    },
                },
                "diet": {"type": "string"},
                "target_discount_pct": {"type": "number", "minimum": 0, "maximum": 99},
                "target_total_eur": {"type": "number", "exclusiveMinimum": 0},
                "justification": {"type": "string"},
                "recipe_id": {"type": "string"},
                "target_pax": {"type": "integer", "minimum": 1, "maximum": 50000},
            },
            "required": ["requested_delivery_date", "lines"],
        },
    },
]

_TOOL_NAMES = frozenset(t["name"] for t in MCP_TOOLS)


def _rpc_result(rpc_id: str | int | None, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _rpc_error(
    rpc_id: str | int | None,
    *,
    code: int,
    message: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "error": {
            "code": code,
            "message": message,
            "data": data if data is not None else {},
        },
    }


def _tool_success(rpc_id: str | int | None, payload: dict[str, Any]) -> dict[str, Any]:
    return _rpc_result(
        rpc_id,
        {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(payload, ensure_ascii=False, default=str),
                }
            ],
            "structuredContent": payload,
            "isError": False,
        },
    )


def _tool_error(
    rpc_id: str | int | None,
    *,
    code: str,
    status_code: int,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "error": {
            "code": code,
            "status_code": status_code,
            "message": message,
            "details": details if details is not None else {},
        }
    }
    return _rpc_result(
        rpc_id,
        {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(payload, ensure_ascii=False, default=str),
                }
            ],
            "structuredContent": payload,
            "isError": True,
        },
    )


def _build_company_mcp_tools(db: DbSession, principal: CurrentPrincipal) -> list[dict[str, Any]]:
    """Return all 10 canonical MCP tools with live company negotiation preflight guidance."""
    company = db.get(Company, principal.company_id)
    company_name = company.name if company is not None else principal.company_id
    neg_settings = negotiation.get_negotiation_settings(db, principal.company_id)
    allow_neg = bool(neg_settings.allow_price_negotiation)
    tools_out: list[dict[str, Any]] = []
    for tool in MCP_TOOLS:
        copied = dict(tool)
        if tool["name"] == "negotiate_quote":
            if allow_neg:
                prefix = (
                    f"[ENABLED for {company_name} in Admin panel (/admin) — on preflight / "
                    "'what are your capabilities?', tell the user you can negotiate prices for "
                    "larger quantities] "
                )
            else:
                prefix = (
                    f"[DISABLED for {company_name} — managed per company in Admin panel (/admin); "
                    "do not claim bulk price negotiation is active] "
                )
            copied["description"] = prefix + str(tool["description"])
        tools_out.append(copied)
    return tools_out


def _dispatch_tool(
    tool_name: str,
    args: dict[str, Any],
    *,
    principal: CurrentPrincipal,
    db: DbSession,
    aimo_client: AimoClient,
    gateway: OrderGateway,
) -> tuple[dict[str, Any], str, str | None]:
    if tool_name == "search_products":
        limit = int(args.get("limit") if args.get("limit") is not None else 20)
        offset = int(args.get("offset") if args.get("offset") is not None else 0)
        limit = max(1, min(limit, 100))
        offset = max(0, offset)
        items, total = catalog.search_products(
            db,
            q=args.get("q"),
            category=args.get("category"),
            allergen_free=args.get("allergen_free"),
            label=args.get("label"),
            diet=args.get("diet"),
            limit=limit,
            offset=offset,
            company_id=principal.company_id,
        )
        return (
            {"items": [p.model_dump(mode="json") for p in items], "total": total},
            "product",
            None,
        )

    if tool_name == "check_products":
        req = ProductCheckRequest.model_validate(args)
        lines = check_products_live(
            db=db,
            aimo_client=aimo_client,
            product_ids=req.product_ids,
            quantities=req.quantities,
            requested_delivery_date=req.requested_delivery_date,
            customer_id=principal.company_id,
        )
        return ({"items": [ln.model_dump(mode="json") for ln in lines]}, "product", None)

    if tool_name == "create_quote":
        quote_req = QuoteCreateRequest.model_validate(args)
        quote_out = quotes.create_quote(
            db=db,
            aimo_client=aimo_client,
            principal=principal,
            req=quote_req,
            source_context={"channel": "mcp"},
        )
        return (quote_out.model_dump(mode="json"), "quote", quote_out.id)

    if tool_name == "place_order":
        order_req = OrderCreateRequest.model_validate(args)
        raw_key = str(args.get("idempotency_key") or "").strip() or f"mcp_{get_request_id()}"
        _, order_body = orders.place_order(
            db=db,
            aimo_client=aimo_client,
            principal=principal,
            req=order_req,
            raw_idempotency_key=raw_key,
            gateway=gateway,
            source_context={"channel": "mcp"},
        )
        order_id = str(order_body.get("id")) if order_body.get("id") else None
        return (order_body, "order", order_id)

    if tool_name == "get_order":
        order_id = str(args.get("order_id") or "").strip()
        if not order_id:
            raise AppError("VALIDATION_ERROR", 422, "order_id is required")
        order_out = orders.get_order(db=db, principal=principal, order_id=order_id)
        return (order_out.model_dump(mode="json"), "order", order_out.id)

    if tool_name == "find_on_sale_products":
        limit = int(args.get("limit") if args.get("limit") is not None else 20)
        limit = max(1, min(limit, 50))
        deals_out = deals.find_deals(
            db,
            aimo_client,
            principal,
            q=args.get("q"),
            category=args.get("category"),
            diet=args.get("diet"),
            valio_only=bool(args.get("valio_only", False)),
            on_sale_only=bool(args.get("on_sale_only", True)),
            requested_delivery_date=args.get("requested_delivery_date"),
            limit=limit,
        )
        return (deals_out.model_dump(mode="json"), "product", None)

    if tool_name == "list_promotions":
        active_only = bool(args.get("active_only", True))
        promos_out = promotions.list_promotions(db, principal.company_id, active_only=active_only)
        return (promos_out.model_dump(mode="json"), "promotion", None)

    if tool_name == "plan_menu":
        menu_req = MenuPlanRequest.model_validate(args)
        plan_out = menu_planner.plan_menu(
            db=db, aimo_client=aimo_client, principal=principal, req=menu_req
        )
        return (plan_out.model_dump(mode="json"), "menu_plan", plan_out.plan_id)

    if tool_name == "optimize_quote":
        opt_req = QuoteOptimizeRequest.model_validate(args)
        opt_out = optimizer.optimize_quote(
            db=db, aimo_client=aimo_client, principal=principal, req=opt_req
        )
        return (opt_out.model_dump(mode="json"), "quote", "preview")

    if tool_name == "negotiate_quote":
        neg_req = QuoteNegotiateRequest.model_validate(args)
        neg_out = negotiation.negotiate_quote(
            db=db, aimo_client=aimo_client, principal=principal, req=neg_req
        )
        return (neg_out.model_dump(mode="json"), "quote", neg_out.quote.id)

    raise AppError("VALIDATION_ERROR", 422, f"Unsupported tool: {tool_name}")


@router.post("")
def post_mcp_rpc(
    payload: MCPRpcRequest,
    principal: CurrentPrincipal,
    db: DbSession,
    aimo_client: Annotated[AimoClient, Depends(get_aimo_client)],
    gateway: Annotated[OrderGateway, Depends(get_order_gateway)],
) -> Response:
    """Handle MCP Streamable HTTP JSON-RPC 2.0 requests (initialize, tools/list, tools/call)."""
    if payload.jsonrpc != "2.0":
        return JSONResponse(
            content=_rpc_error(payload.id, code=-32600, message="Invalid JSON-RPC version")
        )

    if payload.method == "notifications/initialized" or (
        payload.id is None and payload.method.startswith("notifications/")
    ):
        return Response(status_code=202)

    if payload.method == "ping":
        return JSONResponse(content=_rpc_result(payload.id, {}))

    if payload.method == "initialize":
        from app.a2a.service import build_session_capabilities_payload

        params = payload.params if isinstance(payload.params, dict) else {}
        req_proto = params.get("protocolVersion")
        negotiated_proto = (
            req_proto
            if isinstance(req_proto, str) and req_proto in _SUPPORTED_MCP_PROTOCOL_VERSIONS
            else MCP_PROTOCOL_VERSION
        )
        session_caps = build_session_capabilities_payload(db, principal)
        supported_matters_record: dict[str, dict[str, Any]] = {
            str(m["id"]): {
                "title": m["title"],
                "summary": m["summary"],
                "enabled": bool(m["enabled"]),
                "tools_or_skills": list(m.get("tools_or_skills") or []),
            }
            for m in session_caps["supported_matters"]
            if isinstance(m, dict) and m.get("id")
        }
        return JSONResponse(
            content=_rpc_result(
                payload.id,
                {
                    "protocolVersion": negotiated_proto,
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "experimental": {"supportedMatters": supported_matters_record},
                    },
                    "serverInfo": {
                        "name": "aimoannos-mcp",
                        "version": settings.version,
                    },
                    "instructions": session_caps["session_greeting"],
                },
            )
        )

    if payload.method == "tools/list":
        return JSONResponse(
            content=_rpc_result(
                payload.id,
                {"tools": _build_company_mcp_tools(db, principal)},
            )
        )

    if payload.method == "tools/call":
        params = payload.params or {}
        tool_name = params.get("name")
        raw_args = params.get("arguments")
        if not isinstance(tool_name, str) or tool_name not in _TOOL_NAMES:
            return JSONResponse(
                content=_rpc_error(
                    payload.id,
                    code=-32602,
                    message=f"Unknown tool: {tool_name}",
                )
            )
        args = raw_args if isinstance(raw_args, dict) else {}
        if raw_args is not None and not isinstance(raw_args, dict):
            return JSONResponse(
                content=_rpc_error(
                    payload.id,
                    code=-32602,
                    message="Tool arguments must be a JSON object",
                )
            )

        try:
            result_payload, entity_type, entity_id = _dispatch_tool(
                tool_name,
                args,
                principal=principal,
                db=db,
                aimo_client=aimo_client,
                gateway=gateway,
            )
        except ValidationError as exc:
            db.rollback()
            record_audit(
                db,
                company_id=principal.company_id,
                actor=principal,
                action=f"mcp.{tool_name}",
                entity_type="mcp",
                entity_id=None,
                outcome="error",
                details={"code": "VALIDATION_ERROR", "message": str(exc)},
            )
            db.commit()
            return JSONResponse(
                content=_tool_error(
                    payload.id,
                    code="VALIDATION_ERROR",
                    status_code=422,
                    message="Tool argument validation failed",
                    details={"errors": exc.errors(include_url=False)},
                )
            )
        except AppError as exc:
            db.rollback()
            record_audit(
                db,
                company_id=principal.company_id,
                actor=principal,
                action=f"mcp.{tool_name}",
                entity_type="mcp",
                entity_id=None,
                outcome="error",
                details={"code": exc.code, "message": exc.message, "details": exc.details},
            )
            db.commit()
            return JSONResponse(
                content=_tool_error(
                    payload.id,
                    code=exc.code,
                    status_code=exc.status_code,
                    message=exc.message,
                    details=exc.details,
                )
            )

        record_audit(
            db,
            company_id=principal.company_id,
            actor=principal,
            action=f"mcp.{tool_name}",
            entity_type=entity_type,
            entity_id=entity_id,
            outcome="ok",
            details={"tool": tool_name},
        )
        db.commit()
        return JSONResponse(content=_tool_success(payload.id, result_payload))

    return JSONResponse(
        content=_rpc_error(
            payload.id,
            code=-32601,
            message=f"Method not found: {payload.method}",
        )
    )
