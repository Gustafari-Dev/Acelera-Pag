"""Configuração validada; o modo mock não exige credenciais externas."""

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    use_mock_llm: bool = True
    use_mock_embeddings: bool | None = None
    google_api_key: SecretStr = SecretStr("")
    gemini_model: str = "gemini-2.5-flash"
    gemini_embedding_model: str = "gemini-embedding-001"
    llm_timeout_seconds: int = Field(default=30, ge=1, le=300)
    database_url: str = "sqlite:///./data/reembolsos.db"
    chroma_persist_dir: Path = ROOT / "data/chroma"
    policy_docs_dir: Path = ROOT / "docs"
    limite_alcada_automatica: Decimal = Field(
        default=Decimal("1000.00"), gt=0, max_digits=12, decimal_places=2
    )
    autorizacao_ttl_seconds: int = Field(default=900, ge=1)
    app_timezone: str = "America/Sao_Paulo"
    seed_demo_data: bool = True
    # POST /validar aceita pareceres vindos do cliente e contornaria os agentes do backend.
    permitir_validar_externo: bool = False

    @model_validator(mode="after")
    def validate_runtime(self) -> "Settings":
        if not self.database_url.startswith("sqlite:///"):
            raise ValueError("DATABASE_URL deve usar sqlite:///caminho/do/arquivo.db.")
        if self.database_url.removeprefix("sqlite:///") == ":memory:":
            raise ValueError("Use um arquivo SQLite: as conexões são independentes por operação.")
        ZoneInfo(self.app_timezone)
        for field in ("chroma_persist_dir", "policy_docs_dir"):
            path = getattr(self, field)
            if not path.is_absolute():
                setattr(self, field, ROOT / path)
        return self

    @property
    def mock_llm(self) -> bool:
        return self.use_mock_llm or not self.google_api_key.get_secret_value().strip()

    @property
    def mock_embeddings(self) -> bool:
        # Sem credencial, toda a API pode iniciar e operar offline, mesmo com flags false.
        if not self.google_api_key.get_secret_value().strip():
            return True
        return self.use_mock_llm if self.use_mock_embeddings is None else self.use_mock_embeddings

    @property
    def database_path(self) -> Path:
        path = Path(self.database_url.removeprefix("sqlite:///"))
        return path if path.is_absolute() else ROOT / path

    def now(self) -> datetime:
        return datetime.now(ZoneInfo(self.app_timezone))
