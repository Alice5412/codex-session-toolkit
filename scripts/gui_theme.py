#!/usr/bin/env python3
from __future__ import annotations

APP_BG = "#081120"
SURFACE_BG = "#0d1930"
SIDEBAR_BG = "#091326"
CARD_BG = "#11213d"
CARD_ALT_BG = "#172b4f"
INPUT_BG = "#0b1730"
TABLE_BG = "#0c172e"
BORDER = "#22385f"
TEXT_PRIMARY = "#f3f7ff"
TEXT_SECONDARY = "#8fa6c7"
TEXT_MUTED = "#61789a"
ACCENT = "#3b82f6"
ACCENT_HOVER = "#2563eb"
ACCENT_SOFT = "#17396f"
SELECTION_BG = "#1d4ed8"


def short_text(value: str, limit: int = 48) -> str:
    collapsed = " ".join(value.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3] + "..."


def ui_font(size: int, weight: str = "normal") -> str:
    return f"{{Segoe UI}} {size}" if weight == "normal" else f"{{Segoe UI}} {size} {weight}"
