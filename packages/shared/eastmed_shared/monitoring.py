import sentry_sdk

from eastmed_shared.config import Settings


def configure_error_monitoring(settings: Settings) -> None:
    if not settings.sentry_dsn:
        return
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        send_default_pii=False,
        traces_sample_rate=max(0.0, min(1.0, settings.sentry_traces_sample_rate)),
    )
