from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator
from pathlib import Path

_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"

# Load .env early so AppSettings picks up values when imported in different contexts.
# Use python-dotenv if available; otherwise rely on pydantic's env_file setting.
try:
    from dotenv import load_dotenv
    if _ENV_FILE.exists():
        load_dotenv(_ENV_FILE)
    else:
        # fallback: attempt to load default .env from cwd
        load_dotenv()
except Exception:
    # dotenv not installed or failed to load — pydantic will still attempt env_file
    pass


class AppSettings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        extra="ignore",
    )

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
    ENVIRONMENT: str = Field(default="development")
    ENABLE_API_DOCS: bool | None = Field(
        default=None,
        description="Enable Swagger/ReDoc/OpenAPI. Defaults to false in production.",
    )
    ENABLE_METRICS_ENDPOINT: bool = Field(
        default=False,
        description="Expose /metrics. Keep disabled unless scraped from a trusted network.",
    )
    METRICS_BEARER_TOKEN: str | None = Field(
        default=None,
        description="Optional bearer token required by /metrics when enabled.",
    )
    TRUSTED_HOSTS: str = Field(
        default="",
        description="Comma-separated hosts allowed by TrustedHostMiddleware in production.",
    )
    TRUST_PROXY_HEADERS: bool = Field(
        default=False,
        description="Trust X-Forwarded-* headers from a known reverse proxy.",
    )
    JWT_TOKEN_EXPIRE_MINUTES: int = 60
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    JWT_SECRET_KEY: str = Field(..., min_length=32)
    JWT_ALGORITHM: str = "HS256"
    ALLOWED_ORIGINS: str = Field(default="http://localhost:3000,http://localhost:5173")
    PASSKEY_RP_NAME: str = Field(default="FieldCore")
    PASSKEY_RP_ID: str = Field(default="")
    PASSKEY_ALLOWED_ORIGINS: str = Field(default="")
    PASSKEY_CEREMONY_TIMEOUT_MS: int = Field(default=120000, ge=30000, le=600000)

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

    # SaaS operations
    TENANT_RATE_LIMIT_ENABLED: bool = Field(
        default=True,
        description="Enable per-tenant API rate limits for authenticated requests",
    )
    TENANT_RATE_LIMIT_REQUESTS: int = Field(
        default=600,
        ge=1,
        description="Allowed requests per tenant/user key in each rate-limit window",
    )
    TENANT_RATE_LIMIT_WINDOW_SECONDS: int = Field(
        default=60,
        ge=1,
        description="Per-tenant rate-limit window length in seconds",
    )
    TENANT_RATE_LIMIT_REDIS_URL: str | None = Field(
        default=None,
        description="Optional Redis URL for rate limits. Falls back to REDIS_URL, then memory.",
    )
    TENANT_RATE_LIMIT_FAIL_OPEN: bool = Field(
        default=True,
        description="Allow requests if Redis rate limiting errors",
    )
    TENANT_RATE_LIMIT_OVERRIDE_HEADER: str = Field(
        default="X-FieldCore-RateLimit-Override",
        description="Emergency override header accepted only from super_admin tokens",
    )
    SUPPORT_DIAGNOSTICS_RECENT_OPERATION_LIMIT: int = Field(
        default=5,
        ge=1,
        le=25,
        description="Recent tenant operation rows returned by support diagnostics",
    )

    # Licensing / metering
    LICENSING_METERING_JOB_ENABLED: bool = Field(
        default=False,
        description="Run optional in-process daily tenant metering loop",
    )
    LICENSING_METERING_JOB_INTERVAL_SECONDS: int = Field(
        default=86400,
        ge=3600,
        description="Interval in seconds between in-process metering runs",
    )
    LICENSING_METERING_STARTUP_DELAY_SECONDS: int = Field(
        default=60,
        ge=0,
        description="Delay before optional metering loop starts after boot",
    )
    BILLING_WEBHOOK_SECRET: str | None = Field(
        default=None,
        description="Optional shared secret for mock billing webhook ingestion",
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
    def is_production(self) -> bool:
        """Whether the app is running in a production-like environment."""
        return self.ENVIRONMENT.strip().lower() in {"prod", "production"}

    @property
    def api_docs_enabled(self) -> bool:
        """Expose API documentation unless explicitly disabled, or production by default."""
        if self.ENABLE_API_DOCS is not None:
            return self.ENABLE_API_DOCS
        return not self.is_production

    @property
    def trusted_hosts(self) -> list[str]:
        """Parse trusted hosts from a comma-separated string."""
        return [host.strip() for host in self.TRUSTED_HOSTS.split(",") if host.strip()]

    @property
    def allowed_origins(self) -> list[str]:
        """Parse allowed origins from comma-separated string."""
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",") if origin.strip()]

    @property
    def passkey_allowed_origins(self) -> list[str]:
        """Origins allowed to initiate WebAuthn ceremonies."""
        raw = self.PASSKEY_ALLOWED_ORIGINS.strip()
        if raw:
            return [origin.strip() for origin in raw.split(",") if origin.strip()]
        return self.allowed_origins

    @property
    def tenant_rate_limit_redis_url(self) -> str | None:
        """Redis URL for tenant rate limiting."""
        return self.TENANT_RATE_LIMIT_REDIS_URL or self.REDIS_URL

    @property
    def database_url(self) -> str:
        """"""
        return (
            f"postgresql+psycopg2://"
            f"{self.DB_USER}:{self.DB_PASSWORD}@"
            f"{self.DB_HOST}:{self.DB_PORT}/"
            f"{self.DB_NAME}"
        )
    
app_settings = AppSettings()
