# LLM Budget Gateway 7.2 service-manager update

This update adds UI controls that start or stop the Gateway, Control Center and all other independently hosted services.

## Apply

Extract this ZIP into the existing 7.1.x project root and overwrite files. Then install the project again:

```powershell
python -m pip install -e ".[dev]"
```

Start only the console:

```powershell
python -m uvicorn llm_budget_gateway.console_api:create_console_app --factory --host 127.0.0.1 --port 8013
```

Open:

```text
http://127.0.0.1:8013/
```

Use **Manage services** to start one service or all services. Service-specific configuration, such as API keys, virtual keys and provider credentials, must be present in the console process environment before starting children.

Run targeted tests:

```powershell
python -m pytest -q tests/test_service_manager.py tests/test_console_service_api.py
```
