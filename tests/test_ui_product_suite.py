from llm_budget_gateway.product_ui import (
    PAGES,
    activity_view,
    render_page,
    setup_progress,
    spend_view,
)


def test_all_six_pages_are_complete_and_accessible():
    assert set(PAGES) == {"setup", "spend", "keys", "policies", "routes", "activity"}
    for page in PAGES:
        html = render_page(page, {"role": "admin", "tenant": "acme"})
        for token in (
            "<main",
            "aria-live",
            "Skip to main content",
            "@media",
            "prefers-reduced-motion",
            'data-state="loading"',
            'data-state="empty"',
            'data-state="error"',
        ):
            assert token in html


def test_guided_setup_is_resumable():
    assert setup_progress({"workspace": 1, "key": 1, "budget": 0, "route": 0}) == {
        "completed": 2,
        "total": 4,
        "next": "budget",
    }


def test_spend_filters_and_forecast():
    v = spend_view(
        [{"model": "a", "cost": 2.0}, {"model": "b", "cost": 3.0}], model="a", budget=10
    )
    assert v["total"] == 2.0 and v["remaining"] == 8.0 and v["rows"] == 1


def test_permission_aware_and_secret_safe():
    assert "Create key" not in render_page(
        "keys", {"role": "viewer", "tenant": "t", "secret": "gw_bad"}
    )
    assert "gw_bad" not in render_page(
        "keys", {"role": "admin", "tenant": "t", "secret": "gw_bad"}
    )


def test_activity_recovery_states():
    v = activity_view([{"state": "warning"}, {"state": "failed"}, {"state": "active"}])
    assert v == {"actionable": 2, "total": 3}


def test_unknown_page_fails_closed():
    try:
        render_page("missing", {"role": "admin"})
    except KeyError:
        pass
    else:
        raise AssertionError("must fail closed")
