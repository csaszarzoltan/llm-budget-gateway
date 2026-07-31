# Root homepage update for LLM Budget Gateway 7.1.x

This drop-in update makes the Unified Gateway Console available at:

- `http://127.0.0.1:8013/`
- `http://127.0.0.1:8013/console` remains a compatibility alias.

## Apply

Copy the contents of this ZIP into the root of the existing 7.1.0 project and allow the files to overwrite existing files.

Then reinstall the editable package if needed:

```powershell
python -m pip install -e ".[dev]"
```

Start the console:

```powershell
python -m uvicorn llm_budget_gateway.console_api:create_console_app --factory --host 127.0.0.1 --port 8013
```

Open `http://127.0.0.1:8013/`.

## Test

```powershell
python -m pytest -q tests/test_console_home.py
```
