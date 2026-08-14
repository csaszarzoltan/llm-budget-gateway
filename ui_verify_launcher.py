import sqlite3
from pathlib import Path

import uvicorn

from llm_budget_gateway.console_api import create_console_app
from llm_budget_gateway.service_manager import ServiceManager

root = Path("/home/zoltan/llm-budget-gateway")
data_dir = root / ".gateway-console"
data_dir.mkdir(parents=True, exist_ok=True)

# Seed a couple of customers + a budget + fake spend so the UI has data to render.
cost = sqlite3.connect(root / "gateway.db", check_same_thread=False)

app = create_console_app(
    manager=ServiceManager(workdir=root),
    project_root=root,
    product_connection=sqlite3.connect(data_dir / "product.db", check_same_thread=False),
    provider_connection=sqlite3.connect(data_dir / "providers.db", check_same_thread=False),
    routing_connection=sqlite3.connect(data_dir / "routing.db", check_same_thread=False),
    cost_connection=cost,
    credential_key_path=data_dir / "provider-master.key",
    auto_start_services=False,
)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8099, log_level="warning")
