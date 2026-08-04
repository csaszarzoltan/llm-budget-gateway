# Console Service Manager 7.2

The Unified Console can now start and stop each local FastAPI service independently or operate all services as one batch.

## Usage

Start only the console:

```powershell
python -m uvicorn llm_budget_gateway.console_api:create_console_app --factory --host 127.0.0.1 --port 8013
```

Open `http://127.0.0.1:8013/`, then select **Manage services** in the lower-right corner.

Available actions:

- **Start** launches one service with the active virtual environment's Python interpreter.
- **Stop** terminates only a process created by this console session.
- **Start all** launches all 15 independently hosted services.
- **Stop all** terminates all child services owned by the console.
- **Open** opens the service dashboard or OpenAPI page.
- **Refresh** updates process and port status.

Logs are written to `.gateway-console/logs/<service>.log` in the project directory.

## Safety boundaries

Lifecycle endpoints accept requests only from localhost and require the custom `X-Console-Action: 1` header. Commands are passed directly to `subprocess.Popen` with `shell=False`. The manager never terminates a process it did not start. If a required port is already occupied, startup fails and reports the conflict.

This feature is intended for local development. Do not expose the console service manager directly to an untrusted network.
