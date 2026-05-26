import os
import json
from typing import Optional, Any
from paperflow.config import get_config

class Cache:
    def __init__(self, base_dir: str = ""):
        self.base_dir = base_dir or os.path.join("data", "cache", get_config().project.topic_slug)
        os.makedirs(self.base_dir, exist_ok=True)

    def _get_path(self, zotero_key: str, filename: str, create: bool = True) -> str:
        key_dir = os.path.join(self.base_dir, zotero_key)
        if create:
            os.makedirs(key_dir, exist_ok=True)
        return os.path.join(key_dir, filename)

    def load_json(self, zotero_key: str, filename: str) -> Optional[Any]:
        path = self._get_path(zotero_key, filename, create=False)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return None

    def save_json(self, zotero_key: str, filename: str, data: Any):
        path = self._get_path(zotero_key, filename)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load_text(self, zotero_key: str, filename: str) -> Optional[str]:
        path = self._get_path(zotero_key, filename, create=False)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        return None

    def save_text(self, zotero_key: str, filename: str, text: str):
        path = self._get_path(zotero_key, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

cache = Cache()
