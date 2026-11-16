#!/usr/bin/env python3
"""english_gate — deterministic English-only send-gate for the local-cron senders.

The local model intermittently drifts into a non-English language mid-reply despite the
prompt-level English-only contract (seen live 2026-11-13: a full-Chinese storage sweep;
2026-11-16: a full-Chinese FluxHelmReleaseStalled enrichment reached Rob's inbox). Rob
reads English only, so a CJK-bearing body must NEVER reach the email sink. Prompt-steering
alone does not hold — the standing rule is to fix the drift at the SEND PATH, deterministically,
not by asking the model to behave — so every local-cron sender routes its model-authored body
through is_non_english() before notify-mcp. triage_run.py has gated this way since home-ops#15899;
this module hoists that one definition out of triage_run so the sibling senders share it instead
of each carrying (or lacking) a copy.

Built from integer codepoint ranges so this source stays plain ASCII (no literal CJK glyphs or
\\u escapes to mangle on edit). Threshold >=3 clears an odd stray fullwidth glyph while catching
any real drift (which runs to hundreds of CJK chars).

The alert-agent-bridge app (kubernetes/apps/ai/alert-agent-bridge/) carries its OWN inline copy
of this gate, not an import: it is a separate, intentionally dependency-free app in a different
pod + ConfigMap and cannot import this module. If the ranges or threshold change here, change them
there too.
"""
import re

_CJK_RANGES = (
    (0x3040, 0x30FF),  # Hiragana + Katakana
    (0x3400, 0x4DBF),  # CJK Unified Ext-A
    (0x4E00, 0x9FFF),  # CJK Unified
    (0xAC00, 0xD7AF),  # Hangul syllables
    (0xFF00, 0xFFEF),  # Halfwidth + Fullwidth forms
)
_CJK = re.compile(
    "[" + "".join("%s-%s" % (chr(lo), chr(hi)) for lo, hi in _CJK_RANGES) + "]")


def is_non_english(text):
    """True when the reply carries a meaningful amount of CJK text — the known
    local-model language-drift failure. Callers suppress the send (pure digests)
    or withhold the body behind an ASCII summary (signal-bearing result emails).
    Rob reads English only."""
    return len(_CJK.findall(text)) >= 3
