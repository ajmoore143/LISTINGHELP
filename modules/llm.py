"""
Single entry point for all model calls (Anthropic Claude).

Every pipeline stage goes through here, so switching models or providers is a
one-file change. Structured stages use forced tool calling to guarantee schema-
valid JSON; web research uses Claude's server-side web search tool.
"""

import base64
import os
from typing import Any, Dict, Optional

import streamlit as st

DEFAULT_MAX_TOKENS = 8000
WEB_SEARCH_TOOL_TYPE = "web_search_20250305"
_EMIT_TOOL = "emit_result"


def _api_key() -> str:
    return st.secrets.get("ANTHROPIC_API_KEY", os.getenv("ANTHROPIC_API_KEY", ""))


def get_client():
    """Return an Anthropic client, or None when no API key is configured."""
    key = _api_key()
    if not key:
        return None
    from anthropic import Anthropic  # imported lazily so the app loads without the package
    return Anthropic(api_key=key)


def _require_client():
    client = get_client()
    if client is None:
        raise ValueError(
            "Anthropic API key is missing. Set ANTHROPIC_API_KEY in Streamlit secrets "
            "or your environment."
        )
    return client


def _extract_tool_json(response) -> Dict[str, Any]:
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", "") == _EMIT_TOOL:
            return dict(block.input)
    raise ValueError("Model did not return structured output.")


def complete_json(
    model: str,
    system: str,
    user: str,
    schema: Dict[str, Any],
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> Dict[str, Any]:
    """Text-only call that returns schema-valid JSON via forced tool use."""
    client = _require_client()
    tool = {"name": _EMIT_TOOL, "description": "Return the structured result.",
            "input_schema": schema}
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        tools=[tool],
        tool_choice={"type": "tool", "name": _EMIT_TOOL},
    )
    return _extract_tool_json(resp)


def complete_json_with_pdf(
    model: str,
    system: str,
    user_text: str,
    pdf_bytes: bytes,
    schema: Dict[str, Any],
    filename: str = "report.pdf",
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> Dict[str, Any]:
    """Send a PDF natively (image/text/dashboards) and return schema-valid JSON."""
    client = _require_client()
    encoded = base64.b64encode(pdf_bytes).decode("ascii")
    content = [
        {"type": "document",
         "source": {"type": "base64", "media_type": "application/pdf", "data": encoded}},
        {"type": "text", "text": user_text},
    ]
    tool = {"name": _EMIT_TOOL, "description": "Return the structured result.",
            "input_schema": schema}
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": content}],
        tools=[tool],
        tool_choice={"type": "tool", "name": _EMIT_TOOL},
    )
    return _extract_tool_json(resp)


def complete_text_with_websearch(
    model: str,
    system: str,
    user: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    max_uses: int = 5,
) -> str:
    """Run live web research and return the model's markdown notes.

    Returns an empty string when no API key is configured so the caller can
    continue without web data.
    """
    client = get_client()
    if client is None:
        return ""
    tools = [{"type": WEB_SEARCH_TOOL_TYPE, "name": "web_search", "max_uses": max_uses}]
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        tools=tools,
    )
    parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    return "\n".join(parts).strip()
