import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.services import get_or_create_bookmark, queue_job, upsert_source  # noqa: E402


def parse_date(value):
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        try:
            return datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("json_file", nargs="?", default=str(ROOT / "bookmarks_final.json"))
    parser.add_argument("--queue-missing", action="store_true")
    args = parser.parse_args()
    source = Path(args.json_file)
    backup_dir = Path(os.getenv("LEGACY_BACKUP_DIR", ROOT / "data" / "legacy-backups"))
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{source.stem}-{datetime.now().strftime('%Y%m%d-%H%M%S')}{source.suffix}"
    shutil.copy2(source, backup)
    records = json.loads(source.read_text(encoding="utf-8"))
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        added = 0
        for index, item in enumerate(records):
            favorited_at = parse_date(item.get("add_date") or item.get("add_date_readable"))
            bookmark, created = get_or_create_bookmark(db, item["url"], item.get("name", ""), favorited_at)
            added += int(created)
            if item.get("category") and not bookmark.ai_category:
                bookmark.ai_category = item["category"]
                bookmark.is_ai_classified = True
                bookmark.ai_status = "success"
            for field in ("favicon", "notes"):
                if item.get(field) and not getattr(bookmark, field):
                    setattr(bookmark, field, item[field])
            if item.get("tags") and not bookmark.tags:
                bookmark.tags = item["tags"]
            upsert_source(
                db, bookmark, "legacy_json", f"legacy-{index}-{item.get('hash', '')}",
                title=item.get("name", ""), url=item["url"], favorited_at=favorited_at
            )
            if args.queue_missing and not bookmark.is_crawled:
                queue_job(db, bookmark.id, "crawl")
        db.commit()
    print(f"迁移 {len(records)} 条，新增 {added} 条。原文件备份到 {backup}")


if __name__ == "__main__":
    main()
