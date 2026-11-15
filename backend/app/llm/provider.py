"""LLM provider abstraction, hourly rate/cost guard, and Anthropic/Featherless/Fake backends."""

from __future__ import annotations

import json
import logging
import re
import threading
from collections import deque
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from app import clock
from app.config import settings
from app.errors import AppError
from app.ids import new_id
from app.llm.prep_list import default_delivery_date, parse_prep_list

logger = logging.getLogger(__name__)

MAX_ASSISTANT_TOKENS = 1024

_RATE_LOCK = threading.Lock()
_CALL_TIMESTAMPS: deque[datetime] = deque()

_QUOTE_ID_RE = re.compile(r"\b(qte_[0-9A-Za-z]+)\b")
_ORDER_ID_RE = re.compile(r"\b(ord_[0-9A-Za-z]+)\b")
_PRODUCT_ID_RE = re.compile(r"\b(\d{6})\b")
_BRANCH_TAG_PREFIX_RE = re.compile(r"^\s*\[Branch:\s*[^\]]+\]\s*", re.I)
_ORDER_INTENT_RE = re.compile(
    r"\b(order|buy|purchase|place\s+(?:(?:the|an|this)\s+)?order|place\s+it|confirm|"
    r"tilaa|vahvista)\b",
    re.I,
)
_FOLLOWUP_AFFIRM_RE = re.compile(
    r"^\s*(?:yes(?:\s*,?\s*please)?|yep|yeah|sure|ok(?:ay)?|do\s+it|send\s+it|book\s+it|"
    r"go\s+ahead|proceed|confirm(?:\s+(?:the\s+|this\s+)?order)?|"
    r"place\s+(?:(?:the|this)\s+)?order|place\s+it|order\s+(?:it|that|this)|"
    r"submit(?:\s+(?:the\s+|this\s+)?order)?|"
    r"(?:looks\s+good|sounds\s+good)[\s,!]*(?:place\s+(?:it|(?:the\s+)?order)|confirm)?|"
    r"kyllä(?:\s*,?\s*tilaa)?|joo|tilaa(?:\s+se)?|vahvista(?:\s+tilaus)?|"
    r"laita\s+tilaukseen)\s*[.!]*\s*$",
    re.I,
)
_CAPABILITY_PREFLIGHT_RE = re.compile(
    r"\b(preflight|pre-flight|what\s+are\s+your\s+capabilities|"
    r"what\s+(?:matters\s+|things\s+)?can\s+you\s+(?:handle|do|support)|"
    r"what\s+do\s+you\s+(?:handle|support)|supported\s+matters|session\s+capabilities)\b",
    re.I,
)
_RECENT_ORDERS_RE = re.compile(
    r"\b(my\s+orders?|recent\s+orders?|order\s+status(?:es)?|order\s+history|"
    r"check\s+order|status\s+of\s+order|ord_[0-9A-Za-z]+)\b",
    re.I,
)
_BULK_NEGOTIATE_QUOTE_RE = re.compile(
    r"\b(negotiate\s+(?:a\s+|the\s+)?(?:bulk\s+|volume\s+)?(?:price|pricing|quote|discount)|"
    r"(?:bulk|volume)\s+discount|\d+(?:\.\d+)?\s*%\s*discount)\b",
    re.I,
)
_QUOTE_INTENT_RE = re.compile(r"\b(quote|price|cost)\b", re.I)
_SUBSTITUTE_INTENT_RE = re.compile(r"\b(substitut\w*|replac\w*|alternativ\w*)\b", re.I)
_CHECK_INTENT_RE = re.compile(r"\b(check|availability|stock)\b", re.I)
_CHILLED_RE = re.compile(r"\b(chilled|fresh|cold|kylmä|tuore)\b", re.I)
_UHT_RE = re.compile(r"\b(uht|ambient|huoneenlämpö|iskukuumennettu)\b", re.I)
_DEALS_INTENT_RE = re.compile(r"\b(on[- ]sale|deals?|discounts?|offers?|tarjous)\b", re.I)
_PROMO_INTENT_RE = re.compile(r"\b(promotions?|management recommendations?|chef'?s choice)\b", re.I)
_MENU_INTENT_RE = re.compile(
    r"\b(plan (?:a )?menu|menu plan|valio recipes?|menu for \d+ pax)\b", re.I
)
_A2A_INTENT_RE = re.compile(r"\b(a2a|agent[- ]to[- ]agent|negotiate)\b", re.I)
_RECIPE_HINT_RE = re.compile(
    r"\b(rcp_[a-z0-9_]+|lohikeitto|salmon\s+soup|kermaperunat|smoothie|riisipuuro)\b",
    re.I,
)
_PAX_RE = re.compile(r"\b(\d+)\s*pax\b", re.I)
_MEAL_TYPE_RE = re.compile(r"\b(breakfast|lunch|dinner)\b", re.I)


class LLMToolCall(BaseModel):
    """Structured tool invocation emitted by an LLM provider."""

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class LLMMessage(BaseModel):
    """Normalized chat message exchanged with an LLM provider."""

    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[LLMToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None


class LLMToolSpec(BaseModel):
    """JSON Schema definition of an assistant tool."""

    name: str
    description: str
    input_schema: dict[str, Any]


class LLMResponse(BaseModel):
    """Normalized completion response from an LLM provider."""

    text: str = ""
    tool_calls: list[LLMToolCall] = Field(default_factory=list)
    stop_reason: str | None = None
    usage: dict[str, int] | None = None


@runtime_checkable
class LLMProvider(Protocol):
    """Protocol implemented by all Layer 2 / Assistant LLM providers."""

    def complete(
        self,
        messages: Sequence[LLMMessage],
        tools: Sequence[LLMToolSpec] | None = None,
        system: str | None = None,
        max_tokens: int = MAX_ASSISTANT_TOKENS,
    ) -> LLMResponse: ...


def reset_llm_call_counter() -> None:
    """Clear the global hourly LLM call window (intended for tests)."""
    with _RATE_LOCK:
        _CALL_TIMESTAMPS.clear()


def get_llm_call_count_last_hour() -> int:
    """Return the active number of LLM calls in the trailing 1-hour window."""
    now = clock.now()
    cutoff = now - timedelta(hours=1)
    with _RATE_LOCK:
        while _CALL_TIMESTAMPS and _CALL_TIMESTAMPS[0] <= cutoff:
            _CALL_TIMESTAMPS.popleft()
        return len(_CALL_TIMESTAMPS)


def enforce_llm_rate_limit() -> None:
    """Enforce settings.llm_max_calls_per_hour over a sliding 1-hour clock.now() window."""
    limit = int(settings.llm_max_calls_per_hour)
    now = clock.now()
    cutoff = now - timedelta(hours=1)
    with _RATE_LOCK:
        while _CALL_TIMESTAMPS and _CALL_TIMESTAMPS[0] <= cutoff:
            _CALL_TIMESTAMPS.popleft()
        if limit <= 0 or len(_CALL_TIMESTAMPS) >= limit:
            raise AppError(
                "LLM_UNAVAILABLE",
                503,
                "Hourly LLM call limit reached; please try again later",
                details={"limit_per_hour": limit, "calls_last_hour": len(_CALL_TIMESTAMPS)},
            )
        _CALL_TIMESTAMPS.append(now)


def _clamp_max_tokens(max_tokens: int) -> int:
    return min(max(1, int(max_tokens)), MAX_ASSISTANT_TOKENS)


class AnthropicProvider:
    """Assistant LLM provider backed by the official Anthropic Python SDK."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.api_key = (api_key if api_key is not None else settings.anthropic_api_key).strip()
        self.model = (model or settings.llm_model or "claude-sonnet-4-5").strip()
        self.timeout = timeout

    def _to_anthropic_messages(self, messages: Sequence[LLMMessage]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        pending_tool_results: list[dict[str, Any]] = []

        def _flush_tool_results() -> None:
            if pending_tool_results:
                out.append({"role": "user", "content": list(pending_tool_results)})
                pending_tool_results.clear()

        for msg in messages:
            if msg.role == "tool":
                pending_tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": msg.tool_call_id or "",
                        "content": msg.content,
                    }
                )
                continue

            _flush_tool_results()
            if msg.role == "user":
                out.append({"role": "user", "content": msg.content})
            elif msg.role == "assistant":
                if msg.tool_calls:
                    blocks: list[dict[str, Any]] = []
                    if msg.content:
                        blocks.append({"type": "text", "text": msg.content})
                    for tc in msg.tool_calls:
                        blocks.append(
                            {
                                "type": "tool_use",
                                "id": tc.id,
                                "name": tc.name,
                                "input": tc.arguments,
                            }
                        )
                    out.append({"role": "assistant", "content": blocks})
                else:
                    out.append({"role": "assistant", "content": msg.content})

        _flush_tool_results()
        return out

    def complete(
        self,
        messages: Sequence[LLMMessage],
        tools: Sequence[LLMToolSpec] | None = None,
        system: str | None = None,
        max_tokens: int = MAX_ASSISTANT_TOKENS,
    ) -> LLMResponse:
        enforce_llm_rate_limit()
        if not self.api_key:
            raise AppError("LLM_UNAVAILABLE", 503, "Anthropic API key is not configured")

        try:
            import anthropic

            client = anthropic.Anthropic(api_key=self.api_key, timeout=self.timeout)
            kwargs: dict[str, Any] = {
                "model": self.model,
                "max_tokens": _clamp_max_tokens(max_tokens),
                "messages": self._to_anthropic_messages(messages),
            }
            if system:
                kwargs["system"] = system
            if tools:
                kwargs["tools"] = [
                    {
                        "name": t.name,
                        "description": t.description,
                        "input_schema": t.input_schema,
                    }
                    for t in tools
                ]
            resp = client.messages.create(**kwargs)
        except AppError:
            raise
        except Exception as exc:
            logger.warning("AnthropicProvider completion failed: %s", exc)
            raise AppError(
                "LLM_UNAVAILABLE",
                503,
                "Anthropic LLM service is currently unavailable",
            ) from exc

        text_parts: list[str] = []
        tool_calls: list[LLMToolCall] = []
        for block in getattr(resp, "content", []) or []:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(str(getattr(block, "text", "")))
            elif btype == "tool_use":
                raw_input = getattr(block, "input", {}) or {}
                tool_calls.append(
                    LLMToolCall(
                        id=str(getattr(block, "id", new_id("tc_"))),
                        name=str(getattr(block, "name", "")),
                        arguments=dict(raw_input) if isinstance(raw_input, dict) else {},
                    )
                )

        usage_obj = getattr(resp, "usage", None)
        usage: dict[str, int] | None = None
        if usage_obj is not None:
            usage = {
                "input_tokens": int(getattr(usage_obj, "input_tokens", 0) or 0),
                "output_tokens": int(getattr(usage_obj, "output_tokens", 0) or 0),
            }

        return LLMResponse(
            text="\n".join(part for part in text_parts if part).strip(),
            tool_calls=tool_calls,
            stop_reason=getattr(resp, "stop_reason", None),
            usage=usage,
        )


class FeatherlessProvider:
    """Assistant LLM provider backed by Featherless OpenAI-compatible API."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str = "https://api.featherless.ai/v1",
        timeout: float = 20.0,
    ) -> None:
        self.api_key = (api_key if api_key is not None else settings.featherless_api_key).strip()
        self.model = (model or settings.llm_model).strip()
        self.base_url = base_url
        self.timeout = timeout

    def _to_openai_messages(
        self,
        messages: Sequence[LLMMessage],
        system: str | None,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if system:
            out.append({"role": "system", "content": system})
        for msg in messages:
            if msg.role == "user":
                out.append({"role": "user", "content": msg.content})
            elif msg.role == "assistant":
                if msg.tool_calls:
                    out.append(
                        {
                            "role": "assistant",
                            "content": msg.content or None,
                            "tool_calls": [
                                {
                                    "id": tc.id,
                                    "type": "function",
                                    "function": {
                                        "name": tc.name,
                                        "arguments": json.dumps(tc.arguments),
                                    },
                                }
                                for tc in msg.tool_calls
                            ],
                        }
                    )
                else:
                    out.append({"role": "assistant", "content": msg.content})
            elif msg.role == "tool":
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": msg.tool_call_id or "",
                        "content": msg.content,
                    }
                )
        return out

    def complete(
        self,
        messages: Sequence[LLMMessage],
        tools: Sequence[LLMToolSpec] | None = None,
        system: str | None = None,
        max_tokens: int = MAX_ASSISTANT_TOKENS,
    ) -> LLMResponse:
        enforce_llm_rate_limit()
        if not self.api_key:
            raise AppError("LLM_UNAVAILABLE", 503, "Featherless API key is not configured")

        try:
            import openai

            client = openai.OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout,
            )
            kwargs: dict[str, Any] = {
                "model": self.model,
                "max_tokens": _clamp_max_tokens(max_tokens),
                "messages": self._to_openai_messages(messages, system),
            }
            if tools:
                kwargs["tools"] = [
                    {
                        "type": "function",
                        "function": {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.input_schema,
                        },
                    }
                    for t in tools
                ]
            resp = client.chat.completions.create(**kwargs)
        except AppError:
            raise
        except Exception as exc:
            logger.warning("FeatherlessProvider completion failed: %s", exc)
            raise AppError(
                "LLM_UNAVAILABLE",
                503,
                "Featherless LLM service is currently unavailable",
            ) from exc

        choices = getattr(resp, "choices", None) or []
        if not choices:
            raise AppError("LLM_UNAVAILABLE", 503, "LLM returned no choices")

        choice = choices[0]
        message = choice.message
        text = str(getattr(message, "content", "") or "").strip()
        tool_calls: list[LLMToolCall] = []
        for tc in getattr(message, "tool_calls", None) or []:
            fn = getattr(tc, "function", None)
            raw_args = getattr(fn, "arguments", "{}") or "{}"
            try:
                parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except (json.JSONDecodeError, TypeError):
                parsed_args = {}
            tool_calls.append(
                LLMToolCall(
                    id=str(getattr(tc, "id", new_id("tc_"))),
                    name=str(getattr(fn, "name", "")),
                    arguments=parsed_args if isinstance(parsed_args, dict) else {},
                )
            )

        usage_obj = getattr(resp, "usage", None)
        usage: dict[str, int] | None = None
        if usage_obj is not None:
            usage = {
                "input_tokens": int(getattr(usage_obj, "prompt_tokens", 0) or 0),
                "output_tokens": int(getattr(usage_obj, "completion_tokens", 0) or 0),
            }

        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason=getattr(choice, "finish_reason", None),
            usage=usage,
        )


def _safe_json_loads(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


class FakeProvider:
    """Deterministic LLM provider with optional scripted response queue for tests."""

    def __init__(self, script: Sequence[LLMResponse] | None = None) -> None:
        self._script: deque[LLMResponse] = deque(script or [])

    def queue_response(self, response: LLMResponse) -> None:
        """Append a scripted response to the queue."""
        self._script.append(response)

    def complete(
        self,
        messages: Sequence[LLMMessage],
        tools: Sequence[LLMToolSpec] | None = None,
        system: str | None = None,
        max_tokens: int = MAX_ASSISTANT_TOKENS,
    ) -> LLMResponse:
        enforce_llm_rate_limit()
        if self._script:
            return self._script.popleft()
        return self._deterministic_complete(messages, tools or [], system=system)

    def _deterministic_complete(
        self,
        messages: Sequence[LLMMessage],
        tools: Sequence[LLMToolSpec],
        *,
        system: str | None = None,
    ) -> LLMResponse:
        last_user_idx = -1
        for idx, msg in enumerate(messages):
            if msg.role == "user":
                last_user_idx = idx
        if last_user_idx == -1:
            return LLMResponse(
                text="How can I help with your foodservice order?",
                stop_reason="stop",
            )

        user_text = messages[last_user_idx].content.strip()
        prior_msgs = list(messages[:last_user_idx])
        turn_tools = [m for m in messages[last_user_idx + 1 :] if m.role == "tool"]

        if not turn_tools:
            return self._handle_step_zero(user_text, prior_msgs, tools=tools, system=system)
        return self._handle_post_tools(user_text, prior_msgs, turn_tools)

    def _extract_prior_context(
        self,
        prior_msgs: Sequence[LLMMessage],
        *,
        system: str | None = None,
    ) -> dict[str, Any]:
        latest_quote_id: str | None = None
        last_user_text = ""
        last_assistant_text = ""
        candidate_ids: list[str] = []

        for msg in prior_msgs:
            if msg.role == "user":
                last_user_text = _BRANCH_TAG_PREFIX_RE.sub("", msg.content.strip())
            elif msg.role == "assistant":
                last_assistant_text = msg.content
            q_matches = _QUOTE_ID_RE.findall(msg.content)
            if q_matches:
                latest_quote_id = q_matches[-1]
            for pid in _PRODUCT_ID_RE.findall(msg.content):
                if pid not in candidate_ids:
                    candidate_ids.append(pid)

        if latest_quote_id is None and system:
            sys_q = re.search(r"\[OPEN_QUOTE_ID:\s*(qte_[0-9A-Za-z]+)\]", system)
            if sys_q:
                latest_quote_id = sys_q.group(1)

        parsed_prior = parse_prep_list(last_user_text) if last_user_text else None
        return {
            "latest_quote_id": latest_quote_id,
            "last_user_text": last_user_text,
            "last_assistant_text": last_assistant_text,
            "candidate_ids": candidate_ids,
            "parsed_prior": parsed_prior,
        }

    def _handle_step_zero(
        self,
        user_text: str,
        prior_msgs: Sequence[LLMMessage],
        *,
        tools: Sequence[LLMToolSpec] = (),
        system: str | None = None,
    ) -> LLMResponse:
        user_text = _BRANCH_TAG_PREFIX_RE.sub("", user_text.strip())
        ctx = self._extract_prior_context(prior_msgs, system=system)
        parsed = parse_prep_list(user_text)
        lowered = user_text.lower()
        tool_names = {t.name for t in tools}

        # 0. Preflight / "what are your capabilities?" check
        bare_word = lowered.strip().rstrip("?.!")
        if (
            not _PRODUCT_ID_RE.search(user_text)
            and not re.search(r"\b\d+\b", user_text)
            and (
                bool(_CAPABILITY_PREFLIGHT_RE.search(user_text))
                or bare_word in {"capabilities", "preflight", "pre-flight"}
            )
        ):
            sys_txt = system or ""
            comp_m = re.search(r"\[COMPANY_NAME:\s*([^\]]+)\]", sys_txt)
            comp_name = comp_m.group(1).strip() if comp_m else "your company"
            neg_enabled = "[NEGOTIATION_STATUS: ENABLED]" in sys_txt
            neg_line = (
                f"- Volume price negotiation: ENABLED for {comp_name} in the Admin panel (/admin) "
                "— I can negotiate prices for larger quantities and multi-line bundle orders."
                if neg_enabled
                else (
                    "- Volume price negotiation for larger quantities: currently DISABLED for "
                    f"{comp_name} (managed per company in the Admin panel /admin)."
                )
            )
            return LLMResponse(
                text=(
                    f"Connected to the Aimo Annos Purchasing Gateway for {comp_name}. "
                    "Capabilities:\n"
                    "- Diet-safe catalog search, substitutes & live CO2/delivery checks "
                    "(L, VL, M, G, VEG, HEART)\n"
                    "- Nordic menu planning for N pax & live on-sale deals\n"
                    "- Locked 30-minute quotes, Order Assurance, governed order placement, "
                    "and pre-cutoff Undo\n"
                    f"{neg_line}"
                ),
                stop_reason="stop",
            )

        # 1. Follow-up "place the order" / "yes" / "tilaa" on an existing open quote
        if (
            ctx["latest_quote_id"]
            and (_ORDER_INTENT_RE.search(user_text) or _FOLLOWUP_AFFIRM_RE.match(user_text))
            and not _PRODUCT_ID_RE.search(user_text)
            and not any(it.quantity is not None for it in parsed.items)
            and not _SUBSTITUTE_INTENT_RE.search(user_text)
            and not _CHECK_INTENT_RE.search(user_text)
        ):
            diet_str = ",".join(parsed.global_diets) if parsed.global_diets else None
            args: dict[str, Any] = {"quote_id": ctx["latest_quote_id"]}
            if diet_str:
                args["diet"] = diet_str
            return LLMResponse(
                tool_calls=[LLMToolCall(id=new_id("tc_"), name="place_order", arguments=args)],
                stop_reason="tool_use",
            )

        # 1b. Order status / recent orders lookup
        if (
            "list_recent_orders" in tool_names
            and _RECENT_ORDERS_RE.search(user_text)
            and not _PRODUCT_ID_RE.search(user_text)
        ):
            ord_match = _ORDER_ID_RE.search(user_text)
            ord_args: dict[str, Any] = {"limit": 5}
            if ord_match:
                ord_args["order_id"] = ord_match.group(1)
            return LLMResponse(
                tool_calls=[
                    LLMToolCall(id=new_id("tc_"), name="list_recent_orders", arguments=ord_args)
                ],
                stop_reason="tool_use",
            )

        # 2. Follow-up clarification choice ("chilled", "uht", "switch", "split", or product ID)
        is_followup_choice = bool(
            prior_msgs
            and (
                _CHILLED_RE.search(user_text)
                or _UHT_RE.search(user_text)
                or "switch" in lowered
                or "split" in lowered
                or (
                    len(user_text.split()) <= 4
                    and _PRODUCT_ID_RE.search(user_text)
                    and not _CHECK_INTENT_RE.search(user_text)
                )
            )
        )
        if is_followup_choice and ctx["parsed_prior"] is not None:
            prior_parsed = ctx["parsed_prior"]
            delivery_date = (
                parsed.requested_delivery_date
                or prior_parsed.requested_delivery_date
                or default_delivery_date()
            )
            qty = 1.0
            if parsed.items and parsed.items[0].quantity:
                qty = parsed.items[0].quantity
            elif prior_parsed.items and prior_parsed.items[0].quantity:
                qty = prior_parsed.items[0].quantity

            diets = parsed.global_diets or prior_parsed.global_diets
            diet_str = ",".join(diets) if diets else None

            explicit_pids = _PRODUCT_ID_RE.findall(user_text)
            chosen_pid: str | None = explicit_pids[0] if explicit_pids else None
            if chosen_pid is None:
                # Match option in assistant's prior clarification text
                for line in ctx["last_assistant_text"].splitlines():
                    pids = _PRODUCT_ID_RE.findall(line)
                    if not pids:
                        continue
                    if _CHILLED_RE.search(user_text) and _CHILLED_RE.search(line):
                        chosen_pid = pids[0]
                        break
                    if _UHT_RE.search(user_text) and _UHT_RE.search(line):
                        chosen_pid = pids[0]
                        break
            if chosen_pid is None:
                if _CHILLED_RE.search(user_text) or "switch" in lowered:
                    chosen_pid = "464306"
                elif _UHT_RE.search(user_text):
                    chosen_pid = "464398"
                elif "split" in lowered:
                    chosen_pid = "469363"
                    qty = min(qty, 384.0)
                elif ctx["candidate_ids"]:
                    chosen_pid = ctx["candidate_ids"][0]

            if chosen_pid:
                quote_args: dict[str, Any] = {
                    "requested_delivery_date": delivery_date,
                    "lines": [{"product_id": chosen_pid, "quantity": float(qty)}],
                }
                if diet_str:
                    quote_args["diet"] = diet_str
                return LLMResponse(
                    tool_calls=[
                        LLMToolCall(id=new_id("tc_"), name="create_quote", arguments=quote_args)
                    ],
                    stop_reason="tool_use",
                )

        # 2a. Bulk volume quote negotiation ("negotiate price", "5% discount") vs A2A negotiation
        if (
            "negotiate_quote" in tool_names
            and _BULK_NEGOTIATE_QUOTE_RE.search(user_text)
            and "via a2a" not in lowered
            and not _RECIPE_HINT_RE.search(user_text)
            and bool(_PRODUCT_ID_RE.findall(user_text))
        ):
            explicit_pids = _PRODUCT_ID_RE.findall(user_text)
            clean_for_qty = re.sub(r"\b\d+(?:[.,]\d+)?\s*%", " ", user_text)
            neg_parsed = parse_prep_list(clean_for_qty)
            default_qty = (
                float(neg_parsed.items[0].quantity)
                if neg_parsed.items and neg_parsed.items[0].quantity
                else 50.0
            )
            neg_lines: list[dict[str, Any]] = []
            for pid in explicit_pids:
                per_sku_m = re.search(
                    rf"\b(\d+(?:[.,]\d+)?)\s*(?:x|×|units?|pcs|pks|l|kg)?\s*(?:of\s+)?{re.escape(pid)}\b",
                    clean_for_qty,
                    re.I,
                )
                sku_qty = (
                    float(per_sku_m.group(1).replace(",", "."))
                    if per_sku_m
                    else default_qty
                )
                neg_lines.append({"product_id": pid, "quantity": sku_qty})
            pct_m = re.search(r"(\d+(?:\.\d+)?)\s*%", user_text)
            neg_q_args: dict[str, Any] = {
                "requested_delivery_date": (
                    parsed.requested_delivery_date or default_delivery_date()
                ),
                "lines": neg_lines,
                "justification": user_text[:200],
            }
            if parsed.global_diets:
                neg_q_args["diet"] = ",".join(parsed.global_diets)
            if pct_m:
                neg_q_args["target_discount_pct"] = float(pct_m.group(1))
            return LLMResponse(
                tool_calls=[
                    LLMToolCall(id=new_id("tc_"), name="negotiate_quote", arguments=neg_q_args)
                ],
                stop_reason="tool_use",
            )

        # 2a-bis. Mobile /assistant A2A negotiation parity ("a2a", "agent-to-agent", "negotiate")
        if _A2A_INTENT_RE.search(user_text):
            explicit_pids = _PRODUCT_ID_RE.findall(user_text)
            pid = explicit_pids[0] if explicit_pids else "464306"
            pax_match = _PAX_RE.search(user_text)
            recipe_match = _RECIPE_HINT_RE.search(user_text)
            recipe_id = recipe_match.group(1).lower() if recipe_match else None
            target_pax = int(pax_match.group(1)) if pax_match else (60 if recipe_id else None)
            qty = (
                float(parsed.items[0].quantity)
                if parsed.items and parsed.items[0].quantity
                else (120.0 if recipe_id else 10.0)
            )
            a2a_args: dict[str, Any] = {
                "product_id": pid,
                "quantity": qty,
                "requested_delivery_date": (
                    parsed.requested_delivery_date or default_delivery_date()
                ),
                "note": user_text[:200],
            }
            if parsed.global_diets:
                a2a_args["diet"] = ",".join(parsed.global_diets)
            if recipe_id:
                a2a_args["recipe_id"] = recipe_id
            if target_pax is not None:
                a2a_args["target_pax"] = target_pax
            return LLMResponse(
                tool_calls=[
                    LLMToolCall(id=new_id("tc_"), name="negotiate_a2a_order", arguments=a2a_args)
                ],
                stop_reason="tool_use",
            )

        # 2b. Menu planner intent ("plan a menu", "menu for 25 pax", "valio recipes")
        if _MENU_INTENT_RE.search(user_text):
            pax_match = _PAX_RE.search(user_text)
            pax = int(pax_match.group(1)) if pax_match else 20
            meal_match = _MEAL_TYPE_RE.search(user_text)
            menu_args: dict[str, Any] = {
                "pax": max(1, min(pax, 5000)),
                "prefer_on_sale": True,
                "include_valio_recipes": True,
                "requested_delivery_date": (
                    parsed.requested_delivery_date or default_delivery_date()
                ),
            }
            if meal_match:
                menu_args["meal_type"] = meal_match.group(1).lower()
            if parsed.global_diets:
                menu_args["diet"] = ",".join(parsed.global_diets)
            if parsed.max_budget_eur is not None:
                menu_args["max_budget_eur"] = parsed.max_budget_eur
            return LLMResponse(
                tool_calls=[LLMToolCall(id=new_id("tc_"), name="plan_menu", arguments=menu_args)],
                stop_reason="tool_use",
            )

        # 2c. On-sale deals discovery intent ("on sale", "deals", "discounts")
        if _DEALS_INTENT_RE.search(user_text):
            deals_args: dict[str, Any] = {
                "on_sale_only": True,
                "valio_only": "valio" in lowered,
                "limit": 10,
                "requested_delivery_date": (
                    parsed.requested_delivery_date or default_delivery_date()
                ),
            }
            if parsed.global_diets:
                deals_args["diet"] = ",".join(parsed.global_diets)
            return LLMResponse(
                tool_calls=[
                    LLMToolCall(
                        id=new_id("tc_"), name="find_on_sale_products", arguments=deals_args
                    )
                ],
                stop_reason="tool_use",
            )

        # 2d. Management promotions listing intent
        if _PROMO_INTENT_RE.search(user_text):
            return LLMResponse(
                tool_calls=[
                    LLMToolCall(
                        id=new_id("tc_"),
                        name="list_promotions",
                        arguments={"active_only": True},
                    )
                ],
                stop_reason="tool_use",
            )

        # 3. Substitute query
        if _SUBSTITUTE_INTENT_RE.search(user_text):
            pids = _PRODUCT_ID_RE.findall(user_text)
            if pids:
                qty = parsed.items[0].quantity if parsed.items and parsed.items[0].quantity else 1.0
                delivery_date = parsed.requested_delivery_date or default_delivery_date()
                sub_args: dict[str, Any] = {
                    "product_id": pids[0],
                    "quantity": float(qty),
                    "requested_delivery_date": delivery_date,
                }
                if parsed.global_diets:
                    sub_args["diet"] = ",".join(parsed.global_diets)
                return LLMResponse(
                    tool_calls=[
                        LLMToolCall(
                            id=new_id("tc_"), name="suggest_substitutes", arguments=sub_args
                        )
                    ],
                    stop_reason="tool_use",
                )

        # 4. Explicit product ID check / quote / order
        explicit_pids = _PRODUCT_ID_RE.findall(user_text)
        if explicit_pids:
            delivery_date = parsed.requested_delivery_date or default_delivery_date()
            diet_str = ",".join(parsed.global_diets) if parsed.global_diets else None
            if _CHECK_INTENT_RE.search(user_text) and not _ORDER_INTENT_RE.search(user_text):
                quantities = {
                    pid: float(
                        parsed.items[0].quantity
                        if parsed.items and parsed.items[0].quantity
                        else 1.0
                    )
                    for pid in explicit_pids
                }
                check_args: dict[str, Any] = {
                    "product_ids": explicit_pids,
                    "quantities": quantities,
                    "requested_delivery_date": delivery_date,
                }
                if diet_str:
                    check_args["diet"] = diet_str
                return LLMResponse(
                    tool_calls=[
                        LLMToolCall(id=new_id("tc_"), name="check_products", arguments=check_args)
                    ],
                    stop_reason="tool_use",
                )

            if (
                _QUOTE_INTENT_RE.search(user_text)
                or _ORDER_INTENT_RE.search(user_text)
                or (parsed.items and parsed.items[0].quantity)
            ):
                qty = (
                    float(parsed.items[0].quantity)
                    if parsed.items and parsed.items[0].quantity
                    else 1.0
                )
                quote_args = {
                    "requested_delivery_date": delivery_date,
                    "lines": [{"product_id": pid, "quantity": qty} for pid in explicit_pids],
                }
                if diet_str:
                    quote_args["diet"] = diet_str
                recipe_match = _RECIPE_HINT_RE.search(user_text)
                pax_match = _PAX_RE.search(user_text)
                if recipe_match:
                    quote_args["recipe_id"] = recipe_match.group(1).lower()
                if pax_match:
                    quote_args["target_pax"] = int(pax_match.group(1))
                return LLMResponse(
                    tool_calls=[
                        LLMToolCall(id=new_id("tc_"), name="create_quote", arguments=quote_args)
                    ],
                    stop_reason="tool_use",
                )

        # 5. Prep-list / natural-language catalog search
        calls: list[LLMToolCall] = []
        for item in parsed.items[:4]:
            search_args: dict[str, Any] = {"limit": 10}
            if item.query:
                search_args["q"] = item.query
            if item.diets:
                search_args["diet"] = ",".join(item.diets)
            elif parsed.global_diets:
                search_args["diet"] = ",".join(parsed.global_diets)
            if not search_args.get("q") and not search_args.get("diet"):
                search_args["q"] = "milk"
            calls.append(
                LLMToolCall(id=new_id("tc_"), name="search_products", arguments=search_args)
            )

        if not calls:
            calls.append(
                LLMToolCall(
                    id=new_id("tc_"),
                    name="search_products",
                    arguments={"q": user_text[:60], "limit": 10},
                )
            )

        return LLMResponse(tool_calls=calls, stop_reason="tool_use")

    def _handle_post_tools(
        self,
        user_text: str,
        prior_msgs: Sequence[LLMMessage],
        turn_tools: Sequence[LLMMessage],
    ) -> LLMResponse:
        user_text = _BRANCH_TAG_PREFIX_RE.sub("", user_text.strip())
        parsed = parse_prep_list(user_text)
        latest = turn_tools[-1]
        latest_data = _safe_json_loads(latest.content)
        wants_order = bool(_ORDER_INTENT_RE.search(user_text))
        wants_quote = bool(_QUOTE_INTENT_RE.search(user_text))
        is_injection_override = bool(re.search(r"\b(ignore|bypass|override)\b", user_text, re.I))

        if latest.name == "list_recent_orders":
            if isinstance(latest_data, dict) and "error" in latest_data:
                err = latest_data["error"]
                return LLMResponse(
                    text=f"Could not fetch order status ({err.get('code')}): {err.get('message')}.",
                    stop_reason="stop",
                )
            orders_list = (latest_data or {}).get("items") or []
            if not orders_list:
                return LLMResponse(
                    text="No orders found yet for this account.",
                    stop_reason="stop",
                )
            rows_txt = [
                f"- {o.get('id')}: {o.get('status')} · €{o.get('total_excl_vat_eur', 0):.2f} "
                f"excl. VAT · delivery {o.get('requested_delivery_date')}"
                + (
                    f" · gateway {o.get('gateway_reference')}"
                    if o.get("gateway_reference")
                    else ""
                )
                for o in orders_list[:5]
            ]
            return LLMResponse(
                text="Recent orders:\n" + "\n".join(rows_txt),
                stop_reason="stop",
            )

        if latest.name == "negotiate_quote":
            if isinstance(latest_data, dict) and "error" in latest_data:
                err = latest_data["error"]
                return LLMResponse(
                    text=(
                        f"Bulk price negotiation unavailable ({err.get('code')}): "
                        f"{err.get('message')}"
                    ),
                    stop_reason="stop",
                )
            neg = latest_data if isinstance(latest_data, dict) else {}
            quote = neg.get("quote") or {}
            policy = quote.get("policy_preview") or {}
            return LLMResponse(
                text=(
                    f"Negotiated quote {quote.get('id')} ({neg.get('status')}): "
                    f"€{neg.get('negotiated_total_eur', 0):.2f} excl. VAT "
                    f"(saved €{neg.get('savings_eur', 0):.2f} / -{neg.get('savings_pct', 0):.2f}% "
                    f"vs list €{neg.get('list_total_eur', 0):.2f}; "
                    f"policy: {policy.get('outcome')}). "
                    "Would you like me to place this order?"
                ),
                stop_reason="stop",
            )

        if latest.name == "negotiate_a2a_order":
            if isinstance(latest_data, dict) and "error" in latest_data:
                err = latest_data["error"]
                return LLMResponse(
                    text=(
                        f"A2A negotiation halted by governance policy ({err.get('code')}): "
                        f"{err.get('message')}."
                    ),
                    stop_reason="stop",
                )
            order = (latest_data or {}).get("order") or {}
            policy = order.get("policy") or {}
            warning = order.get("recipe_sanity_warning")
            warn_txt = f" Recipe sanity alert: {warning.get('message')}" if warning else ""
            return LLMResponse(
                text=(
                    f"A2A negotiation complete: order {order.get('id')} is {order.get('status')} "
                    f"(€{order.get('total_excl_vat_eur', 0):.2f} excl. VAT, "
                    f"policy: {policy.get('outcome')}).{warn_txt}"
                ),
                stop_reason="stop",
            )

        if latest.name == "place_order":
            if isinstance(latest_data, dict) and latest_data.get("deferred"):
                return LLMResponse(
                    text=str(
                        latest_data.get("message")
                        or "Quote locked — please confirm explicitly to place the order."
                    ),
                    stop_reason="stop",
                )
            if isinstance(latest_data, dict) and "error" in latest_data:
                err = latest_data["error"]
                rules = (err.get("details") or {}).get("rules") or []
                rule_summary = "; ".join(f"{r.get('rule_id')}: {r.get('message')}" for r in rules)
                return LLMResponse(
                    text=(
                        f"Order blocked by purchasing policy ({err.get('code')}): "
                        f"{err.get('message')}."
                        + (f" Fired rules: {rule_summary}." if rule_summary else "")
                    ),
                    stop_reason="stop",
                )
            order = latest_data if isinstance(latest_data, dict) else {}
            policy = order.get("policy") or {}
            lines = order.get("lines") or []
            co2_bases = [ln.get("co2_basis") for ln in lines if ln.get("co2_basis")]
            basis_txt = f" CO2 basis: {co2_bases[0]}." if co2_bases else ""
            return LLMResponse(
                text=(
                    f"Order {order.get('id')} created with status {order.get('status')} "
                    f"(total €{order.get('total_excl_vat_eur', 0):.2f} excl. VAT, "
                    f"policy outcome: {policy.get('outcome')})."
                    + (
                        f" Approval content_hash: {order.get('content_hash')}."
                        if order.get("status") == "PENDING_APPROVAL"
                        else ""
                    )
                    + basis_txt
                ),
                stop_reason="stop",
            )

        if latest.name == "create_quote":
            if isinstance(latest_data, dict) and "error" in latest_data:
                err = latest_data["error"]
                return LLMResponse(
                    text=f"Could not create quote ({err.get('code')}): {err.get('message')}.",
                    stop_reason="stop",
                )
            quote = latest_data if isinstance(latest_data, dict) else {}
            quote_id = quote.get("id")
            if wants_order and quote_id:
                order_args: dict[str, Any] = {"quote_id": quote_id, "note": user_text[:200]}
                if parsed.global_diets:
                    order_args["diet"] = ",".join(parsed.global_diets)
                return LLMResponse(
                    tool_calls=[
                        LLMToolCall(id=new_id("tc_"), name="place_order", arguments=order_args)
                    ],
                    stop_reason="tool_use",
                )

            policy = quote.get("policy_preview") or {}
            lines = quote.get("lines") or []
            co2_bases = [ln.get("co2_basis") for ln in lines if ln.get("co2_basis")]
            basis_txt = f" CO2 basis: {co2_bases[0]}." if co2_bases else ""
            return LLMResponse(
                text=(
                    f"Quote {quote_id} created for {quote.get('requested_delivery_date')}: "
                    f"total €{quote.get('total_excl_vat_eur', 0):.2f} excl. VAT "
                    f"(€{quote.get('total_incl_vat_eur', 0):.2f} incl. VAT), "
                    f"delivery {quote.get('overall_delivery_status')}, "
                    f"policy outcome: {policy.get('outcome')}.{basis_txt} "
                    "Would you like me to place this order?"
                ),
                stop_reason="stop",
            )

        if latest.name == "plan_menu":
            plan = latest_data if isinstance(latest_data, dict) else {}
            if "error" in plan:
                err = plan["error"]
                return LLMResponse(
                    text=f"Could not plan menu ({err.get('code')}): {err.get('message')}.",
                    stop_reason="stop",
                )
            recipes = [r.get("name") for r in (plan.get("recipes") or []) if r.get("name")]
            items = plan.get("items") or []
            policy = plan.get("policy_preview") or {}
            return LLMResponse(
                text=(
                    f"Planned {plan.get('meal_type') or 'mixed'} menu {plan.get('plan_id')} "
                    f"for {plan.get('pax')} pax ({len(items)} ingredient lines; "
                    f"recipes: {', '.join(recipes) or 'curated'}). "
                    f"Estimated total €{plan.get('estimated_total_excl_vat_eur', 0):.2f} excl. VAT "
                    f"(savings €{plan.get('estimated_savings_eur', 0):.2f}), "
                    f"policy outcome: {policy.get('outcome')}."
                ),
                stop_reason="stop",
            )

        if latest.name == "find_on_sale_products":
            deals = latest_data if isinstance(latest_data, dict) else {}
            items = deals.get("items") or []
            if not items:
                return LLMResponse(
                    text="No matching on-sale items were found for the requested filters.",
                    stop_reason="stop",
                )
            summary = "; ".join(
                f"{it['product']['id']} ({it['product']['name']}): "
                f"-{it.get('discount_pct', 0):.1f}% off "
                f"(€{it['quote_line'].get('unit_price_eur')} vs "
                f"list €{it.get('list_price_ex_vat')}, "
                f"{it['quote_line'].get('delivery_status')})"
                for it in items[:5]
            )
            promos = deals.get("promotions_applied") or []
            promo_txt = f" Promotions applied: {', '.join(promos)}." if promos else ""
            return LLMResponse(
                text=f"On-sale deals: {summary}.{promo_txt}",
                stop_reason="stop",
            )

        if latest.name == "list_promotions":
            promos = (latest_data or {}).get("items") or []
            if not promos:
                return LLMResponse(
                    text="No active management promotions are configured.",
                    stop_reason="stop",
                )
            summary = "; ".join(f"{p.get('title')} (boost={p.get('boost_weight')})" for p in promos)
            return LLMResponse(
                text=f"Active management promotions: {summary}.",
                stop_reason="stop",
            )

        if latest.name == "suggest_substitutes":
            items = (latest_data or {}).get("items") or []
            if not items:
                return LLMResponse(
                    text="No diet-safe, delivery-feasible substitutes were found.",
                    stop_reason="stop",
                )
            summaries = [
                f"{it['product']['id']} ({it['product']['name']}, "
                f"€{it['quote_line'].get('unit_price_eur')}/{it['product']['sales_unit']}, "
                f"{it['quote_line'].get('delivery_status')})"
                for it in items[:3]
            ]
            return LLMResponse(
                text="Feasible substitutes: " + "; ".join(summaries) + ".",
                stop_reason="stop",
            )

        if latest.name == "check_products":
            items = (latest_data or {}).get("items") or []
            warnings = (latest_data or {}).get("ingredient_warnings") or []
            if (latest_data or {}).get("clarification_needed"):
                opts = (latest_data or {}).get("clarification_options") or []
                return LLMResponse(
                    text="Stock is only partially available. Please choose: " + "; ".join(opts),
                    stop_reason="stop",
                )
            if parsed.max_budget_eur is not None:
                affordable = [
                    it
                    for it in items
                    if it.get("unit_price_eur") is not None
                    and float(it["unit_price_eur"]) <= parsed.max_budget_eur
                ]
                if not affordable:
                    return LLMResponse(
                        text=f"No checked products are within €{parsed.max_budget_eur:.2f}.",
                        stop_reason="stop",
                    )
                summary = "; ".join(
                    f"{it['product_id']} ({it['product_name']}) €{it['unit_price_eur']:.2f}/"
                    f"{it['sales_unit']} (CO2 basis: {it.get('co2_basis')})"
                    for it in affordable[:5]
                )
                return LLMResponse(
                    text=f"Options under €{parsed.max_budget_eur:.2f}: {summary}.",
                    stop_reason="stop",
                )
            summary = "; ".join(
                f"{it['product_id']} ({it['product_name']}): €{it.get('unit_price_eur')}, "
                f"{it.get('delivery_status')} (CO2 basis: {it.get('co2_basis')})"
                for it in items
            )
            warn_txt = f" Warnings: {'; '.join(warnings)}." if warnings else ""
            return LLMResponse(
                text=f"Live check results: {summary}.{warn_txt}",
                stop_reason="stop",
            )

        # Latest tool(s) are search_products
        search_results = [
            _safe_json_loads(m.content) for m in turn_tools if m.name == "search_products"
        ]
        all_products: list[dict[str, Any]] = []
        clarification_opts: list[str] = []
        for res in search_results:
            if not isinstance(res, dict):
                continue
            all_products.extend(res.get("products") or [])
            if res.get("clarification_needed") and not clarification_opts:
                clarification_opts = list(res.get("clarification_options") or [])

        if parsed.max_budget_eur is not None and all_products:
            pids = [str(p["id"]) for p in all_products[:10]]
            delivery_date = parsed.requested_delivery_date or default_delivery_date()
            return LLMResponse(
                tool_calls=[
                    LLMToolCall(
                        id=new_id("tc_"),
                        name="check_products",
                        arguments={
                            "product_ids": pids,
                            "quantities": {pid: 1.0 for pid in pids},
                            "requested_delivery_date": delivery_date,
                        },
                    )
                ],
                stop_reason="tool_use",
            )

        has_qty = any(it.quantity is not None for it in parsed.items)
        # Single-item quote/order request with ambiguity -> ask clarification first
        if (
            clarification_opts
            and len(search_results) == 1
            and (has_qty or wants_quote or wants_order)
            and not is_injection_override
            and not _CHILLED_RE.search(user_text)
            and not _UHT_RE.search(user_text)
        ):
            opts_text = "\n".join(f"- {opt}" for opt in clarification_opts)
            return LLMResponse(
                text=(
                    "Multiple storage/unit options are available. "
                    f"Which option do you prefer?\n{opts_text}"
                ),
                stop_reason="stop",
            )

        # Direct quote/order if single clear top candidate + quantity or injection override
        if (
            all_products
            and (has_qty or wants_quote or wants_order)
            and (len(search_results) == 1 and (not clarification_opts or is_injection_override))
        ):
            qty = (
                float(parsed.items[0].quantity)
                if parsed.items and parsed.items[0].quantity
                else 1.0
            )
            delivery_date = parsed.requested_delivery_date or default_delivery_date()
            diet_str = ",".join(parsed.global_diets) if parsed.global_diets else None
            quote_args: dict[str, Any] = {
                "requested_delivery_date": delivery_date,
                "lines": [{"product_id": str(all_products[0]["id"]), "quantity": qty}],
            }
            if diet_str:
                quote_args["diet"] = diet_str
            return LLMResponse(
                tool_calls=[
                    LLMToolCall(id=new_id("tc_"), name="create_quote", arguments=quote_args)
                ],
                stop_reason="tool_use",
            )

        if not all_products:
            return LLMResponse(
                text="No matching diet-safe products were found in the catalog.",
                stop_reason="stop",
            )

        lines_out: list[str] = []
        for idx, res in enumerate(search_results):
            prods = (res or {}).get("products") or []
            item_ctx = parsed.items[idx] if idx < len(parsed.items) else None
            tag_label = f"[{','.join(item_ctx.diets)}] " if item_ctx and item_ctx.diets else ""
            qty_label = (
                f"{item_ctx.quantity:g} {item_ctx.unit or ''} ".strip() + " "
                if item_ctx and item_ctx.quantity
                else ""
            )
            top_names = ", ".join(f"{p['id']} ({p['name']})" for p in prods[:3]) or "no matches"
            q_label = item_ctx.query if item_ctx else ""
            lines_out.append(f"- {qty_label}{tag_label}{q_label}: {top_names}")

        extra = ""
        if clarification_opts:
            extra = " Clarification needed on options: " + "; ".join(clarification_opts) + "."
        return LLMResponse(
            text="Found matching products:\n" + "\n".join(lines_out) + extra,
            stop_reason="stop",
        )


def get_llm_provider() -> LLMProvider:
    """FastAPI dependency returning the configured LLMProvider."""
    if settings.llm_provider == "anthropic":
        return AnthropicProvider()
    if settings.llm_provider == "featherless":
        return FeatherlessProvider()
    return FakeProvider()
