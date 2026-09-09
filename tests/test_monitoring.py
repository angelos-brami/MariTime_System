from typing import Any

from eastmed_shared import Settings
from eastmed_shared.monitoring import configure_error_monitoring


def test_sentry_is_disabled_without_dsn(monkeypatch: Any) -> None:
    called = False

    def fake_init(**kwargs: object) -> None:
        del kwargs
        nonlocal called
        called = True

    monkeypatch.setattr("eastmed_shared.monitoring.sentry_sdk.init", fake_init)
    configure_error_monitoring(Settings(sentry_dsn=None))
    assert called is False


def test_sentry_disables_pii_and_bounds_trace_sampling(monkeypatch: Any) -> None:
    captured: dict[str, object] = {}

    def fake_init(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr("eastmed_shared.monitoring.sentry_sdk.init", fake_init)
    configure_error_monitoring(
        Settings(
            environment="test",
            sentry_dsn="https://public@example.test/1",
            sentry_traces_sample_rate=2.0,
        )
    )
    assert captured["environment"] == "test"
    assert captured["send_default_pii"] is False
    assert captured["traces_sample_rate"] == 1.0
