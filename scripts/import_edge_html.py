import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.importers import parse_edge_html  # noqa: E402
from app.services import get_or_create_bookmark, queue_job, upsert_source  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("html_file")
    parser.add_argument("--queue-missing", action="store_true")
    args = parser.parse_args()
    records = parse_edge_html(Path(args.html_file).read_bytes())
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        added = 0
        for record in records:
            bookmark, created = get_or_create_bookmark(
                db, record["url"], record["title"], record["favorited_at"]
            )
            added += int(created)
            if record["icon"] and not bookmark.favicon:
                bookmark.favicon = record["icon"]
            upsert_source(
                db,
                bookmark,
                "edge_html",
                record["external_id"],
                record["folder_path"],
                record["title"],
                record["url"],
                record["favorited_at"],
                "cli-import",
            )
            if args.queue_missing and not bookmark.is_crawled:
                queue_job(db, bookmark.id, "crawl")
            if args.queue_missing and not bookmark.is_ai_classified:
                queue_job(db, bookmark.id, "ai")
        db.commit()
    print(f"导入 {len(records)} 条，新增 {added} 条。")


if __name__ == "__main__":
    main()
