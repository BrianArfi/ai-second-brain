#!/usr/bin/env python3
"""
mermaid_render.py - render Mermaid source to PNG bytes, with a fallback renderer.

Shared by .agent/skills/diagram-gen/render_check.py and
scripts/embed_mermaid_in_gdoc.py so that what you validate is what gets embedded.

Why a fallback exists: kroki.io runs Mermaid inside a headless Chrome that falls
over on its own. On 9 Sep 2026 kroki's mermaid backend returned HTTP 500 for
every diagram, including `flowchart TB\\n A-->B`, while kroki's graphviz backend
on the same host returned 200. render_check.py reported that as "INVALID
Mermaid", so a correct diagram looked like a syntax error and no diagram could
be produced at all. A renderer being down and a diagram being wrong are
different failures and must not print the same message.

Order: kroki.io first (same encoding the Google Doc embed path has always used),
then mermaid.ink. mermaid.ink needs a browser User-Agent or it answers 403.
"""
import base64
import json
import zlib

import requests

UA = {"User-Agent": "Mozilla/5.0 (compatible; work-diagram-gen/1.0)"}

class MermaidSyntaxError(Exception):
    """Every renderer rejected the source, and at least one blamed the syntax."""

class RendererUnavailable(Exception):
    """No renderer could be reached or all of them failed on their own side."""

def kroki_png_url(src: str) -> str:
    data = base64.urlsafe_b64encode(zlib.compress(src.encode("utf-8"), 9)).decode()
    return "https://kroki.io/mermaid/png/" + data

def mermaid_ink_png_url(src: str) -> str:
    raw = json.dumps({"code": src, "mermaid": {"theme": "default"}}).encode("utf-8")
    co = zlib.compressobj(9, zlib.DEFLATED, 15)
    data = base64.urlsafe_b64encode(co.compress(raw) + co.flush()).decode()
    return "https://mermaid.ink/img/pako:" + data + "?type=png"

def _is_png(content: bytes) -> bool:
    return content[:8] == b"\x89PNG\r\n\x1a\n"

def _try(url: str, timeout: int):
    """Return (png_bytes, None) on success, or (None, (status, body)) on failure."""
    try:
        r = requests.get(url, headers=UA, timeout=timeout)
    except requests.RequestException as exc:
        return None, (None, str(exc)[:400])
    if r.status_code == 200 and _is_png(r.content):
        return r.content, None
    return None, (r.status_code, r.text[:400] if r.text else repr(r.content[:200]))

def render_png(src: str, timeout: int = 60):
    """Render `src` to PNG bytes.

    Returns (png_bytes, renderer_name).
    Raises MermaidSyntaxError when a renderer answered 4xx, which means it read
    the source and rejected it. Raises RendererUnavailable otherwise.
    """
    errors = []
    syntax_blamed = False
    for name, url in (("kroki.io", kroki_png_url(src)),
                      ("mermaid.ink", mermaid_ink_png_url(src))):
        content, err = _try(url, timeout)
        if content is not None:
            return content, name
        status, body = err
        if status is not None and 400 <= status < 500:
            syntax_blamed = True
        errors.append(f"{name}: status={status} body={body}")

    detail = "\n".join(errors)
    if syntax_blamed:
        raise MermaidSyntaxError(detail)
    raise RendererUnavailable(detail)
