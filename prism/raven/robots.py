"""robots.txt 的字面前綴規則。只判斷允不允許，不嘗試繞過。"""

from __future__ import annotations


def robots_allows(text: str, path: str, agent: str = "prismbot") -> bool:
    """最長前綴勝出；同長度時 Allow 勝出。空的 Disallow 不擋任何路徑。"""
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
    for directive, prefix in chosen:
        if not prefix or not target.startswith(prefix):
            continue
        length = len(prefix)
        if length > best or (length == best and directive == "allow"):
            best = length
            allowed = directive == "allow"
    return allowed


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
