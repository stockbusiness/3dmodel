"""設定。値は環境変数からのみ読む（仕様第12章）。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

MIB = 1024 * 1024


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="APP_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    secret_key: str = Field(min_length=32)
    data_dir: Path = Path("./data")

    # 実API生成の可否。既定 false（仕様第8章）
    live_api_enabled: bool = False
    # 全体上限額（USD）。未設定なら実API生成不可（仕様第11章）
    global_cost_cap_usd: str | None = None

    session_ttl_hours: int = 12

    # 入力制限（仕様第12章）
    max_image_bytes: int = 10 * MIB
    max_image_pixels: int = 20_000_000
    max_download_bytes: int = 100 * MIB

    # 校正対象件数（仕様第10章「最初の5件（設定で変更）」）
    calibration_target_count: int = 0

    # 送信枠（仕様第9章：検証セット内の asset×provider で 2 回）
    default_quota_per_asset_provider: int = 2

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def database_url(self) -> str:
        return f"sqlite+pysqlite:///{self.db_path}"

    @property
    def objects_dir(self) -> Path:
        return self.data_dir / "objects"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.objects_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()  # type: ignore[call-arg]
    settings.ensure_dirs()
    return settings
