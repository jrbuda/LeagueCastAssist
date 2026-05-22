from __future__ import annotations

import html
import re


class TooltipFormatter:
    """Converts Riot tooltip markup into simple Qt rich text."""

    COLORED_TAGS = {
        "magicdamage": "#7aa2ff",
        "physicaldamage": "#ff9966",
        "truedamage": "#f5f5f5",
        "scaleap": "#7aa2ff",
        "scalead": "#ff9966",
        "status": "#d6b35a",
        "shield": "#70d6ff",
        "speed": "#80d878",
        "healing": "#80d878",
        "spellpassive": "#d6b35a",
        "recast": "#d6b35a",
        "attention": "#d6b35a",
        "active": "#d6b35a",
        "passive": "#d6b35a",
        "rules": "#9aa4b2",
        "raritygeneric": "#d6b35a",
    }

    TAG_PATTERN = re.compile(r"</?([a-zA-Z][a-zA-Z0-9]*)[^>]*>")
    PLACEHOLDER_PATTERN = re.compile(r"(@[A-Za-z0-9_:.+*\-/]+@|{{\s*[^}]+\s*}})")
    ICON_PATTERN = re.compile(r"%i:[^%]+%")

    def to_rich_text(self, raw_text: str | None) -> str:
        if not raw_text:
            return ""

        raw_text = self.ICON_PATTERN.sub("", raw_text)
        raw_text = self.PLACEHOLDER_PATTERN.sub("", raw_text)
        raw_text = remove_empty_tags(raw_text)
        raw_text = raw_text.replace("% for seconds", "for seconds")
        raw_text = raw_text.replace("+%", "")
        return f"<div>{self._convert_tags(raw_text)}</div>"

    def _convert_tags(self, text: str) -> str:
        parts: list[str] = []
        cursor = 0

        for match in self.TAG_PATTERN.finditer(text):
            parts.append(html.escape(text[cursor : match.start()], quote=False))

            full = match.group(0)
            tag = match.group(1).lower()
            closing = full.startswith("</")

            if tag == "br":
                parts.append("<br>")
            elif tag in self.COLORED_TAGS:
                if closing:
                    parts.append("</span>")
                else:
                    color = self.COLORED_TAGS[tag]
                    parts.append(f'<span style="color: {color}; font-weight: 600;">')

            cursor = match.end()

        parts.append(html.escape(text[cursor:], quote=False))
        return "".join(parts)


def remove_empty_tags(text: str) -> str:
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r"<([A-Za-z][A-Za-z0-9]*)[^>]*>\s*</\1>", "", text)
    return text
