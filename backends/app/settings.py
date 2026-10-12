"""환경 변수 설정. 값은 backends/.env 또는 프로세스 환경 변수에서 읽는다(목록: backends/.env.copy)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8",
        extra="ignore", env_ignore_empty=True,
    )

    # 생성 모델
    anthropic_api_key: SecretStr | None = None
    llm_model: str = "claude-haiku-5-5"
    llm_model_strong: str = "claude-sonnet-5-5"

    # OpenRouter: 임베딩·judge
    openrouter_key: SecretStr | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    llm_model_judge: str | None = None
    embedding_model: str = "baai/bge-m3"

    # 데이터 소스
    opendart_api_key: SecretStr | None = None
    tavily_api_key: SecretStr | None = None
    tavily_max_credits: int = 300
    law_oc: SecretStr | None = None

    # 저장소
    weaviate_url: str = "http://localhost:8090"
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr | None = None
    sqlite_path: Path = Path("data/state.sqlite")

    # 서버
    admin_token: SecretStr | None = None
    cors_origins: str = "http://localhost:5173"

    # 캐시·예산
    cache_dir: Path = Path("data/cache")
    llm_cache: bool = False
    budget_usd: float = 9.0
    request_token_budget: int = 300_000

    # 트레이싱 (langsmith가 LANGSMITH_* 환경 변수를 직접 읽는다)
    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "finsight-v2"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def resolve(self, p: Path) -> Path:
        """상대 경로는 backends/ 기준으로 푼다."""
        return p if p.is_absolute() else BACKEND_DIR / p


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    _export_langsmith_env(s)
    return s


def _export_langsmith_env(s: Settings) -> None:
    """pydantic-settings는 .env를 os.environ에 넣지 않으므로, LangSmith가 읽도록 직접 넣는다."""
    import os

    if s.langsmith_tracing and s.langsmith_api_key:
        os.environ.setdefault("LANGSMITH_TRACING", "true")
        os.environ.setdefault("LANGSMITH_API_KEY", s.langsmith_api_key.get_secret_value())
        os.environ.setdefault("LANGSMITH_PROJECT", s.langsmith_project)


def require(secret: SecretStr | None, name: str) -> str:
    if secret is None or not secret.get_secret_value():
        raise RuntimeError(f"{name}가 설정되지 않았습니다(backends/.env 확인)")
    return secret.get_secret_value()


__all__ = ["Settings", "get_settings", "require", "BACKEND_DIR", "Field"]
