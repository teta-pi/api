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
    northdata_api_key: str = ""  # commercial — activates NorthData DE/EU verifier
    opendatabot_api_key: str = ""  # commercial — activates Opendatabot UA verifier

    pi_camera_root_ca_pem: str = ""

    # Public base URLs for links we hand out (profile pages, badges, proofs,
    # magic links). Profiles live on the Next.js app, badges/proofs on the API
    # — the bare landing domain serves neither (known-issues §6.6 / 1.23).
    app_url: str = "https://app.tetapi.dev"
    api_url: str = "https://api.tetapi.dev"

    # C2PA signing — P-256 ECDSA key + certificate chain
    # Set from .env; fallback to certs/ files if env vars are empty
    c2pa_signing_key_pem: str = ""
    c2pa_signing_cert_pem: str = ""
    c2pa_root_ca_pem: str = ""

    # Gate for claiming C2PA verification happened (known-issues §6.8). False
    # until real manifest verification (c2pa-python, cert-chain validation) is
    # in place — today verify_pi_camera_signature() is a substring match on a
    # client-supplied manifest_json, so it proves nothing about the file.
    # Flip to True only once that work (docs/verification-rework.md "task B")
    # ships; until then every upload path must leave c2pa_verified False.
    c2pa_verification_enabled: bool = False

    # Self-hosted GoatCounter analytics — read-only SQLite bridge for the
    # Back Office Analytics tab. See docs/analytics.md.
    goatcounter_db_path: str = "/opt/goatcounter/db/goatcounter.sqlite3"

    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://localhost:3001",
        "https://tetapi.dev",
        "https://app.tetapi.dev",
        "https://api.tetapi.dev",
    ]


settings = Settings()
