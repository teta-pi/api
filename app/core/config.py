from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    secret_key: str = "dev-secret-key-change-in-production"
    access_token_expire_minutes: int = 60 * 24 * 7  # 1 week

    database_url: str = "postgresql+asyncpg://tetapi:tetapi_dev@localhost:5432/tetapi"
    redis_url: str = "redis://localhost:6379"

    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "tetapi-media"

    anthropic_api_key: str = ""
    openai_api_key: str = ""
    resend_api_key: str = ""  # https://resend.com — free tier 3k emails/month
    pii_encryption_key: str = ""  # Fernet key for at-rest PII encryption (server .env only)

    ukraine_edr_api_url: str = "https://usr.minjust.gov.ua/api"
    germany_hr_api_url: str = "https://www.handelsregister.de/rp_web/search"
    uk_companies_house_api_key: str = ""
    uk_companies_house_api_url: str = "https://api.company-information.service.gov.uk"
    opencorporates_api_key: str = ""

    pi_camera_root_ca_pem: str = ""

    # C2PA signing — P-256 ECDSA key + certificate chain
    # Set from .env; fallback to certs/ files if env vars are empty
    c2pa_signing_key_pem: str = ""
    c2pa_signing_cert_pem: str = ""
    c2pa_root_ca_pem: str = ""

    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://localhost:3001",
        "https://tetapi.dev",
        "https://app.tetapi.dev",
        "https://api.tetapi.dev",
    ]


settings = Settings()
