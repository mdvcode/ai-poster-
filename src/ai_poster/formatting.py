"""A small post format: plain text, line breaks, bullets and **bold** spans.

Telegram receives explicit entities, never model-generated HTML or parse_mode.
"""

import re

from ai_poster.telegram import utf16_len

BOLD = re.compile(r"(?<!\*)\*\*([^\n*]+)\*\*(?!\*)")


def render_post(body: str) -> tuple[str, list[dict]]:
    parts, entities = [], []
    cursor = offset = 0
    for match in BOLD.finditer(body):
        if not match[1].strip():
            continue
        prefix = body[cursor : match.start()]
        parts.extend((prefix, match[1]))
        offset += utf16_len(prefix)
        length = utf16_len(match[1])
        entities.append({"type": "bold", "offset": offset, "length": length})
        offset += length
        cursor = match.end()
    parts.append(body[cursor:])
    return "".join(parts), entities


def post_kwargs(body: str):
    text, entities = render_post(body)
    return text, {"entities": entities} if entities else {}
