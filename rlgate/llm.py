"""Model access for the LLM layers (L2 static review, L3 agentic audits, L4 hack agent).

Every call goes through LiteLLM, so any provider works with a ``provider/model`` name —
``anthropic/claude-opus-5-5``, ``openai/<model>``, ``gemini/<model>``, ``ollama/<model>``,
``bedrock/…``, ``vertex_ai/…``, ``openrouter/<maker>/<model>`` — and the provider's own key
variable (``ANTHROPIC_API_KEY``, ``OPENAI_API_KEY``, ``OPENROUTER_API_KEY``, …), as in Harbor.
Models LiteLLM has no entry for (often the case behind OpenRouter) get their prices and
limits from ``[models.info."<model>"]`` (LiteLLM ``model_info`` keys, registered with
``litellm.register_model``, the way Harbor's ``model_info`` does). Install with
``pip install 'rlgate[llm]'``.

Robustness rules, because a judge verdict feeds a keep/drop decision:

  - **Fail closed.** Every failure raises ``LLMError`` with a ``kind``; a caller must treat
    any error as "could not judge", never as a pass. No silent fallbacks to defaults.
  - **Structured output is validated.** With a JSON schema, the reply is parsed and checked
    against it; one repair round-trip is attempted, then ``invalid_output``. Providers that
    reject native structured output get the schema in the prompt instead (same validation).
  - **No partial answers.** A reply cut off at ``max_tokens`` is ``truncated``, never parsed.
  - **Bounded cost.** Spend is tracked per client; an optional ``budget_usd`` stops calls
    once reached (``budget``).
  - **Retries** for rate limits / timeouts / 5xx are LiteLLM's (``num_retries``); auth,
    bad-request and not-found errors are not retried.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from rlgate.config import load_dotenv


class LLMError(RuntimeError):
    """A model call that produced no usable answer. ``kind`` says why."""

    KINDS = ("unavailable", "transient", "refused", "truncated", "invalid_output",
             "context_too_long", "budget", "bad_request")

    def __init__(self, kind: str, message: str) -> None:
        assert kind in self.KINDS, kind
        super().__init__(f"[{kind}] {message}")
        self.kind = kind


@dataclass
class LLMResult:
    text: str
    data: Any = None                 # parsed + validated JSON when a schema was given
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    mode: str = "text"               # "text" | "native_schema" | "prompt_schema"


# -- model families (for the "judge from a different family" rule) ---------------------

_FAMILY_BY_NAME = [
    (re.compile(r"claude|anthropic"), "anthropic"),
    (re.compile(r"\bgpt|^o\d|/o\d|openai|chatgpt"), "openai"),
    (re.compile(r"gemini|gemma|palm"), "google"),
    (re.compile(r"(?<!o)llama"), "meta"),      # not ollama/<model>
    (re.compile(r"mistral|mixtral|codestral|magistral|devstral"), "mistral"),
    (re.compile(r"qwen"), "alibaba"),
    (re.compile(r"deepseek"), "deepseek"),
    (re.compile(r"grok"), "xai"),
    (re.compile(r"command|cohere"), "cohere"),
    (re.compile(r"kimi|moonshot"), "moonshot"),
    (re.compile(r"glm|zhipu|z-ai"), "zhipu"),
    (re.compile(r"minimax"), "minimax"),
]

# Providers that serve other makers' models as ``<router>/<maker>/<model>``: the maker,
# not the router, is the family (``openrouter/moonshotai/kimi-k2`` is moonshot).
ROUTERS = {"openrouter", "together_ai", "fireworks_ai", "deepinfra", "nebius", "novita",
           "litellm_proxy", "hosted_vllm", "huggingface", "replicate", "vercel_ai_gateway"}
_MAKER_ALIASES = {"meta-llama": "meta", "mistralai": "mistral", "x-ai": "xai",
                  "moonshotai": "moonshot", "z-ai": "zhipu", "thudm": "zhipu",
                  "qwen": "alibaba", "google": "google", "openai": "openai",
                  "anthropic": "anthropic", "deepseek-ai": "deepseek",
                  "minimaxai": "minimax"}


def family(model: str) -> str:
    """The model's maker, independent of where it's hosted: ``bedrock/anthropic.claude-…``,
    ``vertex_ai/claude-…`` and ``openrouter/anthropic/claude-…`` are all ``anthropic``."""
    name = model.lower()
    for rx, fam in _FAMILY_BY_NAME:
        if rx.search(name.split("/", 1)[-1]) or rx.search(name):
            return fam
    parts = name.split("/")
    if len(parts) >= 3 and parts[0] in ROUTERS:
        if parts[0] == "fireworks_ai" and parts[1] == "accounts" and len(parts) >= 5:
            return _MAKER_ALIASES.get(parts[2], parts[2])   # fireworks_ai/accounts/<maker>/…
        return _MAKER_ALIASES.get(parts[1], parts[1])
    return parts[0] if len(parts) > 1 else name


def same_family(a: str, b: str) -> bool:
    return family(a) == family(b)


# -- models LiteLLM doesn't know (prices, limits, capabilities) -------------------------

_REGISTERED: dict[str, dict] = {}


def register_models(info: dict[str, dict]) -> None:
    """Teach LiteLLM about models from ``[models.info."<model>"]`` (Harbor's ``model_info``):
    ``input_cost_per_token``, ``output_cost_per_token``, ``max_input_tokens``,
    ``supports_function_calling``, ``supports_response_schema``, … Idempotent."""
    todo = {m: dict(v) for m, v in info.items() if _REGISTERED.get(m) != v}
    if not todo:
        return
    import logging

    try:
        import litellm
    except ImportError:              # not installed: the session's checks say so, politely
        return
    litellm.suppress_debug_info = True
    log = logging.getLogger("LiteLLM")
    level = log.level
    log.setLevel(logging.ERROR)      # its notes about cache prices aren't the user's problem
    try:
        _register(litellm, todo, info)
    finally:
        log.setLevel(level)


def _register(litellm: Any, todo: dict[str, dict], info: dict[str, dict]) -> None:
    for m, v in todo.items():
        provider = m.split("/", 1)[0] if "/" in m else None
        if provider and "litellm_provider" not in v:
            v["litellm_provider"] = provider
        v.setdefault("mode", "chat")
        litellm.register_model({m: v})
        _REGISTERED[m] = info[m]


def price(model: str, tokens_in: int, tokens_out: int) -> float | None:
    """Dollars for one call, from LiteLLM's price table; None when the price is unknown.

    LiteLLM answers $0 for some models it has no price for (e.g. many behind OpenRouter),
    so $0 counts as unknown unless ``[models.info]`` sets the price (a local model is
    free only when you say so)."""
    try:
        import litellm
        litellm.suppress_debug_info = True
        a, b = litellm.cost_per_token(model=model, prompt_tokens=tokens_in,
                                      completion_tokens=tokens_out)
        usd = float(a) + float(b)
    except Exception:  # noqa: BLE001 - an unknown model has no price, not an error
        return None
    if usd == 0.0 and not _priced(model):
        return None
    return usd


def _priced(model: str) -> bool:
    v = _REGISTERED.get(model) or {}
    return "input_cost_per_token" in v or "output_cost_per_token" in v


# -- the client ----------------------------------------------------------------------


@dataclass
class LLM:
    model: str
    timeout_s: float = 600.0
    max_retries: int = 3
    max_tokens: int = 16000
    budget_usd: float | None = None
    extra: dict = field(default_factory=dict)      # forwarded to litellm.completion
    spent_usd: float = 0.0
    calls: int = 0

    @classmethod
    def from_settings(cls, ms, *, model: str | None = None,
                      budget_usd: float | None = None) -> LLM:
        register_models(ms.info)
        return cls(model=model or ms.judge, timeout_s=ms.timeout_s, max_retries=ms.max_retries,
                   max_tokens=ms.max_tokens, budget_usd=budget_usd, extra=dict(ms.extra))

    # -- readiness -----------------------------------------------------------------

    def check(self) -> str | None:
        """None if this model can be called from here, else the reason + fix. No network."""
        load_dotenv()
        try:
            import litellm
        except ImportError:
            return "litellm is not installed — run: pip install 'rlgate[llm]'"
        if "/" not in self.model:
            return (f"model {self.model!r} has no provider prefix — use provider/model, "
                    f"e.g. anthropic/{self.model}")
        if self.extra.get("api_key"):
            return None
        try:
            env = litellm.validate_environment(model=self.model,
                                               api_base=self.extra.get("api_base"))
        except Exception as e:  # noqa: BLE001 - unknown provider etc.
            return f"litellm can't resolve {self.model!r}: {e}"
        missing = env.get("missing_keys") or []
        if missing:
            return f"set {', '.join(missing)} (in your shell or .env) to use {self.model}"
        return None

    def ping(self) -> LLMResult:
        """One tiny real call — proves the key, model name and network all work."""
        return self.complete("Reply with exactly: ok", max_tokens=2048)

    # -- calls ---------------------------------------------------------------------

    def complete(self, prompt: str | list[dict], *, system: str | None = None,
                 schema: dict | None = None, schema_name: str = "result",
                 max_tokens: int | None = None, seed: int | None = None,
                 temperature: float | None = None) -> LLMResult:
        """One call. ``seed`` / ``temperature`` make independent votes differ; providers that
        don't take them ignore them (``drop_params``)."""
        reason = self.check()
        if reason:
            raise LLMError("unavailable", reason)
        if self.budget_usd is not None and self.spent_usd >= self.budget_usd:
            raise LLMError("budget", f"spent ${self.spent_usd:.4f} of the "
                                     f"${self.budget_usd:.2f} budget")
        messages = _messages(prompt, system)
        sampling = {k: v for k, v in (("seed", seed), ("temperature", temperature))
                    if v is not None}
        if schema is None:
            return self._call(messages, max_tokens=max_tokens, mode="text", sampling=sampling)

        mode = "native_schema" if self._supports_native_schema() else "prompt_schema"
        try:
            res = self._call(messages, max_tokens=max_tokens, mode=mode, schema=schema,
                             schema_name=schema_name, sampling=sampling)
        except LLMError as e:
            if mode != "native_schema" or e.kind != "bad_request":
                raise
            mode = "prompt_schema"     # provider rejected native structured output
            res = self._call(messages, max_tokens=max_tokens, mode=mode, schema=schema,
                             schema_name=schema_name, sampling=sampling)

        problem = _parse_and_validate(res, schema)
        if problem is None:
            return res
        # One repair round: show the model its reply and the exact problem.
        repair = messages + [
            {"role": "assistant", "content": res.text},
            {"role": "user", "content": f"That reply is not valid: {problem}. Reply again "
                                        "with only a JSON object that matches the schema."},
        ]
        res2 = self._call(repair, max_tokens=max_tokens, mode=mode, schema=schema,
                          schema_name=schema_name, sampling=sampling)
        problem2 = _parse_and_validate(res2, schema)
        if problem2 is not None:
            raise LLMError("invalid_output", f"{self.model} returned invalid JSON twice: "
                                             f"{problem2}")
        return res2

    def _supports_native_schema(self) -> bool:
        import litellm
        try:
            return bool(litellm.supports_response_schema(model=self.model))
        except Exception:  # noqa: BLE001 - unknown model → assume not
            return False

    def _call(self, messages: list[dict], *, max_tokens: int | None, mode: str,
              schema: dict | None = None, schema_name: str = "result",
              sampling: dict | None = None) -> LLMResult:
        import litellm
        litellm.suppress_debug_info = True

        kwargs: dict[str, Any] = dict(
            model=self.model, messages=messages, max_tokens=max_tokens or self.max_tokens,
            timeout=self.timeout_s, num_retries=self.max_retries, **self.extra,
        )
        if sampling:
            kwargs.update(sampling)
            kwargs.setdefault("drop_params", True)   # e.g. a provider without seeds
        if schema is not None and mode == "native_schema":
            kwargs["response_format"] = {"type": "json_schema", "json_schema": {
                "name": schema_name, "schema": schema, "strict": True}}
        elif schema is not None:
            kwargs["messages"] = _with_schema_instruction(messages, schema)

        try:
            resp: Any = litellm.completion(**kwargs)
        except Exception as e:
            raise _classify(e, self.model) from e
        self.calls += 1

        choice = resp.choices[0]
        text = choice.message.content or ""
        usage = getattr(resp, "usage", None)
        cost = _cost(resp)
        if cost is not None:
            self.spent_usd += cost
        finish = (choice.finish_reason or "").lower()
        if finish == "length":
            raise LLMError("truncated", f"{self.model} hit max_tokens before finishing; "
                                        "raise models.max_tokens")
        if finish in ("content_filter", "refusal"):
            raise LLMError("refused", f"{self.model} declined to answer ({finish})")
        return LLMResult(
            text=text, model=getattr(resp, "model", self.model) or self.model,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            cost_usd=cost, mode=mode,
        )


@dataclass
class ChatTurn:
    """One agent turn: the assistant message to append, and the tool calls it makes."""
    message: dict
    calls: list[tuple[str, str, str]]       # (call id, tool name, JSON arguments)
    text: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None


def chat(llm: LLM, messages: list[dict], tools: list[dict], *,
         max_tokens: int | None = None) -> ChatTurn:
    """One tool-calling turn through LiteLLM (any provider with tool use). Same failure
    rules as ``LLM.complete``: every error is an ``LLMError``."""
    reason = llm.check()
    if reason:
        raise LLMError("unavailable", reason)
    import litellm
    litellm.suppress_debug_info = True
    kwargs: dict[str, Any] = dict(
        model=llm.model, messages=wire(messages, llm.model), tools=tools, tool_choice="auto",
        max_tokens=max_tokens or llm.max_tokens, timeout=llm.timeout_s,
        num_retries=llm.max_retries, **llm.extra)
    try:
        resp: Any = litellm.completion(**kwargs)
    except Exception as e:
        raise _classify(e, llm.model) from e
    llm.calls += 1
    choice = resp.choices[0]
    msg = choice.message
    calls = [(tc.id, tc.function.name, tc.function.arguments or "{}")
             for tc in (getattr(msg, "tool_calls", None) or [])]
    finish = (choice.finish_reason or "").lower()
    if finish == "length" and not calls:
        raise LLMError("truncated", f"{llm.model} hit max_tokens before finishing a turn")
    if finish in ("content_filter", "refusal"):
        raise LLMError("refused", f"{llm.model} declined to answer ({finish})")
    if hasattr(msg, "model_dump"):
        message = {k: v for k, v in msg.model_dump(exclude_none=True).items()
                   if k in ("role", "content", "tool_calls", "thinking_blocks")}
    else:
        message = {"content": getattr(msg, "content", "") or ""}
        if calls:
            message["tool_calls"] = [{"id": i, "type": "function",
                                      "function": {"name": n, "arguments": a}}
                                     for i, n, a in calls]
    message["role"] = "assistant"
    message.setdefault("content", "")
    usage = getattr(resp, "usage", None)
    cost = _cost(resp)
    if cost is not None:
        llm.spent_usd += cost
    return ChatTurn(message, calls, getattr(msg, "content", "") or "",
                    getattr(usage, "prompt_tokens", 0) or 0,
                    getattr(usage, "completion_tokens", 0) or 0, cost)


def wire(messages: list[dict], model: str) -> list[dict]:
    """The messages as sent: private bookkeeping keys (``_masked``) dropped, and for
    Anthropic models the system prompt and the first user message (the stable prefix every
    turn repeats) marked for prompt caching. Other providers cache prefixes on their own."""
    out = [{k: v for k, v in m.items() if not k.startswith("_")} for m in messages]
    if family(model) == "anthropic":
        for m in out[:2]:
            if isinstance(m.get("content"), str) and m["content"]:
                m["content"] = [{"type": "text", "text": m["content"],
                                 "cache_control": {"type": "ephemeral"}}]
    return out


def _messages(prompt: str | list[dict], system: str | None) -> list[dict]:
    msgs = [{"role": "user", "content": prompt}] if isinstance(prompt, str) else list(prompt)
    if system:
        msgs = [{"role": "system", "content": system}] + msgs
    return msgs


def _with_schema_instruction(messages: list[dict], schema: dict) -> list[dict]:
    instr = ("Respond with only a JSON object (no prose, no code fences) that conforms to "
             "this JSON Schema:\n" + json.dumps(schema, indent=2))
    if messages and messages[0]["role"] == "system":
        return [{"role": "system", "content": messages[0]["content"] + "\n\n" + instr}] + \
            messages[1:]
    return [{"role": "system", "content": instr}] + messages


def _cost(resp) -> float | None:
    try:
        import litellm
        return float(litellm.completion_cost(completion_response=resp))
    except Exception:  # noqa: BLE001 - unknown pricing is not an error
        return None


def _classify(e: Exception, model: str) -> LLMError:
    import litellm
    ex = litellm.exceptions
    name = type(e).__name__
    if isinstance(e, ex.ContextWindowExceededError):
        return LLMError("context_too_long", f"{model}: input exceeds the context window")
    if isinstance(e, ex.ContentPolicyViolationError):
        return LLMError("refused", f"{model}: {e}")
    if isinstance(e, (ex.AuthenticationError, ex.PermissionDeniedError, ex.NotFoundError)):
        return LLMError("unavailable", f"{model}: {name}: {e}")
    if isinstance(e, ex.BadRequestError):
        return LLMError("bad_request", f"{model}: {e}")
    if isinstance(e, (ex.RateLimitError, ex.Timeout, ex.APIConnectionError,
                      ex.ServiceUnavailableError, ex.InternalServerError, ex.APIError)):
        return LLMError("transient", f"{model}: {name} after retries: {e}")
    return LLMError("transient", f"{model}: {name}: {e}")


# -- JSON parsing + a small JSON-Schema validator (no extra dependency) ---------------

_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def _parse_and_validate(res: LLMResult, schema: dict) -> str | None:
    text = res.text.strip()
    m = _FENCE.match(text)
    if m:
        text = m.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return f"not JSON ({e.msg} at char {e.pos})"
    problem = validate(data, schema)
    if problem is None:
        res.data = data
    return problem


_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def validate(data: Any, schema: dict, path: str = "$") -> str | None:
    """Validate the JSON-Schema subset judges use: type, required, properties,
    additionalProperties=false, enum, items, minItems/maxItems, minimum/maximum."""
    types = schema.get("type")
    if types is not None:
        types = [types] if isinstance(types, str) else list(types)
        if not any(_is_type(data, t) for t in types):
            return f"{path}: expected {'/'.join(types)}, got {type(data).__name__}"
    if "enum" in schema and data not in schema["enum"]:
        return f"{path}: {data!r} is not one of {schema['enum']}"
    if isinstance(data, dict):
        for k in schema.get("required", []):
            if k not in data:
                return f"{path}: missing required field {k!r}"
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extra = sorted(set(data) - set(props))
            if extra:
                return f"{path}: unexpected field(s) {extra}"
        for k, sub in props.items():
            if k in data:
                p = validate(data[k], sub, f"{path}.{k}")
                if p:
                    return p
    if isinstance(data, list):
        if "minItems" in schema and len(data) < schema["minItems"]:
            return f"{path}: needs at least {schema['minItems']} item(s)"
        if "maxItems" in schema and len(data) > schema["maxItems"]:
            return f"{path}: allows at most {schema['maxItems']} item(s)"
        if "items" in schema:
            for i, item in enumerate(data):
                p = validate(item, schema["items"], f"{path}[{i}]")
                if p:
                    return p
    if isinstance(data, (int, float)) and not isinstance(data, bool):
        if "minimum" in schema and data < schema["minimum"]:
            return f"{path}: {data} < minimum {schema['minimum']}"
        if "maximum" in schema and data > schema["maximum"]:
            return f"{path}: {data} > maximum {schema['maximum']}"
    return None


def _is_type(data: Any, t: str) -> bool:
    if t == "integer":
        return isinstance(data, int) and not isinstance(data, bool)
    if t == "number":
        return isinstance(data, (int, float)) and not isinstance(data, bool)
    return isinstance(data, _TYPES.get(t, object))


def provider_env_hint(model: str) -> str:
    """Best-effort name of the key variable for a model, for error messages/doctor."""
    try:
        import litellm
        missing = litellm.validate_environment(model=model).get("missing_keys") or []
        if missing:
            return missing[0]
    except Exception:  # noqa: BLE001 - fall through to the generic hint
        return f"{model.split('/', 1)[0].upper()}_API_KEY" if "/" in model else "API key"
    return f"{model.split('/', 1)[0].upper()}_API_KEY" if "/" in model else "API key"


__all__ = ["LLM", "ROUTERS", "ChatTurn", "LLMError", "LLMResult", "chat", "family", "price",
           "provider_env_hint", "register_models", "same_family", "validate"]
