"""Configuração de ambiente validada; segredos não aparecem em representações."""

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Carrega configurações das variáveis HIVEMIND_* ou de um arquivo .env local."""

    model_config = SettingsConfigDict(
        env_prefix="HIVEMIND_",
        env_file=".env",
        extra="ignore",
        hide_input_in_errors=True,
    )

    demo_mode: bool = True
    api_token: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    google_api_key: SecretStr | None = None
    claude_model: str = "claude-sonnet-5-5"
    openai_model: str = "gpt-6-luna"
    gemini_model: str = "gemini-3.8-flash"
    agent_timeout_seconds: float = Field(default=30, ge=0.1, le=300)
    max_cycles: int = Field(default=2, ge=1, le=10)
    memory_path: str = "data/chroma"
    graph_path: str = "data/knowledge_graph.json"
    memory_top_k: int = Field(default=3, ge=1, le=20)
    max_tasks: int = Field(default=100, ge=1, le=10000)
    max_concurrent_tasks: int = Field(default=4, ge=1, le=100)
    shutdown_timeout_seconds: float = Field(default=10, ge=0.1, le=60)

    @field_validator(
        "api_token",
        "anthropic_api_key",
        "openai_api_key",
        "google_api_key",
        mode="before",
    )
    @classmethod
    def empty_secret(cls, value: object) -> object:
        """Trata variáveis opcionais vazias do Compose como credenciais ausentes."""
        return None if value == "" else value

    @model_validator(mode="after")
    def validate_security(self) -> "Settings":
        """Exige autenticação na API antes de habilitar provedores pagos."""
        for secret in (
            self.api_token,
            self.anthropic_api_key,
            self.openai_api_key,
            self.google_api_key,
        ):
            if secret is not None and not secret.get_secret_value().strip():
                raise ValueError("Credenciais configuradas não podem estar vazias")
        if self.api_token is not None and len(self.api_token.get_secret_value()) < 32:
            raise ValueError("HIVEMIND_API_TOKEN deve ter pelo menos 32 caracteres")
        if not self.demo_mode and self.api_token is None:
            raise ValueError("Modo real exige HIVEMIND_API_TOKEN")
        if self.max_concurrent_tasks > self.max_tasks:
            raise ValueError("max_concurrent_tasks não pode exceder max_tasks")
        return self
