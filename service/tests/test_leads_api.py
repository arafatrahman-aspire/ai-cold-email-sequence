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
