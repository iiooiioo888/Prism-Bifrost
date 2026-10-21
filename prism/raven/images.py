"""商品圖抓取。只收頁面明確標出的公開圖片，不把廣告或整頁圖片都算進來。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, urlunparse

from prism.raven.compliance import ComplianceError, SitePolicy
from prism.raven.http import PayloadTooLarge
from prism.raven.proxies import (
    AccessDenied,
    BytesTransport,
    EgressBytes,
    ProxyAttempt,
    ProxyPoolConfig,
    ProxyPoolExhausted,
    fetch_bytes_with_pool,
)
from prism.raven.robots import robots_allows

MAX_PRODUCT_IMAGES = 4


class ImageCrawlError(ValueError):
    """這次抓取不能當成成功。"""

    def __init__(self, message: str, attempts: tuple[ProxyAttempt, ...] = ()) -> None:
        self.attempts = attempts
        super().__init__(message)


@dataclass(frozen=True)
class FetchedImage:
    source_page_url: str
    image_url: str
    content_type: str
    body: bytes
    sha256: str
    acquisition_mode: str


@dataclass(frozen=True)
class RejectedImage:
    url: str
    reason: str


@dataclass(frozen=True)
class CrawlBatch:
    page_url: str
    images: tuple[FetchedImage, ...]
    rejected: tuple[RejectedImage, ...]
    note: str
    attempts: tuple[ProxyAttempt, ...]


class _ProductImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key.lower(): (value or "") for key, value in attrs}
        if tag == "meta" and attr.get("property", "").lower() == "og:image" and attr.get("content", "").strip():
            self.found.append(attr["content"].strip())
            return
        if tag == "meta" and attr.get("name", "").lower() == "og:image" and attr.get("content", "").strip():
            self.found.append(attr["content"].strip())
            return
        if tag == "link" and _marked_image(attr) and attr.get("href", "").strip():
            self.found.append(attr["href"].strip())
            return
        if tag != "img":
            return
        if attr.get("itemprop", "").lower() == "image" and attr.get("src", "").strip():
            self.found.append(attr["src"].strip())
            return
        if "data-product-image" in attr:
            value = attr["data-product-image"].strip() or attr.get("src", "").strip()
            if value:
                self.found.append(value)


def extract_product_image_urls(html: str, page_url: str) -> list[str]:
    """只取 og:image、itemprop=image、data-product-image。普通 img 略過。"""
    parser = _ProductImageParser()
    parser.feed(html)
    found: list[str] = []
    seen: set[str] = set()
    for raw in parser.found:
        normalized = _normalize_url(page_url, raw)
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        found.append(normalized)
    return found


def sniff_image(body: bytes) -> str | None:
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if body.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if body.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(body) >= 12 and body.startswith(b"RIFF") and body[8:12] == b"WEBP":
        return "image/webp"
    return None


async def crawl_product_images(
    page_url: str,
    *,
    policy: SitePolicy,
    pool: ProxyPoolConfig,
    transport: BytesTransport,
    transport_failures: dict[str, int] | None = None,
) -> CrawlBatch:
    failures = transport_failures if transport_failures is not None else {}
    attempts: list[ProxyAttempt] = []
    robots_cache: dict[str, str] = {}
    try:
        await _ensure_robots(page_url, policy, pool, transport, failures, attempts, robots_cache)
        page = await _fetch(page_url, policy, pool, transport, failures, attempts)
    except AccessDenied as exc:
        raise AccessDenied(exc.proxy_id, exc.status_code, "目標拒絕，已停止，沒有改抓其餘圖片，也沒有換 IP", tuple(attempts)) from exc
    except ProxyPoolExhausted as exc:
        raise ProxyPoolExhausted(str(exc), tuple(attempts)) from exc
    except ComplianceError as exc:
        exc.attempts = tuple(getattr(exc, "attempts", ()) or attempts)  # type: ignore[attr-defined]
        raise
    except PayloadTooLarge as exc:
        raise ImageCrawlError(str(exc), tuple(attempts)) from exc
    if page.status_code in {301, 302, 303, 307, 308}:
        raise ImageCrawlError("回應是重新導向，沒有跟隨。下一跳必須另行通過站點規則", tuple(attempts))
    if page.status_code != 200:
        raise ImageCrawlError(f"頁面回應 {page.status_code}，沒有保存圖片", tuple(attempts))
    if sniff_image(page.body) is not None:
        stored = _accept_image(page_url, page_url, page)
        return CrawlBatch(page_url, (stored,), (), "", tuple(attempts))
    if b"<" not in page.body[:512]:
        raise ImageCrawlError("回應不是公開頁面，也不是圖片", tuple(attempts))
    urls = extract_product_image_urls(page.body.decode("utf-8", errors="replace"), page_url)
    chosen = urls[:MAX_PRODUCT_IMAGES]
    rejected = [RejectedImage(url, "超過單頁 4 張上限，未抓取") for url in urls[MAX_PRODUCT_IMAGES:]]
    images: list[FetchedImage] = []
    for image_url in chosen:
        try:
            fetched = await _fetch_one(page_url, image_url, policy, pool, transport, failures, attempts, robots_cache)
        except AccessDenied as exc:
            raise AccessDenied(exc.proxy_id, exc.status_code, "目標拒絕，已停止，沒有改抓其餘圖片，也沒有換 IP", tuple(attempts)) from exc
        if isinstance(fetched, RejectedImage):
            rejected.append(fetched)
            continue
        images.append(fetched)
    note = ""
    if not images and not rejected:
        note = "頁面沒有標成商品圖的圖片"
    elif not images:
        note = "沒有可保存的商品圖"
    return CrawlBatch(page_url, tuple(images), tuple(rejected), note, tuple(attempts))


def _marked_image(attr: dict[str, str]) -> bool:
    rel = {part.strip().lower() for part in attr.get("rel", "").split()}
    return "image_src" in rel or attr.get("itemprop", "").lower() == "image"


def _normalize_url(page_url: str, raw: str) -> str | None:
    joined = urljoin(page_url, raw.strip())
    parsed = urlparse(joined)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    path = parsed.path or "/"
    return urlunparse(parsed._replace(path=path, fragment=""))


def _host_allowed(image_url: str, page_url: str, policy: SitePolicy) -> bool:
    host = (urlparse(image_url).hostname or "").lower()
    page_host = (urlparse(page_url).hostname or "").lower()
    if host == page_host:
        return True
    return bool(policy.allowed_domains.get(host))


def _remember(failures: dict[str, int], new_attempts: tuple[ProxyAttempt, ...]) -> None:
    for item in new_attempts:
        if item.outcome == "transport_failure":
            failures[item.proxy_id] = failures.get(item.proxy_id, 0) + 1
        elif item.outcome in {"success", "access_denied"}:
            failures[item.proxy_id] = 0


async def _fetch(
    url: str,
    policy: SitePolicy,
    pool: ProxyPoolConfig,
    transport: BytesTransport,
    failures: dict[str, int],
    attempts: list[ProxyAttempt],
) -> EgressBytes:
    try:
        result = await fetch_bytes_with_pool(
            url,
            pool,
            assert_allowed=policy.assert_allowed,
            transport=transport,
            transport_failures=failures,
        )
    except AccessDenied as exc:
        _remember(failures, exc.attempts)
        attempts.extend(exc.attempts)
        raise
    except ProxyPoolExhausted as exc:
        _remember(failures, exc.attempts)
        attempts.extend(exc.attempts)
        raise
    _remember(failures, result.attempts)
    attempts.extend(result.attempts)
    return result


async def _load_robots(
    url: str,
    policy: SitePolicy,
    pool: ProxyPoolConfig,
    transport: BytesTransport,
    failures: dict[str, int],
    attempts: list[ProxyAttempt],
    cache: dict[str, str],
) -> str | None:
    """回傳規則正文。None 表示這個網域不必遵守 robots。"""
    if not policy.respect_robots(url):
        return None
    parsed = urlparse(url)
    robots_url = urlunparse(parsed._replace(path="/robots.txt", params="", query="", fragment=""))
    if robots_url in cache:
        return cache[robots_url]
    try:
        result = await _fetch(robots_url, policy, pool, transport, failures, attempts)
    except ProxyPoolExhausted as exc:
        raise ImageCrawlError("無法讀取 robots.txt，已停止", tuple(attempts)) from exc
    if result.status_code == 404:
        body = ""
    elif result.status_code != 200:
        raise ImageCrawlError(f"無法確認 robots.txt（{result.status_code}），已停止", tuple(attempts))
    else:
        body = result.body.decode("utf-8", errors="replace")
    cache[robots_url] = body
    return body


async def _ensure_robots(
    url: str,
    policy: SitePolicy,
    pool: ProxyPoolConfig,
    transport: BytesTransport,
    failures: dict[str, int],
    attempts: list[ProxyAttempt],
    cache: dict[str, str],
) -> None:
    body = await _load_robots(url, policy, pool, transport, failures, attempts, cache)
    if body is None:
        return
    path = urlparse(url).path or "/"
    if not robots_allows(body, path):
        error = ComplianceError(f"robots.txt 不允許這個路徑，已拒絕：{url}")
        error.attempts = tuple(attempts)  # type: ignore[attr-defined]
        raise error


async def _fetch_one(
    page_url: str,
    image_url: str,
    policy: SitePolicy,
    pool: ProxyPoolConfig,
    transport: BytesTransport,
    failures: dict[str, int],
    attempts: list[ProxyAttempt],
    cache: dict[str, str],
) -> FetchedImage | RejectedImage:
    if not _host_allowed(image_url, page_url, policy):
        return RejectedImage(image_url, "圖片網域未允許，未抓取")
    robots_body = await _load_robots(image_url, policy, pool, transport, failures, attempts, cache)
    if robots_body is not None and not robots_allows(robots_body, urlparse(image_url).path or "/"):
        return RejectedImage(image_url, "robots.txt 不允許這個路徑，未抓取")
    try:
        policy.assert_allowed(image_url)
    except ComplianceError as exc:
        return RejectedImage(image_url, str(exc))
    try:
        result = await _fetch(image_url, policy, pool, transport, failures, attempts)
    except PayloadTooLarge:
        return RejectedImage(image_url, "圖片超過 2MB，未保存")
    except ProxyPoolExhausted:
        return RejectedImage(image_url, "出口傳輸中斷，未保存")
    if result.status_code in {301, 302, 303, 307, 308}:
        return RejectedImage(image_url, "圖片回應是重新導向，沒有跟隨")
    if result.status_code != 200:
        return RejectedImage(image_url, f"圖片回應 {result.status_code}，未保存")
    content_type = sniff_image(result.body)
    if content_type is None:
        return RejectedImage(image_url, "不是 png、jpeg、gif 或 webp，未保存")
    return _accept_image(page_url, image_url, result, content_type)


def _accept_image(page_url: str, image_url: str, result: EgressBytes, content_type: str | None = None) -> FetchedImage:
    sniffed = content_type or sniff_image(result.body)
    if sniffed is None:
        raise ImageCrawlError("回應不是 png、jpeg、gif 或 webp", result.attempts)
    return FetchedImage(
        source_page_url=page_url,
        image_url=image_url,
        content_type=sniffed,
        body=result.body,
        sha256=hashlib.sha256(result.body).hexdigest(),
        acquisition_mode="fallback" if result.proxy_id != "direct" else "live",
    )
