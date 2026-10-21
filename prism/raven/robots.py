"""robots.txt 規則。只判斷允不允許，不嘗試繞過。"""

from __future__ import annotations

import re
from urllib.parse import urlsplit


def robots_target(url: str) -> str:
    """要比對的路徑。查詢字串一併納入，才能認得禁止 ? 的規則。"""
    parsed = urlsplit(url)
    path = parsed.path or "/"
    if parsed.query:
        return f"{path}?{parsed.query}"
    return path


def robots_allows(text: str, path: str, agent: str = "prismbot") -> bool:
    """最長規則勝出；同長度時 Allow 勝出。空的 Disallow 不擋任何路徑。* 與結尾 $ 依字面規則展開。"""
    groups = _groups(text)
    chosen = _rules_for(groups, agent.lower())
    if chosen is None:
        chosen = _rules_for(groups, "*")
    if not chosen:
        return True
    target = path or "/"
    if not target.startswith("/"):
        target = "/" + target
    best = -1
    allowed = True
    for directive, pattern in chosen:
        if not _matches(pattern, target):
            continue
        length = len(pattern)
        if length > best or (length == best and directive == "allow"):
            best = length
            allowed = directive == "allow"
    return allowed


def _matches(pattern: str, target: str) -> bool:
    if not pattern:
        return False
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    expression = "".join(".*" if char == "*" else re.escape(char) for char in body)
    if anchored:
        expression += "$"
    return re.match(expression, target) is not None


def _groups(text: str) -> list[tuple[str, list[tuple[str, str]]]]:
    groups: list[tuple[str, list[tuple[str, str]]]] = []
    agents: list[str] = []
    rules: list[tuple[str, str]] = []

    def flush() -> None:
        nonlocal agents, rules
        for name in agents:
            groups.append((name.lower(), list(rules)))
        agents = []
        rules = []

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            if rules:
                flush()
            agents.append(value)
        elif key in {"allow", "disallow"} and agents:
            rules.append((key, value))
    if agents:
        flush()
    return groups


def _rules_for(
    groups: list[tuple[str, list[tuple[str, str]]]],
    agent: str,
) -> list[tuple[str, str]] | None:
    for name, rules in groups:
        if name == agent:
            return rules
    return None
