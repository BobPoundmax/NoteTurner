from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_CONFIG_DIR = Path(__file__).resolve().parent


@lru_cache
def load_yaml(name: str) -> dict[str, Any]:
    path = _CONFIG_DIR / name
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data or {}
