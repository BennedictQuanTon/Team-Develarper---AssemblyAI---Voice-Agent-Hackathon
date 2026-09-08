"""Load voice profiles from YAML."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from backend.app.config import get_settings


@lru_cache
def _load_profiles(path: str) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return data


def get_voice_profile(name: str | None = None) -> dict[str, Any]:
    settings = get_settings()
    data = _load_profiles(str(settings.voice_profiles_path.resolve()))
    profiles = data.get("profiles") or {}
    chosen = name or settings.voice_profile or data.get("default_profile") or "friendly_guide"
    profile = profiles.get(chosen) or profiles.get(data.get("default_profile")) or {}
    return {
        "name": chosen,
        "cartesia_voice_id": profile.get("cartesia_voice_id")
        or settings.cartesia_voice_id
        or "",
        "speaking_rate": float(profile.get("speaking_rate") or 1.0),
        "style_prompt": str(profile.get("style_prompt") or ""),
        "answer_template": str(profile.get("answer_template") or ""),
    }
