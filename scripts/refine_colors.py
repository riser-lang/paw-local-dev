# -*- coding: utf-8 -*-
"""Comprehensive refinement of paw_console.js to align 1:1 with PAW design language.
Eliminates all remaining Tailwind/ops colors, strange symbols, and emojis.
Applies pure PAW warm-neutral and terracotta design tokens throughout.
"""
import re

TARGET_FILE = r"d:\PAW\paw-beta\frontend\dist\paw_console.js"

with open(TARGET_FILE, "r", encoding="utf-8") as f:
    code = f.read()

# 1. Clean remaining emojis and icons
code = code.replace("\U0001f4be", "")  # floppy disk
code = code.replace("\u25b6\ufe0f", "")  # play icon
code = code.replace("\u25b6", "")
code = code.replace("\ufe0f", "")
code = code.replace(" background:#16a34a;", "")
code = code.replace("color:#ef4444;", "color:#A65F58;")
code = code.replace("color:#166534;", "color:#648166;")
code = code.replace("\u2715", "&times;")

# 2. Tailwind / Ops colors -> PAW warm neutral tokens
COLOR_MAP = {
    # Grays / Slates -> PAW Text & Borders
    "#0f172a": "#302B26",
    "#1e293b": "#302B26",
    "#334155": "#302B26",
    "#475569": "#756E65",
    "#64748b": "#756E65",
    "#94a3b8": "#A1998E",
    "#cbd5e1": "#DED8CC",
    "#e2e8f0": "#E8E2D8",
    "#f1f5f9": "#FAF7F1",
    "#f8fafc": "#FCFAF6",
    # Blues -> Terracotta / Secondary
    "#0284c7": "#B66F51",
    "#0369a1": "#A86348",
    "#f0f9ff": "#FAF7F1",
    # Greens -> PAW Success
    "#14532d": "#648166",
    "#166534": "#648166",
    "#16a34a": "#B66F51",  # use primary accent for actions
    "#bbf7d0": "#E5ECE3",
    "#86efac": "#D3DED1",
    "#f0fdf4": "#FAF7F1",
    # Reds -> PAW Danger
    "#b91c1c": "#A65F58",
    "#ef4444": "#A65F58",
    # Yellows / Ambers -> PAW Warning
    "#ca8a04": "#A47A42",
    "#92400e": "#A47A42",
    "#fcd34d": "#E7D7BC",
    "#fffbeb": "#F2E8D7",
}

for old_color, new_color in COLOR_MAP.items():
    code = code.replace(old_color, new_color)
    code = code.replace(old_color.upper(), new_color)

with open(TARGET_FILE, "w", encoding="utf-8") as f:
    f.write(code)

print("paw_console.js colors & symbols refined 100%!")
