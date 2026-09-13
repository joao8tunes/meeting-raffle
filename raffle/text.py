"""Text normalization helpers shared by the parsers."""

from __future__ import annotations

import re
import unicodedata

_NON_WORD_RE = re.compile(r"[\W_]+", re.UNICODE)
_PARENTHETICAL_RE = re.compile(r"\s*(\([^)]*\)|\[[^\]]*\])")
_INVISIBLE_RE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060\ufeff]")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def clean_cell(value: object) -> str:
    """Trim a raw cell: invisible characters, wrapping quotes and repeated whitespace."""
    if value is None:
        return ""
    text = _INVISIBLE_RE.sub("", str(value)).replace("\xa0", " ").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].replace('""', '"').strip()
    return " ".join(text.split())


def normalize_label(value: object) -> str:
    """Lowercase, accent-free, punctuation collapsed to single spaces (used to match headers)."""
    text = strip_accents(clean_cell(value)).lower()
    return " ".join(_NON_WORD_RE.sub(" ", text).split())


def remove_parentheticals(value: str) -> str:
    return " ".join(_PARENTHETICAL_RE.sub(" ", value).split())


def parentheticals(value: str) -> list[str]:
    return [m.group(1) for m in _PARENTHETICAL_RE.finditer(value)]


def looks_like_email(value: str) -> bool:
    return bool(_EMAIL_RE.match(value.strip()))
