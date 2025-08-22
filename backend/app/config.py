"""Application configuration for the Ultimate Bot backend.

This module centralizes environment configuration using Pydantic models. It
reads environment variables for settings such as the HTTP port, allowed CORS
origins, initial paper equity, and email SMTP credentials. The values are
exposed through the global ``settings`` instance. Modify ``.env.example`` to
adjust defaults when deploying your own environment.
"""

import os
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()


def _get_list(env_key: str) -> list[str]:
    """Split a comma‑separated environment variable into a list.

    Args:
        env_key: Name of the environment variable to split.

    Returns:
        A list of strings. Empty strings and whitespace are omitted.
    """
    raw = os.getenv(env_key, "")
    return [x.strip() for x in raw.split(",") if x.strip()]


class Settings(BaseModel):
    """Typed settings loaded from environment variables."""
    port: int = int(os.getenv("PORT", "8000"))
    cors_origins: list[str] = _get_list("CORS_ORIGINS")
    start_equity: float = float(os.getenv("START_EQUITY", "10000"))
    email_from: str = os.getenv("EMAIL_FROM", "no-reply@ultimate-bot.local")
    smtp_host: str | None = os.getenv("EMAIL_SMTP_HOST") or None
    smtp_user: str | None = os.getenv("EMAIL_SMTP_USER") or None
    smtp_pass: str | None = os.getenv("EMAIL_SMTP_PASS") or None

    # Synthetic top-3 depth notional (USD) used for slippage/depth gating until real depth is wired.
    synthetic_top3_notional: float = float(os.getenv("SYN_TOP3_NOTIONAL", "75000"))


settings = Settings()
