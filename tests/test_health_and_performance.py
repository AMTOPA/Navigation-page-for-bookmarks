from pathlib import Path

import httpx
from sqlalchemy import inspect, select

from app import health_service, worker
from app.db import SessionLocal, engine
from app.models import Bookmark, Job, LinkHealth


ROOT = Path(__file__).resolve().parents[1]


def test_health_check_marks_non_http_links_unsupported():
    result = health_service.check_link_health("file:///C:/notes.html")
    assert result["status"] == "unsupported"
    assert result["http_status"] is None


def test_health_check_records_redirect(monkeypatch):
    response = httpx.Response(200, request=httpx.Request("HEAD", "https://final.example"))
    monkeypatch.setattr(
        health_service,
        "_follow",
        lambda _client, _method, _url: (response, "https://final.example", True),
    )
    result = health_service.check_link_health("https://start.example")
    assert result["status"] == "redirected"
    assert result["http_status"] == 200
    assert result["final_url"] == "https://final.example"


def test_health_api_and_worker_persist_result(client, auth, monkeypatch):
    created = client.post("/api/bookmarks", headers=auth, json={"url": "https://health.example"}).json()
    queued = client.post(
        "/api/health-checks/run",
        headers=auth,
        json=[created["id"]],
    )
    assert queued.status_code == 200
    assert queued.json()["count"] == 1

    monkeypatch.setattr(
        worker,
        "check_link_health",
        lambda _url: {
            "status": "ok",
            "http_status": 204,
            "final_url": "https://health.example",
            "latency_ms": 12,
            "error": "",
        },
    )
    with SessionLocal() as db:
        bookmark = db.get(Bookmark, created["id"])
        job = db.scalar(
            select(Job).where(
                Job.bookmark_id == bookmark.id,
                Job.job_type == "health_check",
            )
        )
        worker.process_health_check(db, job, bookmark)
        db.commit()
        health = db.scalar(select(LinkHealth).where(LinkHealth.bookmark_id == bookmark.id))
        assert health.status == "ok"
        assert health.http_status == 204

    listing = client.get("/api/health-checks?page=1&page_size=20")
    assert listing.status_code == 200
    assert listing.json()["items"][0]["bookmark_id"] == created["id"]


def test_paginated_endpoints_default_to_twenty(client, auth):
    assert client.get("/api/bookmarks?page=1&compact=true").json()["page_size"] == 20
    assert client.get("/api/jobs?page=1").json()["page_size"] == 20
    assert client.get("/api/logs?page=1").json()["page_size"] == 20


def test_api_exposes_server_timing(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.headers["server-timing"].startswith("app;dur=")


def test_frontend_uses_utf8_without_known_mojibake():
    markers = ("锟斤拷", "鈥", "銆", "鐨勣", "浠诲姟", "缁勫埆", "鏀惰棌", "鎼滅储", "閾炬帴", "馃", "\ufffd")
    targets = [
        *ROOT.glob("backend/app/*.py"),
        *ROOT.glob("frontend/*.html"),
        *ROOT.glob("frontend/assets/*.*"),
    ]
    for path in targets:
        content = path.read_bytes()
        assert not content.startswith(b"\xef\xbb\xbf"), path
        text = content.decode("utf-8")
        assert not any(marker in text for marker in markers), path


def test_frontend_has_independent_ai_input_and_selectable_paging():
    html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
    nav = (ROOT / "frontend/assets/nav.js").read_text(encoding="utf-8")
    admin = (ROOT / "frontend/assets/admin.js").read_text(encoding="utf-8")
    assert 'id="aiQuery"' in html
    assert 'id="aiRestoreDock"' in html
    assert "syncSearchInputs" in nav
    assert "clearAiPanelGeometryStyles" in nav
    assert "PAGE_SIZE_OPTIONS = [10, 20, 30, 50, 100]" in admin
    assert 'localStorage.setItem(`adminPageSize:${name}`' in admin
    admin_html = (ROOT / "frontend/admin.html").read_text(encoding="utf-8")
    for section in ("bookmarks", "jobs", "logs", "health"):
        assert f'data-page-size-for="{section}"' in admin_html


def test_performance_indexes_are_present():
    indexes = inspect(engine).get_indexes("jobs")
    assert "ix_jobs_created_at" in {item["name"] for item in indexes}
