"""公開頁面的直連。不帶 Cookie、不偽裝瀏覽器、不跟隨重新導向。"""

from __future__ import annotations

import httpx

from prism.raven.proxies import TransportError

USER_AGENT = "PrismBot/1.0 (public product images; no login; no cookies)"
MAX_RESPONSE_BYTES = 2_000_000


class PayloadTooLarge(RuntimeError):
    """目標有回應，但本體超過上限。這不是出口故障，不得換 IP 再抓。"""


async def fetch_public_bytes(url: str, proxy_url: str | None) -> tuple[int, bytes]:
    timeout = httpx.Timeout(10.0, connect=5.0)
    try:
        async with httpx.AsyncClient(
            proxy=proxy_url,
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,image/png,image/jpeg,image/webp,image/gif;q=0.9",
            },
        ) as client:
            async with client.stream("GET", url) as response:
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_RESPONSE_BYTES:
                        raise PayloadTooLarge("回應超過 2MB，已停止")
                    chunks.append(chunk)
                return response.status_code, b"".join(chunks)
    except PayloadTooLarge:
        raise
    except httpx.HTTPError as exc:
        raise TransportError("出口傳輸失敗") from exc
