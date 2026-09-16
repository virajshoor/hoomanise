import os
import secrets


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def pepper() -> str:
    return os.environ.get("API_KEY_PEPPER", "")


class Settings:
    app_name = "hoomanise-api"
    version = "1.0.0"
    env = _env("APP_ENV", "production")
    log_level = _env("LOG_LEVEL", "INFO")
    request_timeout_seconds = _int("REQUEST_TIMEOUT_SECONDS", 30)

    database_url = _env("DATABASE_URL")
    redis_url = _env("REDIS_URL")

    db_pool_min = _int("DB_POOL_MIN", 1)
    db_pool_max = _int("DB_POOL_MAX", 3)
    redis_pool_max = _int("REDIS_POOL_MAX", 32)

    clerk_secret_key = _env("CLERK_SECRET_KEY")
    clerk_issuer = _env("CLERK_ISSUER")
    clerk_jwks_url = _env("CLERK_JWKS_URL")
    clerk_authorized_parties = [
        o.strip()
        for o in _env("CLERK_AUTHORIZED_PARTIES").split(",")
        if o.strip()
    ]

    api_key_pepper = _env("API_KEY_PEPPER")
    api_key_prefix = _env("API_KEY_PREFIX", "hm_")

    cors_origins = [
        o.strip() for o in _env("ALLOWED_ORIGINS", "*").split(",") if o.strip()
    ]

    max_input_chars = _int("MAX_INPUT_CHARS", 20000)
    max_body_bytes = _int("MAX_BODY_BYTES", 1_000_000)

    rate_limit_user_per_min = _int("RATE_LIMIT_USER_PER_MIN", 10)
    rate_limit_user_per_day = _int("RATE_LIMIT_USER_PER_DAY", 200)
    rate_limit_ip_per_min = _int("RATE_LIMIT_IP_PER_MIN", 30)
    rate_limit_global_per_min = _int("RATE_LIMIT_GLOBAL_PER_MIN", 240)

    job_concurrency = _int("JOB_CONCURRENCY", 3)
    worker_enabled = _bool("WORKER_ENABLED", True)
    human_score_threshold = float(os.environ.get("HUMAN_SCORE_THRESHOLD", "40"))

    log_format = _env("LOG_FORMAT", "text")
    log_sample = float(os.environ.get("LOG_SAMPLE", "1.0"))

    free_credits_default = _int("FREE_CREDITS_DEFAULT", 600)
    words_per_credit = _int("WORDS_PER_CREDIT", 5)
    sales_email = _env("SALES_EMAIL", "support@example.com")

    rate_limit_batch_max = _int("RATE_LIMIT_BATCH_MAX", 10)
    analyze_concurrency = _int("ANALYZE_CONCURRENCY", 2)
    max_outstanding_jobs_per_user = _int("MAX_OUTSTANDING_JOBS_PER_USER", 10)
    max_queue_depth = _int("MAX_QUEUE_DEPTH", 500)
    job_retention_days = _int("JOB_RETENTION_DAYS", 30)
    webhook_signing_key = _env("WEBHOOK_SIGNING_KEY", "")

    response_cache_enabled = _bool("RESPONSE_CACHE_ENABLED", True)
    response_cache_ttl_hours = _int("RESPONSE_CACHE_TTL_HOURS", 168)

    webhook_delivery_retries = _int("WEBHOOK_DELIVERY_RETRIES", 3)
    webhook_timeout_seconds = _int("WEBHOOK_TIMEOUT_SECONDS", 10)
    webhook_max_per_user = _int("WEBHOOK_MAX_PER_USER", 5)
    job_queue_wait_seconds = _int("JOB_QUEUE_WAIT_SECONDS", 600)
    job_slot_ttl_seconds = _int("JOB_SLOT_TTL_SECONDS", 120)
    job_result_ttl_hours = _int("JOB_RESULT_TTL_HOURS", 24)
    job_lease_seconds = _int("JOB_LEASE_SECONDS", 300)
    job_input_store_max_chars = _int("JOB_INPUT_STORE_MAX_CHARS", 100_000)

    request_id_header = "X-Request-ID"


settings = Settings()


def generate_request_id() -> str:
    return secrets.token_hex(8)
