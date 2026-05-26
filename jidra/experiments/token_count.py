"""Experimental token counting / scratchpad.

This module previously contained company-specific package names and absolute file paths.
It has been sanitized so the JIDRA project can be shipped as a standalone pet project.

If you need token counting, consider using `jidra/token_count.py` with a small CLI wrapper,
or rely on your model provider's usage reporting.
"""

from __future__ import annotations


def count_tokens(text: str) -> int:
    """Very rough token estimator.

    Intentionally avoids provider-specific encodings so the project stays standalone.
    """

    # Approximation: ~4 chars/token for English-ish text.
    # Not accurate for code or non-English, but good enough for rough sizing.
    text = text or ""
    return max(1, len(text) // 4)
