"""Thin wrapper around the Anthropic SDK.

Why a wrapper instead of using anthropic.Anthropic() directly:
  1. Lets tests inject a fake client via build_client(fake=...) without
     installing the SDK locally.
  2. Centralizes the system-prompt + cache-control plumbing so tailor.py
     and verify.py share one implementation.
  3. If we swap models or providers later, only this file changes.

The Anthropic SDK is imported lazily so unit tests run in environments
without the package installed (CI installs it from requirements.txt).
"""
from __future__ import annotations
import logging
import os
from dataclasses import dataclass
from typing import Any, Protocol

log = logging.getLogger(__name__)


class _MessagesProtocol(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


class _ClientProtocol(Protocol):
    messages: _MessagesProtocol


@dataclass
class LLMResponse:
    text: str
    input_tokens: int
    output_tokens: int
    model: str


def build_client(fake: _ClientProtocol | None = None) -> _ClientProtocol:
    """Return an Anthropic client, or the passed fake for tests.

    Raises RuntimeError if no fake is provided and ANTHROPIC_API_KEY is unset
    or the anthropic package isn't installed.
    """
    if fake is not None:
        return fake
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set and no fake client provided")
    try:
        from anthropic import Anthropic  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "anthropic package not installed; uncomment in requirements.txt and pip install"
        ) from e
    return Anthropic(api_key=api_key)


def call(
    client: _ClientProtocol,
    *,
    model: str,
    system: str,
    user: str,
    max_tokens: int = 2000,
    temperature: float = 0.4,
    cache_system: bool = False,
) -> LLMResponse:
    """One-shot call. Returns text + token usage.

    If cache_system=True, marks the system prompt for prompt caching
    (5-min TTL on Anthropic's side). Caller is responsible for choosing
    when to cache (typically: profile JSON that's reused across calls).
    """
    if cache_system:
        system_param: Any = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    else:
        system_param = system
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system_param,
        messages=[{"role": "user", "content": user}],
    )
    text = _extract_text(resp)
    usage = getattr(resp, "usage", None)
    return LLMResponse(
        text=text,
        input_tokens=getattr(usage, "input_tokens", 0) if usage else 0,
        output_tokens=getattr(usage, "output_tokens", 0) if usage else 0,
        model=model,
    )


def _extract_text(resp: Any) -> str:
    """Pull text out of Anthropic's content-block response shape."""
    content = getattr(resp, "content", None)
    if not content:
        return ""
    parts: list[str] = []
    for block in content:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", ""))
    return "".join(parts)
