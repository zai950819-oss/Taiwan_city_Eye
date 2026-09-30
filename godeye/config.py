"""Settings loaded from environment / .env (no extra dependency)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(int(default))).lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    tdx_client_id: str = os.getenv("TDX_CLIENT_ID", "")
    tdx_client_secret: str = os.getenv("TDX_CLIENT_SECRET", "")
    force_mock: bool = _bool("GODEYE_MOCK", False)
    cctv_sources: list[str] = field(default_factory=lambda: [
        s.strip() for s in os.getenv("GODEYE_CCTV_SOURCES", "Freeway,Highway").split(",") if s.strip()
    ])
    vision_enabled: bool = _bool("GODEYE_VISION", True)
    vision_max_cameras: int = int(os.getenv("GODEYE_VISION_MAX_CAMERAS", "40"))
    vision_interval_s: int = int(os.getenv("GODEYE_VISION_INTERVAL_S", "120"))
    vision_concurrency: int = int(os.getenv("GODEYE_VISION_CONCURRENCY", "6"))
    conditions_enabled: bool = _bool("GODEYE_CONDITIONS", True)
    condition_interval_s: int = int(os.getenv("GODEYE_CONDITION_INTERVAL_S", "600"))
    cwa_api_key: str = os.getenv("CWA_API_KEY", "")
    db_path: Path = ROOT / os.getenv("GODEYE_DB", "data/godeye.db")
    retention_hours: int = int(os.getenv("GODEYE_RETENTION_HOURS", "72"))

    @property
    def has_tdx_keys(self) -> bool:
        return bool(self.tdx_client_id and self.tdx_client_secret)

    @property
    def mock(self) -> bool:
        return self.force_mock or not self.has_tdx_keys


settings = Settings()
