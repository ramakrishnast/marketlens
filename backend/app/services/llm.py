"""OpenAI client wrapper. MarketLens uses exactly one model: gpt-6-luna (OPENAI_MODEL).

All AI features go through here so "no key" is handled in one place: callers catch
AIUnavailable and show an "add an API key" message instead of crashing.
"""
from __future__ import annotations

import logging
from typing import Any

from ..config import get_settings

log = logging.getLogger(__name__)

NO_ADVICE = (
    "You are part of MarketLens, a personal market-research tool. Never give buy, sell or hold recommendations, "
    "price targets, or position sizing, and never tell the user what they should do with their money. If asked, "
    "explain the relevant data, scenarios and tradeoffs instead, and say that the decision is theirs. "
    "Use only numbers present in the provided data or tool results; never state figures from memory."
)


class AIUnavailable(RuntimeError):
    """No OPENAI_API_KEY configured (or the client library is missing)."""


class AIError(RuntimeError):
    pass


_unsupported: dict[str, set[str]] = {}


def model() -> str:
    return get_settings().openai_model


def _client():
    s = get_settings()
    if not s.openai_api_key:
        raise AIUnavailable("Add OPENAI_API_KEY to .env to enable AI features (model gpt-6-luna).")
    try:
        import openai
    except ImportError as e:  # pragma: no cover
        raise AIUnavailable("The openai package is not installed") from e
    return openai.OpenAI(api_key=s.openai_api_key, base_url=s.openai_base_url or None, max_retries=3, timeout=120)


def create(messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None, max_tokens: int = 2000,
           effort: str = "low", json_mode: bool = False) -> Any:
    """One Chat Completions call with self-healing for optional parameters the model may not support."""
    import openai

    client = _client()
    m = model()
    bad = _unsupported.setdefault(m, set())
    for _ in range(4):
        kwargs: dict[str, Any] = {"model": m, "messages": messages, "max_completion_tokens": max_tokens}
        if tools:
            kwargs["tools"] = tools
        if json_mode and "response_format" not in bad:
            kwargs["response_format"] = {"type": "json_object"}
        if "reasoning_effort" not in bad:
            kwargs["reasoning_effort"] = effort
        try:
            return client.chat.completions.create(**kwargs)
        except openai.BadRequestError as e:
            text = str(e).lower()
            if "reasoning" in text and "reasoning_effort" in kwargs:
                bad.add("reasoning_effort")
            elif "response_format" in kwargs and ("response_format" in text or "json" in text):
                bad.add("response_format")
            else:
                raise AIError(f"OpenAI rejected the request: {e}") from e
        except openai.AuthenticationError as e:
            raise AIUnavailable("OPENAI_API_KEY was rejected by OpenAI. Check the key in .env.") from e
        except openai.APIError as e:
            raise AIError(f"OpenAI error: {e}") from e
    raise AIError("OpenAI request kept failing with unsupported parameters")


def complete(system: str, user: str, *, max_tokens: int = 2000, effort: str = "low", json_mode: bool = False) -> str:
    resp = create([{"role": "system", "content": f"{system}\n\n{NO_ADVICE}"}, {"role": "user", "content": user}],
                  max_tokens=max_tokens, effort=effort, json_mode=json_mode)
    msg = resp.choices[0].message
    if getattr(msg, "refusal", None):
        raise AIError(f"Model refused: {msg.refusal}")
    return (msg.content or "").strip()


def status() -> dict[str, Any]:
    s = get_settings()
    return {"enabled": bool(s.openai_api_key), "model": s.openai_model,
            "message": None if s.openai_api_key else "Add OPENAI_API_KEY to .env to enable AI features."}
