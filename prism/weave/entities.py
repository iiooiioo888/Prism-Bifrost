"""實體別名的規則連結。向量相似只進入人工佇列。"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class EntityRecord:
    entity_id: str
    name: str
    aliases: tuple[str, ...]
    description: str = ""


def _latin_boundary(text: str, alias: str) -> bool:
    pattern = rf"(?i)(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])"
    return re.search(pattern, text) is not None


def alias_in_text(text: str, alias: str) -> bool:
    cleaned = alias.strip()
    if len(cleaned) < 2 or not text:
        return False
    if re.search(r"[A-Za-z]", cleaned):
        return _latin_boundary(text, cleaned)
    return cleaned in text


def match_entities(text: str, entities: list[EntityRecord]) -> list[tuple[EntityRecord, str]]:
    """回傳精確別名命中。較長別名優先，避免短別名搶先覆蓋同一段文字的解釋。"""
    hits: list[tuple[EntityRecord, str]] = []
    occupied: list[tuple[int, int]] = []
    candidates: list[tuple[int, EntityRecord, str]] = []
    for entity in entities:
        names = (entity.name, *entity.aliases)
        for name in names:
            if alias_in_text(text, name):
                candidates.append((len(name), entity, name))
    candidates.sort(key=lambda item: item[0], reverse=True)
    seen: set[str] = set()
    for _, entity, name in candidates:
        if entity.entity_id in seen:
            continue
        start = text.find(name)
        end = start + len(name)
        if start >= 0 and any(not (end <= left or start >= right) for left, right in occupied):
            continue
        seen.add(entity.entity_id)
        if start >= 0:
            occupied.append((start, end))
        hits.append((entity, name))
    return hits


def entity_similarity(text: str, description: str) -> float:
    from prism.bridge.normalize import text_similarity

    if not description.strip():
        return 0.0
    return text_similarity(text, description)
