"""Conservatively repair UTF-8 text that was accidentally decoded as GB18030.

Run without --apply to preview. Always back up the SQLite database before using
--apply in production.
"""

import argparse
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.models import AuditLog, Bookmark, BookmarkSource


SUSPICIOUS = set("\u93b6\u951b\u9286\u9427\u7035\u93b4\u95b0\u935a\u9225\u7f01")
COMMON = set("收藏导航登录后台管理分类标签抓取配置任务失败成功时间链接标题")


def quality(text: str) -> int:
    return sum(char in COMMON for char in text) * 3 - sum(char in SUSPICIOUS for char in text) * 4


def repair_value(value: str) -> str:
    if not value or not any(char in value for char in SUSPICIOUS):
        return value
    try:
        candidate = value.encode("gb18030").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value
    return candidate if quality(candidate) > quality(value) else value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    changes = []
    with SessionLocal() as db:
        targets = [
            (Bookmark, ["title", "description", "ai_category", "manual_category", "notes", "summary", "crawl_error", "ai_error"]),
            (BookmarkSource, ["folder_path", "source_title"]),
            (AuditLog, ["message"]),
        ]
        for model, fields in targets:
            for row in db.scalars(select(model)).all():
                for field in fields:
                    old = getattr(row, field) or ""
                    new = repair_value(old)
                    if new != old:
                        changes.append((model.__tablename__, row.id, field, old, new))
                        if args.apply:
                            setattr(row, field, new)
        if args.apply:
            db.commit()
    for table, row_id, field, old, new in changes[:200]:
        print(f"{table}:{row_id}:{field}\n  - {old}\n  + {new}")
    print(f"{'Applied' if args.apply else 'Preview'}: {len(changes)} field(s)")


if __name__ == "__main__":
    main()
