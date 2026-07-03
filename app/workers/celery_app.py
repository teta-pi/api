from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "tetapi",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "app.workers.tasks.verification",
        "app.workers.tasks.bitcoin",
        "app.workers.tasks.ai",
        "app.workers.tasks.twira",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
)

celery_app.conf.beat_schedule = {
    # SystemSpec v2.1: OTS lifecycle cron 30 min
    "ots-lifecycle": {"task": "ots_lifecycle", "schedule": 30 * 60},
    # Endpoint uptime probe every 30 min
    "probe-endpoints": {"task": "probe_endpoints", "schedule": 30 * 60},
    # Nightly TWIRA T/P recompute (03:00 UTC)
    "twira-recompute": {"task": "twira_recompute_scores", "schedule": 24 * 60 * 60},
    # Existing: check pending media OTS confirmations hourly
    "btc-confirmations": {"task": "check_bitcoin_confirmations", "schedule": 60 * 60},
}
