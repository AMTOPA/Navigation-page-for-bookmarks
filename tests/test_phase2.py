from pathlib import Path

import pytest
from sqlalchemy import func, select

from app import search_service
from app.ai_service import get_routed_model
from app.db import SessionLocal
from app.models import AdminUser, AIModel, AIProvider, AIRoute, Bookmark, Job, JobBatch, SearchIndex
from app.search_service import hybrid_search, index_bookmark
from app.security import encrypt_value
from app.worker import finish_batch_if_complete


def test_account_update_revokes_session(client):
    with SessionLocal() as db:
        user = db.scalar(select(AdminUser).where(AdminUser.username == "admin"))
        user.failed_attempts = 0
        user.locked_until = None
        db.commit()
    login = client.post("/api/auth/login", json={"username": "admin", "password": "test-password"})
    assert login.status_code == 200
    auth = {"X-CSRF-Token": login.json()["csrf_token"]}
    response = client.put(
        "/api/settings/account",
        headers=auth,
        json={
            "current_password": "test-password",
            "username": "admin-updated",
            "new_password": "updated-password",
        },
    )
    assert response.status_code == 200
    assert client.get("/api/auth/me").status_code == 401
    login = client.post("/api/auth/login", json={"username": "admin-updated", "password": "updated-password"})
    assert login.status_code == 200
    restore_headers = {"X-CSRF-Token": login.json()["csrf_token"]}
    restored = client.put(
        "/api/settings/account",
        headers=restore_headers,
        json={
            "current_password": "updated-password",
            "username": "admin",
            "new_password": "test-password",
        },
    )
    assert restored.status_code == 200


def test_embedding_index_is_persistent_and_incremental(monkeypatch):
    calls = []

    def fake_embedding(_provider, _model, texts):
        calls.extend(texts)
        return [[float(len(value)), 1.0, 0.5] for value in texts]

    monkeypatch.setattr(search_service, "embedding", fake_embedding)
    with SessionLocal() as db:
        provider = AIProvider(
            name="Embedding Test",
            base_url="https://api.example.test/v1",
            encrypted_api_key=encrypt_value("secret"),
        )
        db.add(provider)
        db.flush()
        model = AIModel(
            provider_id=provider.id,
            display_name="Embedding",
            model_name="embedding-test",
            capabilities=["embedding"],
        )
        db.add(model)
        db.flush()
        route = db.get(AIRoute, "embedding") or AIRoute(task_type="embedding")
        route.model_id = model.id
        db.add(route)
        bookmark = Bookmark(url="https://semantic.example", normalized_url="https://semantic.example/", title="线性代数课程")
        db.add(bookmark)
        db.commit()

        index_bookmark(db, bookmark)
        db.commit()
        assert len(calls) == 1
        index_bookmark(db, bookmark)
        db.commit()
        assert len(calls) == 1
        assert db.scalar(select(func.count(SearchIndex.id)).where(SearchIndex.bookmark_id == bookmark.id)) == 1

        bookmark.summary = "矩阵、向量空间与特征值"
        index_bookmark(db, bookmark)
        db.commit()
        assert len(calls) == 2

        first = hybrid_search(db, "矩阵课程", mode="semantic")
        db.commit()
        second = hybrid_search(db, "矩阵课程", mode="semantic")
        assert first["items"][0]["id"] == bookmark.id
        assert second["query_cache_hit"] is True
        assert len(calls) == 3


def test_keyword_search_works_before_fts_rebuild():
    with SessionLocal() as db:
        bookmark = Bookmark(
            url="https://keyword-fallback.example",
            normalized_url="https://keyword-fallback.example/",
            title="暑期学校报名通知",
            description="面向数学专业学生",
        )
        db.add(bookmark)
        db.commit()
        result = hybrid_search(db, "暑期学校", mode="lexical")
        assert any(item["id"] == bookmark.id for item in result["items"])


def test_embedding_requires_explicit_embedding_route():
    with SessionLocal() as db:
        route = db.get(AIRoute, "embedding")
        previous = route.model_id if route else None
        if route:
            route.model_id = None
        else:
            db.add(AIRoute(task_type="embedding", model_id=None))
        db.commit()
        try:
            with pytest.raises(ValueError, match="Embedding"):
                get_routed_model(db, "embedding")
        finally:
            route = db.get(AIRoute, "embedding")
            route.model_id = previous
            db.commit()


def test_search_index_switches_only_after_successful_batch():
    with SessionLocal() as db:
        provider = AIProvider(name="Atomic Provider", base_url="https://atomic.example/v1")
        db.add(provider)
        db.flush()
        old_model = AIModel(provider_id=provider.id, display_name="Old", model_name="old", capabilities=["embedding"])
        new_model = AIModel(provider_id=provider.id, display_name="New", model_name="new", capabilities=["embedding"])
        db.add_all([old_model, new_model])
        db.flush()
        route = db.get(AIRoute, "embedding") or AIRoute(task_type="embedding")
        route.model_id = new_model.id
        db.add(route)
        bookmark = Bookmark(url="https://atomic.example", normalized_url="https://atomic.example/", title="Atomic")
        db.add(bookmark)
        db.flush()
        old_index = SearchIndex(
            bookmark_id=bookmark.id, model_id=old_model.id, text_hash="old",
            vector=search_service.pack_vector([1.0]), dimensions=1, index_version=1, active=True,
        )
        new_index = SearchIndex(
            bookmark_id=bookmark.id, model_id=new_model.id, text_hash="new",
            vector=search_service.pack_vector([1.0]), dimensions=1, index_version=1, active=True,
        )
        batch = JobBatch(name="Atomic rebuild", job_type="search_index")
        db.add_all([old_index, new_index, batch])
        db.flush()
        db.add(Job(bookmark_id=bookmark.id, batch_id=batch.id, job_type="search_index", status="success"))
        db.flush()
        finish_batch_if_complete(db, batch.id)
        db.commit()
        db.expire_all()
        assert db.get(SearchIndex, old_index.id).active is False
        assert db.get(SearchIndex, new_index.id).active is True


def test_frontend_and_backend_have_no_common_mojibake_markers():
    suspicious = set("\u93b6\u951b\u9286\u9427\u7035\u93b4\u95b0\u935a\u9225\u7f01")
    root = Path(__file__).resolve().parents[1]
    bad = []
    for folder in ("backend/app", "frontend"):
        for path in (root / folder).rglob("*"):
            if path.suffix not in {".py", ".js", ".html", ".css"}:
                continue
            text = path.read_text(encoding="utf-8")
            if suspicious.intersection(text):
                bad.append(str(path.relative_to(root)))
    assert bad == []
