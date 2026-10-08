"""Environment configuration (spec section 9.3). Every setting is optional."""

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Environment only: a .env file would make the handle key depend on the folder the
    # server starts in, and handles issued under another key read as forged.
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    ncbi_api_key: SecretStr | None = Field(default=None, alias="NCBI_API_KEY")
    ncbi_email: str | None = Field(default=None, alias="NCBI_EMAIL")
    openfda_api_key: SecretStr | None = Field(default=None, alias="OPENFDA_API_KEY")
    crossref_mailto: str | None = Field(default=None, alias="CROSSREF_MAILTO")
    handle_key_hex: SecretStr | None = Field(default=None, alias="MEDCITE_HANDLE_KEY")
    cache_path: Path | None = Field(default=None, alias="MEDCITE_CACHE_PATH")
    log_level: str = Field(default="INFO", alias="MEDCITE_LOG_LEVEL")
    tool_deadline_s: float = Field(default=45.0, alias="MEDCITE_TOOL_DEADLINE_S")
