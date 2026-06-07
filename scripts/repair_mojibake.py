"""Scan and conservatively repair corrupted bookmark text.

The default mode only writes a JSON report. ``--apply`` first creates a
consistent SQLite backup, then repairs reversible mojibake, restores damaged
titles from active bookmark sources, and clears irrecoverable descriptions so
the worker can crawl them again.
"""

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import inspect, select
from sqlalchemy.orm import selectinload

from app.db import SessionLocal, engine
from app.models import AuditLog, Bookmark, BookmarkSource
from app.services import audit, queue_job


MOJIBAKE_SEQUENCES = (
    "锟斤拷",
    "鈥",
    "銆",
    "鐨勣",
    "浠诲姟",
    "缁勫埆",
    "鏀惰棌",
    "鎼滅储",
    "閾炬帴",
    "馃",
)
TEXT_FIELDS = (
    "title",
    "description",
    "ai_category",
    "manual_category",
    "notes",
    "summary",
    "crawl_error",
    "ai_error",
)
JSON_FIELDS = ("tags", "outline", "keywords", "key_info")


def looks_corrupt(value) -> bool:
    if isinstance(value, str):
        if "\ufffd" in value or any(sequence in value for sequence in MOJIBAKE_SEQUENCES):
            return True
        return any(ord(char) < 32 and char not in "\n\r\t" for char in value)
    if isinstance(value, list):
        return any(looks_corrupt(item) for item in value)
    if isinstance(value, dict):
        return any(looks_corrupt(key) or looks_corrupt(item) for key, item in value.items())
    return False


def quality(text: str) -> int:
    return (
        -text.count("\ufffd") * 100
        - sum(text.count(sequence) for sequence in MOJIBAKE_SEQUENCES) * 10
        - sum(ord(char) < 32 and char not in "\n\r\t" for char in text) * 20
    )


def repair_text(value: str) -> str:
    if not value or not looks_corrupt(value) or "\ufffd" in value:
        return value
    for encoding in ("gb18030", "latin1"):
        try:
            candidate = value.encode(encoding).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if quality(candidate) > quality(value):
            return candidate
    return value


def repair_nested(value):
    if isinstance(value, str):
        return repair_text(value)
    if isinstance(value, list):
        return [repair_nested(item) for item in value]
    if isinstance(value, dict):
        return {repair_text(key): repair_nested(item) for key, item in value.items()}
    return value


def clean_source_title(bookmark: Bookmark) -> str:
    sources = sorted(
        (source for source in bookmark.sources if source.is_active and source.source_title),
        key=lambda source: source.updated_at,
        reverse=True,
    )
    for source in sources:
        candidate = repair_text(source.source_title.strip())
        if candidate and not looks_corrupt(candidate):
            return candidate
    return ""


def create_backup() -> Path:
    database = engine.url.database
    if not database:
        raise RuntimeError("无法确定 SQLite 数据库路径")
    source_path = Path(database).resolve()
    backup_dir = source_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{source_path.stem}-before-mojibake-{datetime.now():%Y%m%d-%H%M%S}.db"
    with sqlite3.connect(source_path) as source, sqlite3.connect(backup_path) as target:
        source.backup(target)
    return backup_path


def report_value(value):
    if isinstance(value, str):
        return value if len(value) <= 500 else value[:500] + "…"
    serialized = json.dumps(value, ensure_ascii=False, default=str)
    return value if len(serialized) <= 1000 else serialized[:1000] + "…"


def change_record(table: str, row_id: str, field: str, old, new, method: str) -> dict:
    return {
        "table": table,
        "id": row_id,
        "field": field,
        "method": method,
        "old": report_value(old),
        "new": report_value(new),
    }


def scan_and_repair(apply: bool = False) -> dict:
    if not inspect(engine).has_table("bookmarks"):
        raise RuntimeError("当前数据库尚未初始化，找不到 bookmarks 表")
    changes = []
    skipped = []
    changed_bookmarks = set()
    recrawl_bookmarks = set()
    backup_path = create_backup() if apply else None

    with SessionLocal() as db:
        bookmarks = db.scalars(select(Bookmark).options(selectinload(Bookmark.sources))).all()
        for bookmark in bookmarks:
            overrides = set(bookmark.manual_override_fields or [])
            for field in TEXT_FIELDS:
                old = getattr(bookmark, field) or ""
                if not looks_corrupt(old):
                    continue
                new = repair_text(old)
                method = "reverse-decode"
                if field == "title" and looks_corrupt(new):
                    if "title" in overrides:
                        skipped.append(change_record("bookmarks", bookmark.id, field, old, old, "manual-lock"))
                        continue
                    new = clean_source_title(bookmark)
                    method = "active-source-title"
                elif field == "description" and looks_corrupt(new):
                    if "description" in overrides:
                        skipped.append(change_record("bookmarks", bookmark.id, field, old, old, "manual-lock"))
                        continue
                    new = ""
                    method = "clear-and-recrawl"
                    recrawl_bookmarks.add(bookmark.id)
                if new != old and not looks_corrupt(new):
                    changes.append(change_record("bookmarks", bookmark.id, field, old, new, method))
                    changed_bookmarks.add(bookmark.id)
                    if apply:
                        setattr(bookmark, field, new)
                else:
                    skipped.append(change_record("bookmarks", bookmark.id, field, old, old, "unresolved"))

            for field in JSON_FIELDS:
                old = getattr(bookmark, field)
                if not looks_corrupt(old):
                    continue
                new = repair_nested(old)
                if new != old and not looks_corrupt(new):
                    changes.append(change_record("bookmarks", bookmark.id, field, old, new, "nested-reverse-decode"))
                    changed_bookmarks.add(bookmark.id)
                    if apply:
                        setattr(bookmark, field, new)
                else:
                    skipped.append(change_record("bookmarks", bookmark.id, field, old, old, "unresolved"))

        for model, fields in (
            (BookmarkSource, ("folder_path", "source_title")),
            (AuditLog, ("message", "details")),
        ):
            for row in db.scalars(select(model)).all():
                for field in fields:
                    old = getattr(row, field)
                    if not looks_corrupt(old):
                        continue
                    new = repair_nested(old)
                    if new != old and not looks_corrupt(new):
                        changes.append(change_record(model.__tablename__, row.id, field, old, new, "reverse-decode"))
                        if apply:
                            setattr(row, field, new)
                    else:
                        skipped.append(
                            change_record(model.__tablename__, row.id, field, old, old, "unresolved")
                        )

        if apply:
            for bookmark_id in changed_bookmarks:
                queue_job(db, bookmark_id, "search_index", force=True)
            for bookmark_id in recrawl_bookmarks:
                bookmark = db.get(Bookmark, bookmark_id)
                bookmark.is_crawled = False
                bookmark.crawl_status = "pending"
                bookmark.crawl_error = ""
                queue_job(db, bookmark_id, "crawl", force=True)
            audit(
                db,
                "mojibake_repaired",
                "完成乱码扫描与修复",
                actor="maintenance",
                changed_fields=len(changes),
                changed_bookmarks=len(changed_bookmarks),
                recrawl_bookmarks=len(recrawl_bookmarks),
            )
            db.commit()

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "applied": apply,
        "backup": str(backup_path) if backup_path else "",
        "changed_fields": len(changes),
        "changed_bookmarks": len(changed_bookmarks),
        "recrawl_bookmarks": len(recrawl_bookmarks),
        "changes": changes,
        "skipped": skipped,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = scan_and_repair(args.apply)
    report_path = args.report or Path("data") / (
        f"mojibake-{'apply' if args.apply else 'preview'}-{datetime.now():%Y%m%d-%H%M%S}.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key not in {"changes", "skipped"}}, ensure_ascii=False))
    print(f"Report: {report_path.resolve()}")


if __name__ == "__main__":
    main()
