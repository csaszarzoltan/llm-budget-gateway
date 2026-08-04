import pytest

from llm_budget_gateway import activation_suite as s


def test_setup_progress_orders_missing_steps_and_rejects_unknown():
    r = s.SetupProgress().evaluate(["install", "virtual-key"])
    assert r["next"] == "provider" and r["progress"] == pytest.approx(2 / 6)
    with pytest.raises(ValueError):
        s.SetupProgress().evaluate(["magic"])


def test_environment_template_contains_names_not_secrets():
    r = s.EnvironmentTemplate().build(["gateway", "security"])
    assert "replace-me" in r["template"] and "GATEWAY_SECURITY_API_KEY" in r["keys"]
    with pytest.raises(ValueError):
        s.EnvironmentTemplate().build(["missing"])


def test_provider_credential_check_only_reports_names():
    r = s.ProviderCredentialCheck().evaluate(
        ["OPENAI_API_KEY", "AZURE_API_KEY"], ["OPENAI_API_KEY"]
    )
    assert r["missing"] == ["AZURE_API_KEY"]


def test_port_plan_finds_conflicts_and_reserved_ports():
    r = s.PortPlan().validate({"a": 8000, "b": 8000, "c": 8013}, [8013])
    assert not r["valid"] and r["reserved_hits"] == ["c"]
    with pytest.raises(ValueError):
        s.PortPlan().validate({"a": 70000})


def test_configuration_doctor_detects_missing_auth_timeout_and_sqlite():
    r = s.ConfigurationDoctor().inspect(
        {"GATEWAY_DATABASE_URL": "sqlite:///x", "GATEWAY_PROVIDER_TIMEOUT": 200}, True
    )
    assert len(r["findings"]) == 3
    assert s.ConfigurationDoctor().inspect(
        {"GATEWAY_VIRTUAL_KEYS": "{}", "GATEWAY_PROVIDER_TIMEOUT": 60}
    )["ready"]


def test_first_request_builder_uses_env_variable_not_secret():
    r = s.FirstRequestBuilder().build("http://localhost:8000", "gpt-4o")
    assert "$YOUR_GATEWAY_KEY" in r["curl"] and r["url"].endswith(
        "/v1/chat/completions"
    )
    with pytest.raises(ValueError):
        s.FirstRequestBuilder().build("file:///x", "m")


def test_budget_starter_creates_soft_and_hard_limits():
    r = s.BudgetStarter().build("key1", 100, 60, 90000)["scopes"][0]
    assert r["soft_limit"] == 80 and r["hard_limit"] == 100
    with pytest.raises(ValueError):
        s.BudgetStarter().build("bad key", 1, 1, 1)


def test_service_profiles_match_personas_and_all_has_fifteen():
    assert s.ServiceProfile().resolve("sre")["services"][-1] == "scale"
    assert s.ServiceProfile().resolve("all")["count"] == 15
    with pytest.raises(ValueError):
        s.ServiceProfile().resolve("unknown")


def test_diagnostic_bundle_redacts_extra_fields_and_is_deterministic():
    x = s.DiagnosticBundle().build(
        "8", [{"name": "gateway", "port": 8000, "reachable": True, "secret": "x"}], []
    )
    y = s.DiagnosticBundle().build(
        "8", [{"name": "gateway", "port": 8000, "reachable": True}], []
    )
    assert x == y and "secret" not in str(x) and len(x["sha256"]) == 64


def test_activation_gate_fails_closed_until_every_check_passes():
    gate = s.ActivationGate()
    assert gate.decide({x: True for x in gate.REQUIRED})["activated"]
    assert not gate.decide({"setup": True})["activated"]
    with pytest.raises(ValueError):
        gate.decide({"setup": "yes"})
