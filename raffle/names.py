"""Person name formatting and identity keys."""

from __future__ import annotations

from .text import clean_cell, looks_like_email, normalize_label, parentheticals, remove_parentheticals


def display_name(raw: object, reorder_last_first: bool = True) -> str:
    """Human friendly name.

    ``"Doe, Jane (Guest)"`` becomes ``"Jane Doe (Guest)"`` when ``reorder_last_first`` is set.
    """
    name = clean_cell(raw)
    if not reorder_last_first or looks_like_email(name):
        return name

    base = remove_parentheticals(name)
    if base.count(",") != 1 or any(ch.isdigit() for ch in base):
        return name

    last, first = (part.strip() for part in base.split(","))
    if not last or not first:
        return name

    suffix = " ".join(parentheticals(name))
    return f"{first} {last}" + (f" {suffix}" if suffix else "")


def name_key(raw: object) -> str:
    """Order-insensitive identity key: ``"Doe, Jane"`` and ``"JANE DOE (Guest)"`` share the same key."""
    tokens = normalize_label(remove_parentheticals(clean_cell(raw))).split()
    return " ".join(sorted(tokens))


def email_key(raw: object) -> str:
    value = clean_cell(raw).lower()
    if value.startswith("mailto:"):
        value = value[len("mailto:"):]
    return value if looks_like_email(value) else ""
