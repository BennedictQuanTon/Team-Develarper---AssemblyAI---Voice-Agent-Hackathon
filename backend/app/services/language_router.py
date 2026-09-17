from __future__ import annotations


SUPPORTED_TTS_LANGUAGES = {"en", "es", "fr", "hi", "it", "ja", "zh", "pt-BR"}


def resolve_language(code: str | None) -> tuple[str, bool]:
    language = code or "en"
    return language, language in SUPPORTED_TTS_LANGUAGES or language.split("-")[0] in SUPPORTED_TTS_LANGUAGES
