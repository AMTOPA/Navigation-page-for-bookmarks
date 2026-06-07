from datetime import datetime, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.models import AdminUser
from app.search_api import _event


def test_login_required(client):
    assert client.get("/api/bookmarks").status_code == 401


def test_create_update_and_soft_delete(client, auth):
    created = client.post("/api/bookmarks", headers=auth, json={"url": "https://Example.com/path/?utm_source=x", "title": "Example"})
    assert created.status_code == 200
    item = created.json()
    assert "title" in item["manual_override_fields"]
    updated = client.patch(
        f"/api/bookmarks/{item['id']}",
        headers=auth,
        json={"category": "手动分类", "tags": ["测试", "工具"]},
    )
    assert updated.status_code == 200
    assert updated.json()["final_category"] == "手动分类"
    assert {"category", "tags"}.issubset(updated.json()["manual_override_fields"])
    assert client.delete(f"/api/bookmarks/{item['id']}", headers=auth).status_code == 200
    assert all(value["id"] != item["id"] for value in client.get("/api/bookmarks").json())
    assert client.post(f"/api/bookmarks/{item['id']}/restore", headers=auth).status_code == 200


def test_csrf_required(client):
    client.post("/api/auth/login", json={"username": "admin", "password": "test-password"})
    response = client.post("/api/bookmarks", json={"url": "https://example.org"})
    assert response.status_code == 403


def test_edge_sync_merges_same_url(client, auth):
    token = client.post("/api/settings/tokens", headers=auth, json={"name": "sync-test", "scopes": ["sync"]}).json()["token"]
    payload = {
        "batch_id": "batch-test",
        "snapshot_hash": "abc",
        "upserts": [
            {"guid": "g1", "url": "https://example.net/", "title": "One", "folder_path": "A"},
            {"guid": "g2", "url": "https://example.net/?utm_source=edge", "title": "Two", "folder_path": "B"},
        ],
        "removed_guids": [],
    }
    response = client.post("/api/sync/edge", headers={"Authorization": f"Bearer {token}"}, json=payload)
    assert response.status_code == 200
    matches = [item for item in client.get("/api/bookmarks").json() if item["url"].startswith("https://example.net")]
    assert len(matches) == 1
    assert {source["folder_path"] for source in matches[0]["sources"]} == {"A", "B"}


def test_sync_token_cannot_edit_bookmarks(client, auth):
    token = client.post("/api/settings/tokens", headers=auth, json={"name": "sync-only", "scopes": ["sync"]}).json()["token"]
    response = client.post(
        "/api/bookmarks",
        headers={"Authorization": f"Bearer {token}"},
        json={"url": "https://not-allowed.example"},
    )
    assert response.status_code == 403


def test_edge_removal_soft_deletes_only_source(client, auth):
    token = client.post("/api/settings/tokens", headers=auth, json={"name": "remove-test", "scopes": ["sync"]}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    created = client.post("/api/sync/edge", headers=headers, json={
        "batch_id": "remove-add", "snapshot_hash": "one",
        "upserts": [{"guid": "remove-guid", "url": "https://remove.example", "title": "Remove"}],
        "removed_guids": []
    })
    assert created.status_code == 200
    removed = client.post("/api/sync/edge", headers=headers, json={
        "batch_id": "remove-delete", "snapshot_hash": "two",
        "upserts": [], "removed_guids": ["remove-guid"]
    })
    assert removed.status_code == 200
    assert all(item["url"] != "https://remove.example" for item in client.get("/api/bookmarks").json())


def test_locked_login_returns_401_not_500(client):
    for _ in range(5):
        client.post("/api/auth/login", json={"username": "admin", "password": "wrong-password"})
    response = client.post("/api/auth/login", json={"username": "admin", "password": "wrong-password"})
    assert response.status_code in {401, 429}
    with SessionLocal() as db:
        user = db.scalar(select(AdminUser).where(AdminUser.username == "admin"))
        user.failed_attempts = 0
        user.locked_until = None
        db.commit()


def test_navigation_snapshot_is_compact_and_supports_etag(client, auth):
    created = client.post(
        "/api/bookmarks",
        headers=auth,
        json={"url": "https://snapshot.example", "title": "Snapshot"},
    )
    assert created.status_code == 200
    response = client.get("/api/navigation/snapshot")
    assert response.status_code == 200
    assert response.headers["etag"]
    item = next(value for value in response.json()["items"] if value["url"] == "https://snapshot.example")
    assert "folder_paths" in item
    assert "outline" not in item
    assert "sources" not in item
    cached = client.get(
        "/api/navigation/snapshot",
        headers={"If-None-Match": response.headers["etag"]},
    )
    assert cached.status_code == 304


def test_paginated_management_endpoints(client, auth):
    bookmarks = client.get("/api/bookmarks?page=1&page_size=2&compact=true")
    assert bookmarks.status_code == 200
    assert set(bookmarks.json()) >= {"items", "page", "page_size", "total", "pages"}
    assert len(bookmarks.json()["items"]) <= 2

    jobs = client.get("/api/jobs?page=1&page_size=2")
    assert jobs.status_code == 200
    assert len(jobs.json()["items"]) <= 2

    logs = client.get("/api/logs?page=1&page_size=2")
    assert logs.status_code == 200
    assert len(logs.json()["items"]) <= 2


def test_ai_search_route_is_exposed_in_model_settings(client, auth):
    response = client.get("/api/settings/providers")
    assert response.status_code == 200
    assert "ai_search" in response.json()["routes"]


def test_stream_event_serializes_bookmark_datetimes():
    value = _event("results", {"items": [{"updated_at": datetime(2026, 6, 7, tzinfo=timezone.utc)}]})
    assert "event: results" in value
    assert "2026-06-07T00:00:00+00:00" in value
