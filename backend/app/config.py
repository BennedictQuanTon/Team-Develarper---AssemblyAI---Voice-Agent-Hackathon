from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "development")
    app_host: str = os.getenv("APP_HOST", "0.0.0.0")
    app_port: int = int(os.getenv("APP_PORT", "8000"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    allow_stub_providers: bool = os.getenv("ALLOW_STUB_PROVIDERS", "false").lower() == "true"
    assemblyai_api_key: str = os.getenv("ASSEMBLYAI_API_KEY", "")
    assemblyai_speech_model: str = os.getenv("ASSEMBLYAI_SPEECH_MODEL", "universal-3-5-pro")
    llm_provider: str = os.getenv("LLM_PROVIDER", "ollama")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen3:4b")
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_thinking: bool = os.getenv("OLLAMA_THINKING", "false").lower() == "true"
    tts_provider: str = os.getenv("TTS_PROVIDER", "kokoro")
    kokoro_model_id: str = os.getenv("KOKORO_MODEL_ID", "hexgrad/Kokoro-82M")
    kokoro_device: str = os.getenv("KOKORO_DEVICE", "auto")
    database_path: Path = Path(os.getenv("DATABASE_PATH", "var/lantern.sqlite3"))
    metrics_dir: Path = Path(os.getenv("METRICS_DIR", "var/metrics"))


settings = Settings()
