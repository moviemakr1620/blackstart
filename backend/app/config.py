from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger("blackstart.config")

# Values that shipped in examples / compose files. They are public, so Blackstart
# never signs tokens or encrypts credentials with them.
KNOWN_DEFAULT_KEYS = {
    "change-me",
    "change-me-in-production",
    "change-me-to-a-long-random-string",
}
KNOWN_DEFAULT_PASSWORDS = {"blackstart", "admin", "password", "change-me"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BLACKSTART_",
        env_file=".env",
        extra="ignore",
    )

    secret_key: str = ""
    data_dir: Path = Path("./data")
    host: str = "0.0.0.0"
    port: int = 8088
    admin_user: str = "admin"
    admin_password: str = ""
    live_actions: bool = False

    peanut_url: str = ""
    peanut_user: str = ""
    peanut_password: str = ""
    nut_host: str = ""
    nut_port: int = 3493
    nut_device: str = "ecoflow"
    nut_user: str = ""
    nut_password: str = ""

    # Filled in by bootstrap_secret_key(); never read from the environment.
    secret_source: str = ""
    legacy_secret_keys: list[str] = []

    @property
    def db_path(self) -> Path:
        return self.data_dir / "blackstart.db"

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.db_path}"

    @property
    def secret_key_file(self) -> Path:
        return self.data_dir / "secret.key"

    @property
    def initial_password_file(self) -> Path:
        return self.data_dir / "initial-admin-password.txt"


def _write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)


def bootstrap_secret_key(cfg: Settings) -> None:
    """Pick the key used for JWTs and credential encryption.

    A key from the environment is used only if it is not one of the public
    example values. Otherwise Blackstart uses (or creates) a random key in
    <data_dir>/secret.key. Rejected or older keys are kept only so existing
    encrypted credentials can be read once and re-encrypted.
    """
    configured = (cfg.secret_key or "").strip()
    legacy: list[str] = []
    if configured and configured not in KNOWN_DEFAULT_KEYS:
        cfg.secret_key = configured
        cfg.secret_source = "env"
    else:
        if configured:
            log.error(
                "BLACKSTART_SECRET_KEY is a published example value; refusing to use it. "
                "Using the generated key in %s instead.",
                cfg.secret_key_file,
            )
        path = cfg.secret_key_file
        if path.is_file() and path.read_text().strip():
            cfg.secret_key = path.read_text().strip()
        else:
            cfg.secret_key = secrets.token_urlsafe(48)
            _write_private(path, cfg.secret_key + "\n")
            log.warning("Generated a new secret key in %s", path)
        cfg.secret_source = "file"
    # If a generated key file exists but the env key is now in use (or vice
    # versa), keep the other one around for decrypting older credentials.
    if cfg.secret_source == "env" and cfg.secret_key_file.is_file():
        other = cfg.secret_key_file.read_text().strip()
        if other and other != cfg.secret_key:
            legacy.append(other)
    legacy.extend(k for k in sorted(KNOWN_DEFAULT_KEYS) if k != cfg.secret_key)
    cfg.legacy_secret_keys = legacy


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
bootstrap_secret_key(settings)
