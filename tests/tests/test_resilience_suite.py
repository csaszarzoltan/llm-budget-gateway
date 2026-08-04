import calendar

import pytest

from llm_budget_gateway.resilience_suite import (
    AdaptiveConcurrency,
    ConfigDoctor,
    DeadLetterStore,
    IncidentTimeline,
    MaintenanceWindow,
)


def test_concurrency_tuning_and_bounds():
    a = AdaptiveConcurrency()
    assert a.tune(10, 50, 0, 100)["limit"] == 11
    assert a.tune(10, 200, 0, 100)["limit"] == 5
    assert a.tune(10, 100, 0.02, 100)["reason"] == "hold"
    with pytest.raises(ValueError):
        a.tune(True, 1, 0, 1)


def test_dead_letter_redacts_replays_and_isolates(tmp_path):
    d = DeadLetterStore(str(tmp_path / "d.db"), lambda: 10)
    item = d.add("t", {"job": 1, "prompt": "drop"}, "failed")
    assert item == d.add("t", {"job": 1, "prompt": "other"}, "failed")
    first = d.replay("t", item["id"])
    assert first["payload"] == {"job": 1} and not first["duplicate"]
    assert d.replay("t", item["id"])["duplicate"]
    with pytest.raises(KeyError):
        d.replay("x", item["id"])
    with pytest.raises(ValueError):
        d.add("", {}, "")


def test_maintenance_window():
    epoch = calendar.timegm((2026, 8, 3, 1, 0, 0))
    m = MaintenanceWindow()
    assert m.evaluate(0, 60, 30, epoch)["active"]
    assert not m.evaluate(1, 60, 30, epoch)["active"]
    with pytest.raises(ValueError):
        m.evaluate(7, 0, 1, 0)


def test_config_doctor():
    d = ConfigDoctor()
    bad = d.diagnose(
        {
            "environment": "production",
            "database_url": "sqlite:///x",
            "provider_timeout": 0,
        }
    )
    assert not bad["valid"] and len(bad["findings"]) == 4
    assert d.diagnose(
        {
            "api_key": "x",
            "environment": "production",
            "database_url": "postgres://x",
            "provider_timeout": 10,
            "webhook_secret": "strong",
        }
    )["valid"]
    with pytest.raises(TypeError):
        d.diagnose(None)


def test_incident_timeline():
    t = IncidentTimeline()
    out = t.build(
        [
            {"timestamp": 20, "kind": "recovery", "detail": "ok"},
            {"timestamp": 10, "kind": "outage", "detail": "down"},
        ]
    )
    assert (
        out["duration_seconds"] == 10
        and out["severity"] == "high"
        and out["events"][0]["timestamp"] == 10
    )
    with pytest.raises(ValueError):
        t.build([])
    with pytest.raises(ValueError):
        t.build([{"timestamp": True, "kind": "x"}])
