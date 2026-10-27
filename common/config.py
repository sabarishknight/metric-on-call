"""Central configuration. Everything is overridable by environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DUCKDB_PATH = DATA_DIR / "harbour_lane.duckdb"
CSV_DIR = DATA_DIR / "csv"
TRUTH_PATH = DATA_DIR / "incident_truth.json"
INCIDENTS_YAML = ROOT / "generator" / "incidents.yaml"


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split("#", 1)[0].strip()
        os.environ.setdefault(key.strip(), value)


_load_dotenv()


@dataclass(frozen=True)
class Settings:
    warehouse: str = field(default_factory=lambda: os.getenv("WAREHOUSE", "duckdb"))
    model_provider: str = field(default_factory=lambda: os.getenv("MODEL_PROVIDER", "scripted"))
    ollama_model: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL", "qwen3.5:9b"))
    ollama_base_url: str = field(
        default_factory=lambda: os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    )
    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-2.5-flash"))
    max_steps: int = field(default_factory=lambda: int(os.getenv("MAX_STEPS", "40")))
    max_skeptic_rounds: int = field(
        default_factory=lambda: int(os.getenv("MAX_SKEPTIC_ROUNDS", "2"))
    )
    # hard caps applied by every tool (see tools/guards.py)
    max_rows_returned: int = 50
    max_bytes_billed: int = field(
        default_factory=lambda: int(os.getenv("BQ_MAX_BYTES_BILLED", "1000000000"))
    )


def get_settings() -> Settings:
    return Settings()
