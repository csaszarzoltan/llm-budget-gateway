# Unified Console troubleshooting

## Services do not start

1. Start the console through the project environment with `uv run`.
2. Open **Manage services** and use **Start** or **Start all**.
3. Wait for the status to become `reachable`; the main gateway may need extra startup time while LiteLLM initializes its model catalog.
4. Read the error shown in the panel. It includes the recent child-log tail.
5. Inspect the full log under `.gateway-console/logs/<service>.log` when more context is needed.
6. Check that ports 8000 through 8012 and 8014 through 8015 are free. The console itself uses 8013.

The manager never kills an unmanaged process occupying a registered port. It reports that conflict instead.

## Dark mode does not change

Use the half-circle theme button in the top bar. The button exposes its current state with `aria-pressed` and updates its label between **Use dark theme** and **Use light theme**. The saved value uses the `gateway-theme` browser-local-storage key. On a first visit, the operating-system color preference is used.

After upgrading, restart the console and hard-refresh the browser so an older HTML response is not retained in the active tab.
