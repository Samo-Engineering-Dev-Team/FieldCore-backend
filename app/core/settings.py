from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Deployment environment: "development" | "staging" | "production".
    # Controls how strict startup validation is (see _validate_required).
    ENVIRONMENT: str = Field(default="development")

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
    JWT_SECRET_KEY: str = ""
    JWT_ALGORITHM: str = "HS256"
    ALLOWED_ORIGINS: str = ""
    AUTH_COOKIE_NAME: str = Field(default="fieldcore_access_token")
    REFRESH_COOKIE_NAME: str = Field(default="fieldcore_refresh_token")
    AUTH_COOKIE_DOMAIN: str | None = Field(default=None)
    AUTH_COOKIE_SECURE: bool = Field(
        default=False,
        description="Force auth/performance cookies to use Secure outside production",
    )
    AUTH_COOKIE_SAMESITE: str = Field(default="lax")
    SESSION_SLIDING_REFRESH_MINUTES: int = Field(default=15, ge=1, le=120)
    PERFORMANCE_COOKIE_MAX_AGE_DAYS: int = Field(default=30, ge=1, le=365)
    PASSKEY_RP_NAME: str = Field(default="FieldCore")
    PASSKEY_RP_ID: str = Field(default="")
    PASSKEY_ALLOWED_ORIGINS: str = Field(default="")
    PASSKEY_CEREMONY_TIMEOUT_MS: int = Field(default=120000, ge=30000, le=600000)

    # Background scheduler (SLA breach + weekly task checkers)
    SCHEDULER_ENABLED: bool = Field(
        default=True,
        description="Run the in-process APScheduler for SLA/weekly checks",
    )
    SLA_CHECK_INTERVAL_MINUTES: int = Field(
        default=5,
        ge=1,
        le=60,
        description="How often (minutes) to scan incidents for SLA breaches",
    )
    WEEKLY_CHECK_HOUR_UTC: int = Field(
        default=6,
        ge=0,
        le=23,
        description="Hour (UTC) to run the daily weekly-task checker (self-skips except Wed/Fri)",
    )

    # Celery (durable task queue for notifications/email/webhooks).
    # When no broker is configured, tasks run eagerly (inline) so local/dev/tests
    # work without a worker; production should set CELERY_BROKER_URL.
    CELERY_BROKER_URL: str | None = Field(
        default=None,
        description="Celery broker URL, e.g. redis://host:6379/0",
    )
    CELERY_RESULT_BACKEND: str | None = Field(
        default=None,
        description="Optional Celery result backend URL",
    )
    CELERY_TASK_ALWAYS_EAGER: bool = Field(
        default=False,
        description="Force tasks to run inline (auto-enabled when no broker is set)",
    )

    @property
    def celery_eager(self) -> bool:
        return self.CELERY_TASK_ALWAYS_EAGER or not self.CELERY_BROKER_URL

    # Presence backend (db | redis). If 'redis' and REDIS_URL is set, presence uses Redis for heartbeats.
    PRESENCE_BACKEND: str = Field(
        default="db", description="Storage for presence: 'db' or 'redis'"
    )
    REDIS_URL: str | None = Field(
        default=None,
        description="Optional Redis URL for presence/pubsub (e.g. redis://host:6379/0)",
    )
    PRESENCE_REDIS_TTL_SECONDS: int = Field(
        default=300, description="How long (s) a heartbeat is considered valid in Redis"
    )
    PRESENCE_PUBSUB_CHANNEL: str = Field(
        default="presence_events",
        description="Redis pubsub channel for presence events",
    )
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
    SMTP_PORT: int = Field(
        default=587, description="SMTP port (587 for STARTTLS, 465 for SSL)"
    )
    SMTP_USER: str = Field(default="", description="SMTP login / sender email address")
    SMTP_PASSWORD: str = Field(default="", description="SMTP password or app password")
    SMTP_FROM_NAME: str = Field(
        default="SAMO NOC", description="Display name shown in From header"
    )
    SMTP_USE_TLS: bool = Field(
        default=True, description="Use STARTTLS (true for port 587)"
    )
    # Comma-separated NOC distribution address(es) that receive automated reports
    NOC_EMAIL_ADDRESSES: str = Field(
        default="",
        description="Comma-separated NOC email addresses for automated reports",
    )

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

    @field_validator("AUTH_COOKIE_SAMESITE", mode="before")
    @classmethod
    def validate_auth_cookie_samesite(cls, v: str) -> str:
        normalized = str(v or "").strip().lower()
        if normalized not in {"lax", "strict", "none"}:
            raise ValueError("AUTH_COOKIE_SAMESITE must be one of: lax, strict, none")
        return normalized

    @property
    def allowed_origins(self) -> list[str]:
        """Parse allowed origins from a comma-separated string.

        Strips surrounding whitespace and accidental quotes. Returns an empty
        list when unset — never a wildcard. A wildcard combined with
        ``allow_credentials=True`` would let any site make credentialed
        cross-origin requests, so we fail closed instead (see C2).
        """
        return [
            origin.strip().strip("\"'").rstrip("/")
            for origin in self.ALLOWED_ORIGINS.split(",")
            if origin.strip().strip("\"'").rstrip("/")
        ]

    @property
    def database_url(self) -> str:
        """"""
        return (
            f"postgresql+psycopg2://"
            f"{self.DB_USER}:{self.DB_PASSWORD}@"
            f"{self.DB_HOST}:{self.DB_PORT}/"
            f"{self.DB_NAME}"
        )

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.strip().lower() == "production"

    @model_validator(mode="after")
    def _validate_required(self) -> "AppSettings":
        """Fail fast on missing required config instead of starting broken (H6).

        Database connection settings are always required. In production we also
        require file-storage credentials and an explicit CORS allow-list, so a
        misconfigured prod deploy refuses to boot rather than silently running
        with no storage or a wide-open CORS policy.
        """
        missing: list[str] = []

        for name in ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME"):
            if not str(getattr(self, name)).strip():
                missing.append(name)

        if self.is_production:
            if not self.SUPABASE_URL.strip() or not self.SUPABASE_SERVICE_KEY.strip():
                missing.append("SUPABASE_URL/SUPABASE_SERVICE_KEY")
            if not self.allowed_origins:
                missing.append("ALLOWED_ORIGINS")

        if missing:
            raise ValueError(
                "Missing required settings: "
                + ", ".join(missing)
                + f" (ENVIRONMENT={self.ENVIRONMENT}). Set them in the environment "
                "or .env before starting."
            )

        return self

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


app_settings = AppSettings()
