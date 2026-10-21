"""採集合規閘門。只做拒絕，不做繞過。"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


class ComplianceError(RuntimeError):
    """站點規則或路徑政策拒絕採集。"""


@dataclass(frozen=True)
class SitePolicy:
    allow_by_default: bool
    respect_robots_default: bool
    allowed_domains: dict[str, bool]
    robots_domains: dict[str, bool]
    blocked_fragments: tuple[str, ...]

    @classmethod
    def from_config(cls, payload: dict) -> "SitePolicy":
        defaults = payload.get("defaults", {})
        allowed: dict[str, bool] = {}
        robots: dict[str, bool] = {}
        for site in payload.get("sites", []):
            domain = str(site["domain"]).lower()
            allowed[domain] = bool(site.get("allow_fetch", False))
            robots[domain] = bool(site.get("respect_robots", True))
        fragments = tuple(str(item).lower() for item in payload.get("blocked_path_fragments", []))
        return cls(
            allow_by_default=bool(defaults.get("allow_fetch", False)),
            respect_robots_default=bool(defaults.get("respect_robots", True)),
            allowed_domains=allowed,
            robots_domains=robots,
            blocked_fragments=fragments,
        )

    def assert_allowed(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https", "file"}:
            raise ComplianceError(f"不接受的網址協定：{parsed.scheme or '空'}")
        path = (parsed.path or "").lower()
        query = (parsed.query or "").lower()
        haystack = f"{path}?{query}"
        for fragment in self.blocked_fragments:
            if fragment in haystack:
                raise ComplianceError(f"路徑命中禁止片段 {fragment}，已拒絕：{url}")
        if parsed.scheme == "file":
            return
        host = (parsed.hostname or "").lower()
        if host not in self.allowed_domains:
            if not self.allow_by_default:
                raise ComplianceError(f"網域未登記於站點規則，已拒絕：{host}")
            return
        if not self.allowed_domains[host]:
            raise ComplianceError(f"站點規則拒絕採集：{host}")

    def respect_robots(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return self.robots_domains.get(host, self.respect_robots_default)
