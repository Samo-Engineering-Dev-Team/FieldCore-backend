from pydantic_settings import BaseSettings
from pydantic import Field, field_validator
from pathlib import Path

# Load .env early so AppSettings picks up values when imported in different contexts.
# Use python-dotenv if available; otherwise rely on pydantic's env_file setting.
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parents[1] / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
    else:
        # fallback: attempt to load default .env from cwd
        load_dotenv()
except Exception:
    # dotenv not installed or failed to load — pydantic will still attempt env_file
    pass


class AppSettings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Database
    DB_HOST: str = ""
    DB_USER: str = ""
    DB_PASSWORD: str = ""
    DB_PORT: int = 5432
    DB_NAME: str = ""

    # Supabase Storage
    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_KEY: str = ""
    SUPABASE_STORAGE_BUCKET: str = "attachments"

    # Security
    JWT_TOKEN_EXPIRE_MINUTES: int = 60
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    JWT_SECRET_KEY: str = Field(..., min_length=32)
    JWT_ALGORITHM: str = "HS256"
    ALLOWED_ORIGINS: str = Field(default="http://localhost:3000,http://localhost:5173")

    # Presence backend (db | redis). If 'redis' and REDIS_URL is set, presence uses Redis for heartbeats.
    PRESENCE_BACKEND: str = Field(default="db", description="Storage for presence: 'db' or 'redis'")
    REDIS_URL: str | None = Field(default=None, description="Optional Redis URL for presence/pubsub (e.g. redis://host:6379/0)")
    PRESENCE_REDIS_TTL_SECONDS: int = Field(default=300, description="How long (s) a heartbeat is considered valid in Redis")
    PRESENCE_PUBSUB_CHANNEL: str = Field(default="presence_events", description="Redis pubsub channel for presence events")
    PRESENCE_REDIS_CONNECT_TIMEOUT_SECONDS: int = Field(
        default=5,
        description="Redis connect timeout (seconds) for presence operations",
    )
    PRESENCE_REDIS_SOCKET_TIMEOUT_SECONDS: int = Field(
        default=5,
        description="Redis command socket timeout (seconds) for presence operations",
    )
    PRESENCE_REDIS_RETRY_COOLDOWN_SECONDS: int = Field(
        default=60,
        description="Cooldown (seconds) before retrying Redis after a connection/read failure",
    )

    # Email / MS Exchange SMTP
    # For Exchange Online (Microsoft 365): SMTP_HOST=smtp.office365.com, SMTP_PORT=587
    # For on-premise Exchange:             SMTP_HOST=mail.yourcompany.com, SMTP_PORT=587
    SMTP_HOST: str = Field(default="", description="Exchange SMTP server hostname")
    SMTP_PORT: int = Field(default=587, description="SMTP port (587 for STARTTLS, 465 for SSL)")
    SMTP_USER: str = Field(default="", description="SMTP login / sender email address")
    SMTP_PASSWORD: str = Field(default="", description="SMTP password or app password")
    SMTP_FROM_NAME: str = Field(default="SAMO NOC", description="Display name shown in From header")
    SMTP_USE_TLS: bool = Field(default=True, description="Use STARTTLS (true for port 587)")
    # Comma-separated NOC distribution address(es) that receive automated reports
    NOC_EMAIL_ADDRESSES: str = Field(default="", description="Comma-separated NOC email addresses for automated reports")

    @property
    def noc_email_list(self) -> list[str]:
        return [e.strip() for e in self.NOC_EMAIL_ADDRESSES.split(",") if e.strip()]

    @property
    def smtp_enabled(self) -> bool:
        return bool(self.SMTP_HOST and self.SMTP_USER and self.SMTP_PASSWORD)

    @field_validator("JWT_SECRET_KEY", mode="before")
    @classmethod
    def validate_jwt_secret(cls, v: str) -> str:
        """Ensure JWT_SECRET_KEY is set and has minimum length."""
        if not v or len(v) < 32:
            raise ValueError(
                "JWT_SECRET_KEY must be set and at least 32 characters long. "
                "Generate with: python -c 'import secrets; print(secrets.token_urlsafe(32))'"
            )
        return v

    @property
    def allowed_origins(self) -> list[str]:
        """Parse allowed origins from comma-separated string."""
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",") if origin.strip()]

    @property
    def database_url(self) -> str:
        """"""
        return (
            f"postgresql+psycopg2://"
            f"{self.DB_USER}:{self.DB_PASSWORD}@"
            f"{self.DB_HOST}:{self.DB_PORT}/"
            f"{self.DB_NAME}"
        )
    
    class Config:
        env_file = ".env"


app_settings = AppSettings()
