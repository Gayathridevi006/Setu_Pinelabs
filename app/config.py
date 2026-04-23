from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./setu_payments.db"
    DEBUG: bool = False
    APP_VERSION: str = "1.0.0"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
