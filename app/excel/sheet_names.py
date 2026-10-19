from __future__ import annotations

import re
import unicodedata


_INVALID_SHEET_CHARS = re.compile(r"[\\/*?:\[\]]")


def safe_sheet_name(value: str, *, prefix: str = "", used: set[str] | None = None) -> str:
    normalized = unicodedata.normalize("NFC", value).strip().strip("'")
    normalized = _INVALID_SHEET_CHARS.sub("-", normalized) or "Sheet"
    base = f"{prefix}{normalized}"[:31]
    if used is None or base not in used:
        if used is not None:
            used.add(base)
        return base
    counter = 2
    while True:
        suffix = f"-{counter}"
        candidate = f"{base[:31 - len(suffix)]}{suffix}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        counter += 1


def safe_filename_component(value: str, *, max_length: int = 80) -> str:
    normalized = unicodedata.normalize("NFC", value).strip()
    normalized = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "-", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip(" .")
    return (normalized or "未命名")[:max_length]
