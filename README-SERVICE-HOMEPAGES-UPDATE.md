# LLM Budget Gateway 7.3 service-homepage update

This update builds on the 7.2 Service Manager. Every service started from the console receives a root homepage.

## Apply

Extract this ZIP into the existing 7.2 project root and overwrite files:

```powershell
python apply_update.py
python -m pip install -e ".[dev]"
```

Restart Unified Console and start the desired services:

```powershell
python -m uvicorn llm_budget_gateway.console_api:create_console_app --factory --host 127.0.0.1 --port 8013
```

Open `http://127.0.0.1:8013/`, select **Manage services**, and choose **Start** or **Start all**. The **Open** button now opens the service homepage.

## Test

```powershell
python -m pytest -q tests/test_hosted_services.py
```
