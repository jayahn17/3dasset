"""Thin model client.  Everything agent-side goes through ``LLM.complete`` so
tests can inject a fake and the SDK stays an optional dependency.
"""
from __future__ import annotations

import os
from typing import Optional, Protocol


class AgentError(RuntimeError):
    pass


class LLM(Protocol):
    def complete(self, system: str, user: str, *, effort: str = "medium", max_tokens: int = 16000,
                 model: Optional[str] = None) -> str: ...


DEFAULT_MODEL = os.environ.get("LEARNFIG_MODEL", "claude-opus-5-5")


class ClaudeLLM:
    """Claude via the official SDK (streaming, adaptive thinking, server-side refusal fallback)."""

    def __init__(self, model: Optional[str] = None, client=None, fallbacks: bool = True):
        try:
            import anthropic  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise AgentError("pip install 'learnfig[agents]' (the anthropic package) to use the agents") from exc
        import anthropic
        self.anthropic = anthropic
        self.client = client or anthropic.Anthropic()
        self.model = model or DEFAULT_MODEL
        self.fallbacks = fallbacks and os.environ.get("LEARNFIG_FALLBACKS", "1") != "0"
        self.usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0}

    def complete(self, system: str, user: str, *, effort: str = "medium", max_tokens: int = 16000,
                 model: Optional[str] = None) -> str:
        kwargs = dict(
            model=model or self.model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config={"effort": effort},
        )
        if self.fallbacks:
            # Server-side fallback: if a safeguard declines, the request is re-routed by category.
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
        try:
            msg = self._stream(kwargs)
        except TypeError:
            # older SDK without the typed `fallbacks` parameter → send it in the body
            kwargs.pop("fallbacks", None)
            kwargs["extra_body"] = {"fallbacks": "default"}
            msg = self._stream(kwargs)
        self.usage["calls"] += 1
        if getattr(msg, "usage", None):
            self.usage["input_tokens"] += getattr(msg.usage, "input_tokens", 0) or 0
            self.usage["output_tokens"] += getattr(msg.usage, "output_tokens", 0) or 0
        if msg.stop_reason == "refusal":
            details = getattr(msg, "stop_details", None)
            raise AgentError(f"model declined the request ({getattr(details, 'category', 'unknown')})")
        if msg.stop_reason == "max_tokens":
            raise AgentError("response was cut off by max_tokens; raise max_tokens or narrow the source")
        return "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text")

    def _stream(self, kwargs):
        if "betas" in kwargs:
            with self.client.beta.messages.stream(**kwargs) as stream:
                return stream.get_final_message()
        with self.client.messages.stream(**kwargs) as stream:
            return stream.get_final_message()
