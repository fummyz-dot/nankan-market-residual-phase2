"""Exact official live-card aliases for frozen M02 source vocabulary.

This is intentionally not a normalizer: every permitted raw token is listed
explicitly and the raw display string remains stored unchanged.
"""
from __future__ import annotations

EXACT_M02_SOURCE_ALIASES = {
    "普通競走": "普通",
    "特別競走": "特別",
    "重賞競走": "重賞",
    "準重賞競走": "準重賞",
}

def m02_source_text(raw: str | None) -> str | None:
    """Return an approved exact alias or the unchanged raw token."""
    return EXACT_M02_SOURCE_ALIASES.get(raw, raw)
