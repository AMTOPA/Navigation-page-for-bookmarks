from sqlalchemy import select

from app.crawler import decode_html, extract_html
from app.db import SessionLocal
from app.models import AIModel, AIProvider, Bookmark, BookmarkSource
from scripts import repair_mojibake


def html_bytes(text: str, encoding: str, declared: str) -> bytes:
    return (
        f'<html><head><meta charset="{declared}"><title>{text}</title>'
        f'<meta name="description" content="{text} 简介"></head><body>{text}</body></html>'
    ).encode(encoding)


def test_html_decoder_handles_utf8_gbk_and_wrong_header():
    utf8 = html_bytes("中文标题", "utf-8", "utf-8")
    gbk = html_bytes("中文标题", "gb18030", "gbk")
    wrong_header = html_bytes("中文标题", "utf-8", "utf-8")

    assert decode_html(utf8, "text/html; charset=utf-8")[0].count("中文标题") == 3
    assert decode_html(gbk, "text/html; charset=gbk")[0].count("中文标题") == 3
    assert decode_html(wrong_header, "text/html; charset=windows-1252")[0].count("中文标题") == 3
    assert extract_html(gbk, "https://example.com", "text/html; charset=gbk")["title"] == "中文标题"


def test_mojibake_repair_uses_source_title_and_respects_manual_lock(monkeypatch, tmp_path):
    monkeypatch.setattr(repair_mojibake, "create_backup", lambda: tmp_path / "backup.db")
    with SessionLocal() as db:
        repairable = Bookmark(
            url="https://repair.example",
            normalized_url="https://repair.example/",
            title="损坏\ufffd标题",
            description="损坏\ufffd简介",
        )
        locked = Bookmark(
            url="https://locked.example",
            normalized_url="https://locked.example/",
            title="手动\ufffd标题",
            manual_override_fields=["title"],
        )
        db.add_all([repairable, locked])
        db.flush()
        db.add_all(
            [
                BookmarkSource(
                    bookmark_id=repairable.id,
                    source_type="edge_html",
                    external_id="repair-source",
                    source_title="正确标题",
                    is_active=True,
                ),
                BookmarkSource(
                    bookmark_id=locked.id,
                    source_type="edge_html",
                    external_id="locked-source",
                    source_title="不应覆盖",
                    is_active=True,
                ),
            ]
        )
        db.commit()
        repairable_id = repairable.id
        locked_id = locked.id

    result = repair_mojibake.scan_and_repair(apply=True)
    assert result["changed_bookmarks"] >= 1
    assert result["recrawl_bookmarks"] >= 1
    with SessionLocal() as db:
        assert db.get(Bookmark, repairable_id).title == "正确标题"
        assert db.get(Bookmark, repairable_id).description == ""
        assert db.get(Bookmark, locked_id).title == "手动\ufffd标题"


def test_ai_search_models_are_filtered_and_do_not_expose_keys(client, auth):
    with SessionLocal() as db:
        provider = AIProvider(
            name="Search Model Provider",
            base_url="https://models.example/v1",
            encrypted_api_key="must-not-leak",
        )
        db.add(provider)
        db.flush()
        chat = AIModel(
            provider_id=provider.id,
            display_name="Fast Search",
            model_name="fast-search",
            capabilities=["chat"],
        )
        embedding = AIModel(
            provider_id=provider.id,
            display_name="Embedding Only",
            model_name="embedding-only",
            capabilities=["embedding"],
        )
        db.add_all([chat, embedding])
        db.commit()
        chat_id = chat.id
        embedding_id = embedding.id

    response = client.get("/api/ai-search/models")
    assert response.status_code == 200
    body = response.json()
    serialized = response.text
    assert chat_id in {model["id"] for model in body["models"]}
    assert embedding_id not in {model["id"] for model in body["models"]}
    assert "must-not-leak" not in serialized
    assert client.post(
        "/api/ai-search/stream",
        json={"query": "test", "model_id": embedding_id},
    ).status_code == 400
