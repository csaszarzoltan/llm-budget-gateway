# Gateway homepage update

Extract this ZIP into the existing 7.3 project root and overwrite files. Then run:

```powershell
python apply_update.py
python -m pip install -e ".[dev]"
```

Restart the Gateway or Unified Console service manager. The Gateway homepage becomes available at:

```text
http://localhost:8000/
```

Manual Gateway start:

```powershell
python -m uvicorn llm_budget_gateway.main:create_app --factory --host 127.0.0.1 --port 8000
```

Test:

```powershell
python -m pytest -q tests/test_gateway_home.py
```
