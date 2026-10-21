"""網址歸一化、正文雜湊與相似雜湊。"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import Counter
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "igshid",
    "spm",
    "ref_src",
    "feature",
}
TRACKING_PREFIXES = ("utm_",)


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if not scheme or not host and scheme != "file":
        return url.strip()
    port = parts.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    else:
        netloc = host
    query_pairs = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered in TRACKING_KEYS or lowered.startswith(TRACKING_PREFIXES):
            continue
        query_pairs.append((key, value))
    query_pairs.sort()
    path = parts.path or ""
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return urlunsplit((scheme, netloc, path, urlencode(query_pairs), ""))


def normalize_body(body: str) -> str:
    text = unicodedata.normalize("NFKC", body or "")
    text = text.casefold()
    text = re.sub(r"\s+", " ", text).strip()
    return text


def content_hash(body: str) -> str:
    return hashlib.sha256(normalize_body(body).encode("utf-8")).hexdigest()


def char_bigrams(text: str) -> Counter[str]:
    compact = re.sub(r"\s+", "", normalize_body(text))
    if not compact:
        return Counter()
    if len(compact) == 1:
        return Counter([compact])
    return Counter(compact[index : index + 2] for index in range(len(compact) - 1))


def cosine_counts(left: Counter[str], right: Counter[str]) -> float:
    if not left or not right:
        return 0.0
    dot = sum(left[key] * right[key] for key in left.keys() & right.keys())
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (left_norm * right_norm)))


def text_similarity(left: str, right: str) -> float:
    return cosine_counts(char_bigrams(left), char_bigrams(right))


def simhash(text: str, bits: int = 64) -> int:
    weights = [0] * bits
    tokens = char_bigrams(text)
    if not tokens:
        return 0
    for token, weight in tokens.items():
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        hashed = int.from_bytes(digest, "big")
        for index in range(bits):
            if hashed & (1 << index):
                weights[index] += weight
            else:
                weights[index] -= weight
    value = 0
    for index, weight in enumerate(weights):
        if weight > 0:
            value |= 1 << index
    return value


def simhash_similarity(left: int, right: int, bits: int = 64) -> float:
    distance = (left ^ right).bit_count()
    return 1.0 - (distance / bits)


class BloomFilter:
    """網址級去重的記憶體內布隆過濾器。命中後仍須回查精確集合。"""

    def __init__(self, size: int, hashes: int = 4) -> None:
        if size < 64:
            raise ValueError("布隆過濾器容量過小")
        self.size = size
        self.hashes = hashes
        self.bits = bytearray((size + 7) // 8)

    def _indexes(self, key: str) -> list[int]:
        digest = hashlib.sha256(key.encode("utf-8")).digest()
        first = int.from_bytes(digest[:8], "big")
        second = int.from_bytes(digest[8:16], "big") or 1
        return [(first + index * second) % self.size for index in range(self.hashes)]

    def add(self, key: str) -> None:
        for index in self._indexes(key):
            self.bits[index // 8] |= 1 << (index % 8)

    def __contains__(self, key: str) -> bool:
        return all(self.bits[index // 8] & (1 << (index % 8)) for index in self._indexes(key))
