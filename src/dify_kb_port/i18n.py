"""Screen language: --lang > DIFY_KB_PORT_LANG > LC_ALL > LC_MESSAGES > LANG; anything but Korean is English."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Mapping

LANGS = ("en", "ko")
_LOCALES = Path(__file__).parent / "locales"
_current = "en"


def _normalise(value: str | None) -> str | None:
    if not value:
        return None
    return "ko" if value.strip().lower().startswith("ko") else "en"


def resolve_lang(cli_value: str | None, env: Mapping[str, str]) -> str:
    """The first source that is set decides. Values other than ko* mean English."""
    for raw in (cli_value, env.get("DIFY_KB_PORT_LANG"), env.get("LC_ALL"), env.get("LC_MESSAGES"), env.get("LANG")):
        lang = _normalise(raw)
        if lang:
            return lang
    return "en"


@lru_cache(maxsize=None)
def catalog(lang: str) -> dict[str, str]:
    return json.loads((_LOCALES / f"{lang}.json").read_text(encoding="utf-8"))


def set_lang(lang: str) -> None:
    global _current
    _current = lang if lang in LANGS else "en"


def current_lang() -> str:
    return _current


def t(key: str, /, **kw: object) -> str:
    text = catalog(_current).get(key) or catalog("en")[key]
    return text.format(**kw) if kw else text
