"""Application settings, read once from the environment / .env file."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent
FIXTURES_DIR = BACKEND_DIR / "fixtures"
CONFIG_DIR = REPO_DIR / "config"

# The only model MarketLens uses for AI features (summaries, chat, briefing).
DEFAULT_OPENAI_MODEL = "gpt-6-luna"

load_dotenv(REPO_DIR / ".env", override=False)


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    if v is None or v.strip() == "" or v.strip().startswith("your_"):
        return default
    return v.strip()


def _bool(name: str, default: bool) -> bool:
    v = _env(name)
    if v is None:
        return default
    return v.lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    # data mode: "auto" = live with fallback to bundled sample data, "live" = live only, "mock" = fixtures only
    data_mode: str = field(default_factory=lambda: (_env("DATA_MODE", "auto") or "auto").lower())
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", str(REPO_DIR / "data")) or "data"))

    # AI (OpenAI only)
    openai_api_key: str | None = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    openai_model: str = field(default_factory=lambda: _env("OPENAI_MODEL", DEFAULT_OPENAI_MODEL) or DEFAULT_OPENAI_MODEL)
    openai_base_url: str | None = field(default_factory=lambda: _env("OPENAI_BASE_URL"))

    # Data source credentials
    sec_user_agent: str | None = field(default_factory=lambda: _env("SEC_USER_AGENT"))
    fred_api_key: str | None = field(default_factory=lambda: _env("FRED_API_KEY"))
    finnhub_api_key: str | None = field(default_factory=lambda: _env("FINNHUB_API_KEY"))
    alpha_vantage_api_key: str | None = field(default_factory=lambda: _env("ALPHA_VANTAGE_API_KEY"))
    reddit_client_id: str | None = field(default_factory=lambda: _env("REDDIT_CLIENT_ID"))
    reddit_client_secret: str | None = field(default_factory=lambda: _env("REDDIT_CLIENT_SECRET"))
    reddit_user_agent: str = field(default_factory=lambda: _env("REDDIT_USER_AGENT", "MarketLens/1.0 (personal research)") or "")

    # Briefing / scheduler
    timezone: str = field(default_factory=lambda: _env("TIMEZONE", "America/New_York") or "America/New_York")
    briefing_time: str = field(default_factory=lambda: _env("BRIEFING_TIME", "07:30") or "07:30")
    scheduler_enabled: bool = field(default_factory=lambda: _bool("SCHEDULER_ENABLED", True))
    smtp_host: str | None = field(default_factory=lambda: _env("SMTP_HOST"))
    smtp_port: int = field(default_factory=lambda: int(_env("SMTP_PORT", "587") or 587))
    smtp_user: str | None = field(default_factory=lambda: _env("SMTP_USER"))
    smtp_password: str | None = field(default_factory=lambda: _env("SMTP_PASSWORD"))
    smtp_from: str | None = field(default_factory=lambda: _env("SMTP_FROM"))
    briefing_email_to: str | None = field(default_factory=lambda: _env("BRIEFING_EMAIL_TO"))

    # Misc
    allow_custom_strategies: bool = field(default_factory=lambda: _bool("ALLOW_CUSTOM_STRATEGIES", True))
    price_ttl_minutes: int = field(default_factory=lambda: int(_env("PRICE_TTL_MINUTES", "15") or 15))
    sentiment_model: str = field(default_factory=lambda: (_env("SENTIMENT_MODEL", "auto") or "auto").lower())

    @property
    def db_path(self) -> Path:
        return self.data_dir / "marketlens.db"

    @property
    def ai_enabled(self) -> bool:
        return bool(self.openai_api_key)

    def key_status(self) -> dict[str, bool]:
        return {
            "OPENAI_API_KEY": bool(self.openai_api_key),
            "SEC_USER_AGENT": bool(self.sec_user_agent),
            "FRED_API_KEY": bool(self.fred_api_key),
            "FINNHUB_API_KEY": bool(self.finnhub_api_key),
            "ALPHA_VANTAGE_API_KEY": bool(self.alpha_vantage_api_key),
            "REDDIT_CLIENT_ID": bool(self.reddit_client_id and self.reddit_client_secret),
            "SMTP_HOST": bool(self.smtp_host),
        }


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s


def reset_settings() -> None:
    get_settings.cache_clear()
