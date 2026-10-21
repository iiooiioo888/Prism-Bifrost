"""原始回應物件存儲。預設落在本地目錄，鍵即 raw_ref。"""

from __future__ import annotations

import hashlib
from pathlib import Path


class ObjectStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, payload: bytes, hint: str = "") -> str:
        digest = hashlib.sha256(payload).hexdigest()
        prefix = "images" if hint == "image" else "raw"
        key = f"{prefix}/{digest[:2]}/{digest}"
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(payload)
        return key

    def get(self, key: str) -> bytes:
        if not key or ".." in Path(key).parts:
            raise FileNotFoundError(key)
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root.resolve()) or not path.is_file():
            raise FileNotFoundError(key)
        return path.read_bytes()
