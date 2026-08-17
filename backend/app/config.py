from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BLACKSTART_",
        env_file=".env",
        extra="ignore",
    )

    secret_key: str = "change-me-in-production"
    data_dir: Path = Path("./data")
    host: str = "0.0.0.0"
    port: int = 8088
    admin_user: str = "admin"
    admin_password: str = "blackstart"
    live_actions: bool = False

    peanut_url: str = ""
    peanut_user: str = ""
    peanut_password: str = ""
    nut_host: str = ""
    nut_port: int = 3493
    nut_device: str = "ecoflow"
    nut_user: str = ""
    nut_password: str = ""

    @property
    def db_path(self) -> Path:
        return self.data_dir / "blackstart.db"

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.db_path}"


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
