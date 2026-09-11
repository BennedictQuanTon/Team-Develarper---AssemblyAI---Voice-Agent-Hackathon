from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    assemblyai_api_key: str = ""
    gemini_api_key: str = ""
    cartesia_api_key: str = ""
    cartesia_voice_id: str = ""
    voice_profile: str = "friendly_guide"
    agent_mode: str = "waiter"  # waiter | rag (legacy travel FAQ)
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_rpm: int = 15  # per-minute request cap for the free/tiered Gemini plan
    assemblyai_speech_model: str = "universal-3-5-pro"
    assemblyai_mode: str = "min_latency"

    chroma_persist_dir: Path = ROOT_DIR / "data" / "chroma"
    bm25_index_path: Path = ROOT_DIR / "data" / "bm25" / "index.pkl"
    danang_json_path: Path = ROOT_DIR / "data" / "danang_en" / "documents.json"
    metrics_dir: Path = ROOT_DIR / "reports"
    voice_profiles_path: Path = ROOT_DIR / "config" / "voice_profiles.yaml"

    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"

    @property
    def keys_configured(self) -> dict[str, bool]:
        return {
            "assemblyai": bool(self.assemblyai_api_key.strip()),
            "gemini": bool(self.gemini_api_key.strip()),
            "cartesia": bool(self.cartesia_api_key.strip()),
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
