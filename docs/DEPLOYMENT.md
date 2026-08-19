# Deployment Guide

This guide covers three deployment methods for the LLM Budget Gateway: manual
installation, Docker containers, and systemd services. It also covers
production hardening and common troubleshooting.

---

## Prerequisites

| Requirement | Version | Purpose |
|-------------|---------|---------|
| Python      | 3.11+   | Runtime |
| uv          | latest  | Package manager and virtualenv |
| Node.js     | 20+     | Only needed to rebuild the cockpit UI |
| npm         | 9+      | Only needed to rebuild the cockpit UI |
| Docker      | 24+     | Only for Docker deployment |
| systemd     | 245+    | Only for systemd service deployment |

Verify your tools:

```bash
python3 --version   # Python 3.11+
uv --version        # uv package manager
node --version      # Node.js 20+ (optional)
```

---

## 1. Manual Installation

### Install from source

```bash
# Clone the repository
git clone https://github.com/your-org/llm-budget-gateway.git
cd llm-budget-gateway

# Install Python dependencies (creates .venv automatically)
uv sync --extra dev --frozen
```

### Configure environment

Copy the example environment file and edit it:

```bash
cp .env.example .env
```

Set at minimum the virtual key mapping so the gateway can authenticate client
requests:

```bash
# In .env — maps API key strings to key IDs for budget tracking
GATEWAY_VIRTUAL_KEYS='{"sk-test-123":"key1","sk-prod-456":"key2"}'

# Security keys — replace with strong random secrets
GATEWAY_ASSURANCE_API_KEY=$(openssl rand -hex 32)
GATEWAY_MCP_API_KEY=$(openssl rand -hex 32)
```

Provider credentials are read from the server environment (e.g.
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`). Client request bodies cannot override
provider keys, base URLs, or headers.

Optionally set additional tuning parameters (all have sensible defaults):

```bash
# Timeout tuning (seconds)
GATEWAY_PROVIDER_TIMEOUT=60       # per-provider timeout
GATEWAY_ROUTE_TIMEOUT_BUDGET=90   # total fallback chain budget

# Cooldown behaviour
GATEWAY_COOLDOWN_DYNAMIC=true     # escalate cooldown on repeated failures
GATEWAY_RETRY_BACKOFF_SECONDS=1   # base retry backoff

# Pricing overrides (JSON)
GATEWAY_PRICING_OVERRIDES='{"gpt-4o":{"input_cost_per_million":2.50,"output_cost_per_million":10.0}}'
```

### Run the gateway

**Full system (recommended)** — starts both the gateway proxy (port 8000) and
the cockpit console (port 8013) with all product APIs:

```bash
uv run gateway-system --no-browser
```

**Gateway only** — runs only the OpenAI-compatible proxy on port 8000:

```bash
export GATEWAY_VIRTUAL_KEYS='{"sk-test-123":"key1"}'
uv run uvicorn llm_budget_gateway.main:create_app --factory \
    --host 127.0.0.1 --port 8000
```

### Verify installation

```bash
# Health check (both modes)
curl -s http://127.0.0.1:8000/health

# List available models
curl -s http://127.0.0.1:8000/v1/models | python3 -m json.tool

# System status (full system only)
curl -s http://127.0.0.1:8013/v1/system/status | python3 -m json.tool

# Open the cockpit UI in a browser
open http://127.0.0.1:8013/cockpit   # macOS
xdg-open http://127.0.0.1:8013/cockpit  # Linux
```

---

## 2. Docker Deployment

### Create Dockerfile

Create a `Dockerfile` in the project root. This is a multi-stage build that
compiles the cockpit UI in a Node stage and runs the gateway in a slim Python
image as a non-root user.

```dockerfile
# ---- Stage 1: Build cockpit UI ----
FROM node:20-slim AS ui-builder

WORKDIR /app/ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci --ignore-scripts

COPY ui/ ./
RUN npm test && npm run build

# ---- Stage 2: Runtime ----
FROM python:3.11-slim AS runtime

# Install uv for dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Create non-root user
RUN groupadd -r gateway && useradd -r -g gateway -d /app -s /sbin/nologin gateway

WORKDIR /app

# Copy dependency manifests first for layer caching
COPY pyproject.toml uv.lock ./

# Install production dependencies only
RUN uv sync --frozen --no-dev

# Copy application source
COPY src/ ./src/

# Copy built cockpit UI from builder stage
COPY --from=ui-builder /app/ui/dist ./ui/dist

# Copy default budget config if present
COPY budgets.yaml ./

# Set ownership to non-root user
RUN chown -R gateway:gateway /app

USER gateway

# Environment defaults
ENV PYTHONUNBUFFERED=1
ENV GATEWAY_DATABASE_URL=sqlite:///./data/gateway.db

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

EXPOSE 8000 8013

# Default: run the full system (gateway + cockpit)
CMD ["uv", "run", "gateway-system", "--no-browser"]
```

### Create docker-compose.yml

```yaml
version: "3.8"

services:
  gateway:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: llm-budget-gateway
    restart: unless-stopped
    ports:
      - "8000:8000"   # OpenAI-compatible proxy
      - "8013:8013"   # Cockpit console
    environment:
      - GATEWAY_VIRTUAL_KEYS=${GATEWAY_VIRTUAL_KEYS:-{"sk-test-123":"key1"}}
      - GATEWAY_DATABASE_URL=sqlite:///./data/gateway.db
      - GATEWAY_ASSURANCE_API_KEY=${GATEWAY_ASSURANCE_API_KEY}
      - GATEWAY_MCP_API_KEY=${GATEWAY_MCP_API_KEY}
      # Provider keys — pass through from host environment
      - OPENAI_API_KEY=${OPENAI_API_KEY:-}
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}
      - GROQ_API_KEY=${GROQ_API_KEY:-}
    volumes:
      - gateway-data:/app/data          # SQLite database persistence
      - gateway-logs:/app/logs          # Log output
      - ./budgets.yaml:/app/budgets.yaml:ro  # Budget config (read-only)
      - ./.env:/app/.env:ro             # Environment overrides (read-only)
    networks:
      - gateway-net
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 15s

volumes:
  gateway-data:
    driver: local
  gateway-logs:
    driver: local

networks:
  gateway-net:
    driver: bridge
```

### Build and run

```bash
# Create a data directory for SQLite persistence
mkdir -p data

# Build the image
docker compose build

# Start in foreground (logs to terminal)
docker compose up

# Or run in the background
docker compose up -d

# View logs
docker compose logs -f gateway

# Stop
docker compose down

# Stop and remove volumes (WARNING: deletes database)
docker compose down -v
```

Verify:

```bash
curl -s http://localhost:8000/health
curl -s http://localhost:8013/v1/system/status | python3 -m json.tool
```

---

## 3. Systemd Service

### Create service unit file

Create `/etc/systemd/system/llm-budget-gateway.service`:

```ini
[Unit]
Description=LLM Budget Gateway
Documentation=https://github.com/your-org/llm-budget-gateway#readme
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=gateway
Group=gateway
WorkingDirectory=/opt/llm-budget-gateway

# Environment
EnvironmentFile=/opt/llm-budget-gateway/.env
Environment=PYTHONUNBUFFERED=1

# Run the full system (gateway + cockpit)
ExecStart=/opt/llm-budget-gateway/.venv/bin/gateway-system --no-browser

# Restart policy
Restart=on-failure
RestartSec=5
StartLimitIntervalSec=300
StartLimitBurst=5

# Graceful shutdown
TimeoutStopSec=30
KillMode=mixed
KillSignal=SIGTERM

# Security hardening
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/opt/llm-budget-gateway/data /opt/llm-budget-gateway/logs
PrivateTmp=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
RestrictNamespaces=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
MemoryDenyWriteExecute=yes
LockPersonality=yes
SystemCallArchitectures=native

# Resource limits
LimitNOFILE=65536
MemoryMax=2G
CPUQuota=200%

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=llm-budget-gateway

[Install]
WantedBy=multi-user.target
```

### Install and enable

```bash
# Create a dedicated system user
sudo useradd -r -s /sbin/nologin -d /opt/llm-budget-gateway gateway

# Install the application
sudo mkdir -p /opt/llm-budget-gateway
sudo cp -r . /opt/llm-budget-gateway/
cd /opt/llm-budget-gateway

# Set up Python environment
sudo -u gateway uv sync --frozen

# Create required directories
sudo -u gateway mkdir -p data logs

# Copy and configure environment
sudo -u gateway cp .env.example .env
sudo -u gateway nano .env  # Edit with your settings

# Copy the service file and reload systemd
sudo cp /opt/llm-budget-gateway/llm-budget-gateway.service \
    /etc/systemd/system/
sudo systemctl daemon-reload

# Enable and start the service
sudo systemctl enable llm-budget-gateway
sudo systemctl start llm-budget-gateway
```

### Manage the service

```bash
# Check status
sudo systemctl status llm-budget-gateway

# View logs (follow mode)
sudo journalctl -u llm-budget-gateway -f

# View logs (last 100 lines)
sudo journalctl -u llm-budget-gateway -n 100

# Restart after config change
sudo systemctl restart llm-budget-gateway

# Stop
sudo systemctl stop llm-budget-gateway

# Disable auto-start
sudo systemctl disable llm-budget-gateway
```

---

## 4. Running in Production

### Environment hardening

1. **Generate strong secrets:**

   ```bash
   GATEWAY_ASSURANCE_API_KEY=$(openssl rand -hex 32)
   GATEWAY_MCP_API_KEY=$(openssl rand -hex 32)
   ```

2. **Rotate virtual keys regularly.** The `GATEWAY_VIRTUAL_KEYS` dict maps API
   key strings to key IDs. Regenerate keys and update the environment variable.

3. **Restrict network binding.** Use `--host 127.0.0.1` (default) to bind
   locally, or bind to a specific interface. Never expose the gateway directly
   to the internet without a reverse proxy.

4. **File permissions.** Ensure `.env`, `gateway.db`, and `budgets.yaml` are
   readable only by the gateway user:

   ```bash
   chmod 600 .env budgets.yaml
   chmod 644 gateway.db
   ```

5. **SQLite WAL mode.** The gateway uses WAL journaling by default for
   concurrent read access. For high-throughput deployments, ensure the data
   directory is on a filesystem that supports `fdatasync` (ext4, XFS, btrfs).

### Reverse proxy (nginx)

Place nginx in front of the gateway for TLS termination, rate limiting, and
request buffering.

```nginx
upstream gateway_backend {
    server 127.0.0.1:8000;
    keepalive 32;
}

upstream cockpit_backend {
    server 127.0.0.1:8013;
    keepalive 16;
}

# Gateway proxy (OpenAI-compatible API)
server {
    listen 443 ssl http2;
    server_name gateway.example.com;

    ssl_certificate     /etc/ssl/certs/gateway.crt;
    ssl_certificate_key /etc/ssl/private/gateway.key;

    # Security headers
    add_header Strict-Transport-Security "max-age=63072000" always;
    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options DENY always;

    # Rate limiting
    limit_req_zone $binary_remote_addr zone=gateway:10m rate=30r/s;

    location / {
        limit_req zone=gateway burst=50 nodelay;
        proxy_pass http://gateway_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Streaming support for SSE responses
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
    }
}

# Cockpit console (admin UI)
server {
    listen 443 ssl http2;
    server_name cockpit.example.com;

    ssl_certificate     /etc/ssl/certs/gateway.crt;
    ssl_certificate_key /etc/ssl/private/gateway.key;

    # Basic auth for cockpit access
    auth_basic "LLM Gateway Console";
    auth_basic_user_file /etc/nginx/.htpasswd;

    location / {
        proxy_pass http://cockpit_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Apply and test:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

### Database backup

The gateway stores all data in a single SQLite file (`gateway.db`). Back up
regularly:

```bash
# Online backup using SQLite .backup command (safe while gateway runs)
sqlite3 /opt/llm-budget-gateway/data/gateway.db \
    ".backup '/backup/gateway-$(date +%Y%m%d-%H%M%S).db'"

# Compress older backups
gzip /backup/gateway-*.db
```

Automate with a cron job:

```cron
# /etc/cron.d/gateway-backup — daily at 03:00 UTC
0 3 * * * gateway sqlite3 /opt/llm-budget-gateway/data/gateway.db \
    ".backup '/backup/gateway-$(date +\%Y\%m\%d).db'" && \
    find /backup -name 'gateway-*.db' -mtime +7 -delete
```

For Docker deployments, back up the named volume:

```bash
docker compose exec gateway python -c \
    "import sqlite3, shutil; \
     src=sqlite3.connect('/app/data/gateway.db'); \
     dst=sqlite3.connect('/tmp/backup.db'); \
     src.backup(dst); dst.close(); src.close()"
docker compose cp gateway:/tmp/backup.db ./backup-$(date +%Y%m%d).db
```

### Log management

The gateway logs to stdout/stderr. How you collect logs depends on the
deployment method:

**systemd** — logs go to the journal:

```bash
# View recent logs
sudo journalctl -u llm-budget-gateway --since "1 hour ago"

# Export to file
sudo journalctl -u llm-budget-gateway --since today > /var/log/gateway.log

# Set log retention (in /etc/systemd/journald.conf)
# MaxRetentionSec=30d
# SystemMaxUse=1G
```

**Docker** — logs go to the container's log driver:

```bash
# Rotate container logs
# In daemon.json or compose: logging driver with max-size/max-file
docker compose logs --since 1h gateway > /var/log/gateway.log
```

**File-based** (when running manually):

```bash
# Redirect to log file with rotation
uv run gateway-system --no-browser 2>&1 | \
    tee -a logs/gateway.log | \
    /usr/local/bin/logrotate /etc/logrotate.d/gateway
```

---

## 5. Troubleshooting

### 5.1 Gateway starts but returns 401 on all requests

**Cause:** No virtual keys configured, or key does not match.

**Fix:** Ensure `GATEWAY_VIRTUAL_KEYS` is set and the client sends the
matching API key in the `Authorization: Bearer <key>` header.

```bash
# Check your virtual key mapping
echo $GATEWAY_VIRTUAL_KEYS

# Test with a valid key
curl -s -H "Authorization: Bearer sk-test-123" \
    http://127.0.0.1:8000/v1/models
```

### 5.2 Gateway times out on provider requests

**Cause:** Provider API key missing, wrong key, or provider is down.

**Fix:**

```bash
# Verify provider credentials are in the environment
env | grep -i "OPENAI\|ANTHROPIC\|GROQ"

# Check provider timeout setting (default 60s)
echo $GATEWAY_PROVIDER_TIMEOUT

# Test provider directly
curl -s https://api.openai.com/v1/models \
    -H "Authorization: Bearer $OPENAI_API_KEY"
```

### 5.3 Cockpit UI shows "connection refused"

**Cause:** Running gateway-only mode (`uvicorn` on port 8000) does not start
the cockpit console (port 8013).

**Fix:** Use the full system launcher instead:

```bash
# Instead of:
uv run uvicorn llm_budget_gateway.main:create_app --factory --host 127.0.0.1 --port 8000

# Use:
uv run gateway-system --no-browser
```

### 5.4 SQLite "database is locked" errors under concurrent load

**Cause:** Too many concurrent writers exceeding SQLite's writer limit, or
the database file is on a network filesystem that doesn't support WAL.

**Fix:**

- Ensure the database is on a local filesystem (ext4, XFS, btrfs) — not NFS
  or SMB.
- WAL mode is enabled by default; verify with:
  ```bash
  sqlite3 gateway.db "PRAGMA journal_mode;"  # Should return "wal"
  ```
- For high-throughput scenarios, consider rate-limiting through nginx or
  increasing `GATEWAY_PROVIDER_TIMEOUT` to reduce concurrent open requests.

### 5.5 "uv: command not found" after install

**Cause:** `uv` is installed but not on `PATH`.

**Fix:**

```bash
# Add uv to PATH (check installation method)
# If installed via curl:
export PATH="$HOME/.local/bin:$PATH"

# If installed via pip:
export PATH="$HOME/.local/share/uv/bin:$PATH"

# Make persistent
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
```

### 5.6 Docker build fails at "npm ci"

**Cause:** Node.js version mismatch or lock file out of date.

**Fix:**

```bash
# Ensure Node 20+
node --version

# Regenerate lock file locally
cd ui && npm install && cd ..

# Rebuild
docker compose build --no-cache
```

### 5.7 Service starts but cockpit is unreachable (systemd)

**Cause:** The `ProtectSystem=strict` directive restricts filesystem access.
The data/logs directories must be in `ReadWritePaths`.

**Fix:** Verify the paths in the service file match your installation:

```ini
ReadWritePaths=/opt/llm-budget-gateway/data /opt/llm-budget-gateway/logs
```

Check for permission denials:

```bash
sudo journalctl -u llm-budget-gateway --since "5 min ago" | grep -i "denied\|permission\|read-only"
```
