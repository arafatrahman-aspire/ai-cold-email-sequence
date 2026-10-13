"""The Enroll page's endpoints: browsing leads and removing them."""

import os

import pytest
from fastapi.testclient import TestClient

# Importing the app builds the CMS login config; placeholders are enough here.
os.environ.setdefault("APP_PUBLIC_URL", "http://localhost:5173")
os.environ.setdefault("CMS_PUBLIC_URL", "http://localhost:8102")
os.environ.setdefault("SSO_CLIENT_SECRET", "s" * 40)

from app import main, repository as repo, settings_store  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    calls = {"browse": [], "remove": []}

    async def browse_leads(search, view, source, contactable, skip, limit, offset):
        calls["browse"].append((search, view, limit, offset))
        sizes = {"all": 7, "not_enrolled": 4, "available": 3, "in_sequence": 2, "finished": 1}
        return [{"id": f"{view}-{i}"} for i in range(min(limit, sizes[view]))], sizes[view]

    async def remove_from_sequence(lead_id, reason):
        calls["remove"].append((lead_id, reason))
        return "removed" if lead_id.endswith("1") else "busy"

    async def contactable():
        return ["New"]

    async def get(key):
        return True

    monkeypatch.setattr(repo, "browse_leads", browse_leads)
    monkeypatch.setattr(repo, "remove_from_sequence", remove_from_sequence)
    monkeypatch.setattr(settings_store, "contactable_statuses", contactable)
    monkeypatch.setattr(settings_store, "get", get)
    main.app.dependency_overrides[main.require_user] = lambda: {"email": "me@aspire.com"}
    yield TestClient(main.app), calls   # no "with": the lifespan (DB, workers) is not started
    main.app.dependency_overrides.clear()


def test_browse_returns_the_page_and_every_views_count(client):
    c, calls = client
    r = c.get("/leads", params={"view": "available", "search": "  acme ", "limit": 2, "offset": 4})
    assert r.status_code == 200
    body = r.json()
    assert body["leads"] == [{"id": "available-0"}, {"id": "available-1"}] and body["total"] == 3
    assert body["counts"] == {"all": 7, "not_enrolled": 4, "available": 3, "in_sequence": 2, "finished": 1}
    assert calls["browse"][0] == ("acme", "available", 2, 4)
    assert c.get("/leads", params={"view": "everyone"}).status_code == 422


def test_remove_reports_each_lead_and_who_did_it(client):
    c, calls = client
    ids = ["00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"]
    r = c.post("/enrollments/remove", json={"lead_ids": ids})
    assert r.status_code == 200
    assert r.json() == {"removed": 1, "results": [
        {"lead_id": ids[0], "outcome": "removed"}, {"lead_id": ids[1], "outcome": "busy"}]}
    assert calls["remove"][0][1] == "removed from the sequence by me@aspire.com"
    assert c.post("/enrollments/remove", json={"lead_ids": ["not-a-uuid"]}).status_code == 422
    assert c.post("/enrollments/remove", json={"lead_ids": []}).status_code == 422


def test_nurture_leads_page_and_select_all(client, monkeypatch):
    """Email Nurture -> Leads: every lead with each view's size, and the ids for "select all"."""
    from app.nurture import options
    from app.nurture import repo as nrepo

    c, _ = client
    sizes = {"all": 183, "can_join": 120, "in_nurture": 3, "finished": 1}
    seen = []

    async def browse(search, view, cooldown, blocked, skip, limit, offset):
        seen.append((search, view, limit, offset))
        return [{"lead_id": f"{view}-{i}"} for i in range(min(limit, sizes[view]))], sizes[view]

    async def candidates(cooldown, blocked, skip, limit):
        return [{"lead_id": "a", "tier": "Warm"}, {"lead_id": "b", "tier": "Cold"}]

    async def load(fresh=False):
        return options.merge({})

    monkeypatch.setattr(nrepo, "browse", browse)
    monkeypatch.setattr(nrepo, "candidates", candidates)
    monkeypatch.setattr(options, "load", load)

    r = c.get("/nurture/leads", params={"view": "can_join", "search": " acme ", "limit": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 120 and len(body["leads"]) == 2 and body["counts"] == sizes
    assert seen[0] == ("acme", "can_join", 2, 0)
    assert c.get("/nurture/leads", params={"view": "everyone"}).status_code == 422
    assert c.get("/nurture/eligible").json() == {"count": 2, "by_tier": {"Warm": 1, "Cold": 1}, "lead_ids": ["a", "b"]}


def test_send_now_is_refused_in_production(client, monkeypatch):
    from app.config import get_settings

    c, _ = client
    monkeypatch.setattr(get_settings(), "app_env", "production")
    r = c.post("/nurture/messages/00000000-0000-0000-0000-000000000001/send-now")
    assert r.status_code == 403 and "APP_ENV=dev" in r.json()["detail"]


@pytest.mark.parametrize("role,allowed", [("marketing", False), (None, False), ("admin", True), ("superadmin", True)])
def test_configuration_is_admin_only(client, monkeypatch, role, allowed):
    """Marketing uses the console but cannot change settings or run dev tools."""
    c, _ = client
    saved = []

    async def set_value(key, value, description=None):
        saved.append(key)

    monkeypatch.setattr(settings_store, "set_value", set_value)
    main.app.dependency_overrides[main.require_user] = lambda: {"email": "x@aspire.com", "role": role}

    r = c.put("/settings/daily_cap", json={"value": 5})
    assert (r.status_code == 200) is allowed and saved == (["daily_cap"] if allowed else [])
    if not allowed:
        assert r.json()["detail"] == "admin_only"
        assert c.put("/nurture/settings", json={"paused": True}).status_code == 403
        assert c.post("/nurture/run/send").status_code == 403
        assert c.post("/send-now").status_code == 403
        # Everyday work stays open.
        assert c.get("/leads").status_code == 200


def test_superadmin_can_sign_in():
    from app.auth import ROLES, is_admin

    assert {"admin", "superadmin", "marketing"} <= ROLES
    assert is_admin({"role": "superadmin"}) and not is_admin({"role": "marketing"})


def test_campaign_routes_reach_the_right_handler(client, monkeypatch):
    """/enroll and /preview are not swallowed by /campaigns/{id}/{action}."""
    c, _ = client
    cid = "00000000-0000-0000-0000-0000000000c1"
    calls = []

    async def campaign_get(i):
        return {"id": i, "name": "Q4", "status": "active", "audience": {}, "playbook": {}}

    async def campaign_enroll(campaign_id, ref, contactable, skip):
        calls.append(("enroll", ref))
        return "enrolled", ref

    async def campaign_set_status(i, status):
        calls.append(("status", status))
        return 0

    monkeypatch.setattr(repo, "campaign_get", campaign_get)
    monkeypatch.setattr(repo, "campaign_enroll", campaign_enroll)
    monkeypatch.setattr(repo, "campaign_set_status", campaign_set_status)

    lead = "00000000-0000-0000-0000-000000000001"
    r = c.post(f"/campaigns/{cid}/enroll", json={"lead_ids": [lead]})
    assert r.status_code == 200 and r.json()["enrolled"] == 1
    assert c.post(f"/campaigns/{cid}/pause").status_code == 200
    assert c.post(f"/campaigns/{cid}/explode").status_code == 404
    assert calls == [("enroll", lead), ("status", "paused")]
