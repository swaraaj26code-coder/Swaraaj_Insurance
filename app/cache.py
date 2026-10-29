"""Disk cache for external lookups so re-running a recorded sweep is free and
repeatable. Cached entries keep their original retrieval date."""

import hashlib
import json

from . import config


def _path(namespace: str, key: str):
    digest = hashlib.sha1(key.encode()).hexdigest()[:16]
    folder = config.CACHE_DIR / namespace
    folder.mkdir(exist_ok=True)
    return folder / f"{digest}.json"


def get(namespace: str, key: str):
    p = _path(namespace, key)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def put(namespace: str, key: str, value):
    _path(namespace, key).write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    return value
