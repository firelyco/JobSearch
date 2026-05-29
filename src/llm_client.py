"""Provider-agnostic wrapper around the chat-completion API.

Supports two providers, selected by the LLM_PROVIDER env var:
  - "anthropic" (default): Anthropic SDK, Claude models, ANTHROPIC_API_KEY
  - "nvidia": OpenAI-compatible SDK pointed at NVIDIA's free NIM endpoint
    (https://integrate.api.nvidia.com/v1), DeepSeek/other models,
    NVIDIA_API_KEY

Why a wrapper:
  1. Tests inject a fake client via build_client(fake=...) — no SDK or key
     needed locally.
  2. call() centralizes the request/response shape so tailor.py, verify.py,
     and fit_scorer.py never touch a provider SDK directly.
  3. Swapping providers/models is a one-file + config change.

call() auto-detects the client shape (Anthropic .messages vs OpenAI .chat),
so the same downstream code and the same FakeClient work regardless of which
provider is active.
"""
from __future__ import annotations
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

# DeepSeek (and other reasoning models) may emit a <think>...</think> block
# before the answer. Strip it so JSON extraction downstream isn't confused by
# braces inside the reasoning.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


@dataclass
class LLMResponse:
    text: str
    input_tokens: int
    output_tokens: int
    model: str


def active_provider() -> str:
    return os.environ.get("LLM_PROVIDER", "anthropic").lower().strip()


def build_client(fake: Any | None = None) -> Any:
    """Return a provider client, or the passed fake for tests.

    Raises RuntimeError if the relevant API key is unset or the SDK isn't
    installed. The provider is chosen by LLM_PROVIDER (default 'anthropic').
    """
    if fake is not None:
        return fake
    provider = active_provider()
    if provider == "nvidia":
        api_key = os.environ.get("NVIDIA_API_KEY")
        if not api_key:
            raise RuntimeError("NVIDIA_API_KEY not set and no fake client provided")
        try:
            from openai import OpenAI  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "openai package not installed; add it to requirements.txt and pip install"
            ) from e
        return OpenAI(base_url=NVIDIA_BASE_URL, api_key=api_key)

    # default: anthropic
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set and no fake client provided")
    try:
        from anthropic import Anthropic  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "anthropic package not installed; add it to requirements.txt and pip install"
        ) from e
    return Anthropic(api_key=api_key)


def call(
    client: Any,
    *,
    model: str,
    system: str,
    user: str,
    max_tokens: int = 2000,
    temperature: float = 0.4,
    cache_system: bool = False,
) -> LLMResponse:
    """One-shot completion. Returns text + token usage.

    Dispatches by client shape:
      - OpenAI-compatible (has .chat): NVIDIA / DeepSeek path. cache_system is
        ignored (no Anthropic-style prompt caching); system goes as a system
        message. <think> reasoning blocks are stripped from the output.
      - Anthropic (has .messages): Claude path. cache_system marks the system
        prompt for ephemeral prompt caching.
    """
    if hasattr(client, "chat"):
        return _call_openai(client, model, system, user, max_tokens, temperature)
    return _call_anthropic(client, model, system, user, max_tokens, temperature, cache_system)


def _call_anthropic(client, model, system, user, max_tokens, temperature, cache_system) -> LLMResponse:
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
    content = getattr(resp, "content", None) or []
    parts = [getattr(b, "text", "") for b in content if getattr(b, "type", None) == "text"]
    usage = getattr(resp, "usage", None)
    return LLMResponse(
        text="".join(parts),
        input_tokens=getattr(usage, "input_tokens", 0) if usage else 0,
        output_tokens=getattr(usage, "output_tokens", 0) if usage else 0,
        model=model,
    )


def _call_openai(client, model, system, user, max_tokens, temperature) -> LLMResponse:
    resp = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    choices = getattr(resp, "choices", None) or []
    text = ""
    if choices:
        msg = getattr(choices[0], "message", None)
        text = getattr(msg, "content", "") or "" if msg else ""
    text = _THINK_RE.sub("", text).strip()
    usage = getattr(resp, "usage", None)
    return LLMResponse(
        text=text,
        input_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
        output_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
        model=model,
    )
