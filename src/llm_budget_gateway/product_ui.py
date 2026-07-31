"""Accessible, responsive product UI view models and server-rendered pages."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from html import escape

PAGES = {
    "setup": ("Guided setup", "Complete four safe steps", "Continue setup"),
    "spend": ("Spend explorer", "Filter cost and forecast budget", "Export CSV"),
    "keys": (
        "Key access console",
        "Issue, rotate and revoke credentials",
        "Create key",
    ),
    "policies": ("Policy studio", "Simulate rules before activation", "New policy"),
    "routes": ("Route health", "Understand traffic and circuit recovery", "Edit route"),
    "activity": (
        "Operations center",
        "Resolve warnings and failed operations",
        "Review queue",
    ),
}


def setup_progress(state: Mapping[str, int]) -> dict[str, object]:
    steps = ("workspace", "key", "budget", "route")
    done = sum(bool(state.get(x)) for x in steps)
    return {
        "completed": done,
        "total": 4,
        "next": next((x for x in steps if not state.get(x)), None),
    }


def spend_view(
    records: Sequence[Mapping[str, object]], model: str | None = None, budget: float = 0
) -> dict[str, object]:
    rows = [r for r in records if model is None or r.get("model") == model]
    total = sum(float(r.get("cost", 0)) for r in rows)
    return {"total": total, "remaining": max(0, budget - total), "rows": len(rows)}


def activity_view(items: Sequence[Mapping[str, object]]) -> dict[str, int]:
    return {
        "actionable": sum(x.get("state") in {"warning", "failed"} for x in items),
        "total": len(items),
    }


def render_page(page: str, context: Mapping[str, object]) -> str:
    if page not in PAGES:
        raise KeyError(page)
    title, subtitle, action = PAGES[page]
    role = str(context.get("role", "viewer"))
    tenant = escape(str(context.get("tenant", "Workspace")))
    if page == "keys" and role not in {"admin", "security"}:
        action = ""
    nav = "".join(
        f'<a href="/control/{p}" aria-current="{"page" if p == page else "false"}">{n[0]}</a>'
        for p, n in PAGES.items()
    )
    cards = "".join(
        f'<article class="card"><h2>{x}</h2><p>Ready for tenant-scoped data.</p><button>View details</button></article>'
        for x in ("Overview", "Current state", "Next safe action")
    )
    button = f'<button class="primary">{escape(action)}</button>' if action else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title><style>
:root{{--bg:#f5f7fb;--surface:#fff;--ink:#172033;--muted:#58657d;--brand:#3157d5;--focus:#ffbf47;font-family:Inter,system-ui,sans-serif}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink)}}.skip{{position:absolute;left:-999px}}.skip:focus{{left:12px;top:12px;z-index:9;background:#fff;padding:12px}}.shell{{display:grid;grid-template-columns:260px 1fr;min-height:100vh}}aside{{background:#10182b;color:#fff;padding:24px}}nav{{display:grid;gap:8px;margin-top:28px}}nav a{{color:#d6def1;padding:12px;border-radius:10px;text-decoration:none;min-height:44px}}nav a[aria-current=page]{{background:#2b3b62;color:#fff}}main{{padding:32px;max-width:1200px}}header{{display:flex;justify-content:space-between;gap:20px;align-items:center}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:24px}}.card,.state{{background:var(--surface);border:1px solid #dce3ef;border-radius:16px;padding:20px}}button{{min-height:44px;border-radius:10px;padding:10px 16px;border:1px solid #9aa7c0;background:#fff}}.primary{{background:var(--brand);color:#fff;border:0}}:focus-visible{{outline:3px solid var(--focus);outline-offset:3px}}[data-state]{{margin-top:16px}}[data-state="loading"]{{background:linear-gradient(90deg,#e7ebf3,#fff,#e7ebf3)}}@media(max-width:800px){{.shell{{grid-template-columns:1fr}}aside{{padding:16px}}nav{{display:flex;overflow:auto}}main{{padding:20px}}header{{align-items:flex-start;flex-direction:column}}.grid{{grid-template-columns:1fr}}}}@media(prefers-reduced-motion:reduce){{*{{animation:none!important;scroll-behavior:auto!important}}}}
</style></head><body><a class="skip" href="#content">Skip to main content</a><div class="shell"><aside aria-label="Primary"><strong>Budget Gateway</strong><nav>{nav}</nav></aside><main id="content"><header><div><p>{tenant}</p><h1>{title}</h1><p>{subtitle}</p></div>{button}</header><div class="state" role="status" aria-live="polite">Your work is saved after each confirmed step.</div><section class="grid">{cards}</section><section data-state="loading" aria-label="Loading state">Loading current data…</section><section data-state="empty"><h2>No items yet</h2><p>Use the primary action to begin.</p></section><section data-state="error"><h2>We could not confirm the result</h2><p>Your draft is safe. Retry with the same request reference.</p><button>Retry</button></section></main></div></body></html>"""
