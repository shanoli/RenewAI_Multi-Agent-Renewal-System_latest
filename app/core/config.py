import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash-lite"
    jwt_secret_key: str = "changeme"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440
    sqlite_db_path: str = os.path.abspath("./data/renewai.db")

    chroma_db_path: str = "./data/chroma_db"
    app_host: str = "0.0.0.0"
    app_port: int = 8090
    debug: bool = True
    embedding_model: str = "models/text-embedding-004"
    chroma_telemetry_gather: bool = False

    # Twilio WhatsApp Configuration
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = "whatsapp:+14155238886"
    twilio_content_sid: str = ""

    # SendGrid Email Configuration
    sendgrid_api_key: str = ""
    sendgrid_from_email: str = "noreply@yourdomain.com"

    # Allow extra env vars (e.g. LANGSMITH_*) without causing errors
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache()
def get_settings() -> Settings:
    return Settings()
