import hashlib
from datetime import datetime, timezone

from html.parser import HTMLParser


def unix_time(value: str | None):
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc) if value else None
    except (TypeError, ValueError, OSError):
        return None


def parse_edge_html(content: bytes) -> list[dict]:
    class BookmarkParser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.records = []
            self.folders = []
            self.pending_folder = None
            self.current = None
            self.capture = ""
            self.buffer = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            tag = tag.lower()
            if tag == "h3":
                self.capture, self.buffer = "h3", []
            elif tag == "a" and attrs.get("href"):
                self.capture, self.buffer = "a", []
                self.current = attrs
            elif tag == "dl" and self.pending_folder is not None:
                self.folders.append(self.pending_folder)
                self.pending_folder = None

        def handle_endtag(self, tag):
            tag = tag.lower()
            if tag == "h3" and self.capture == "h3":
                self.pending_folder = "".join(self.buffer).strip()
                self.capture, self.buffer = "", []
            elif tag == "a" and self.capture == "a" and self.current:
                url = self.current["href"].strip()
                title = "".join(self.buffer).strip() or url
                raw_id = f"{'/'.join(self.folders)}\0{url}\0{self.current.get('add_date', '')}"
                self.records.append(
                    {
                        "external_id": "html-" + hashlib.sha256(raw_id.encode("utf-8")).hexdigest(),
                        "url": url,
                        "title": title,
                        "folder_path": "/".join(self.folders),
                        "favorited_at": unix_time(self.current.get("add_date")),
                        "icon": self.current.get("icon", ""),
                    }
                )
                self.capture, self.buffer, self.current = "", [], None
            elif tag == "dl" and self.folders:
                self.folders.pop()

        def handle_data(self, data):
            if self.capture:
                self.buffer.append(data)

    parser = BookmarkParser()
    parser.feed(content.decode("utf-8", errors="replace"))
    return parser.records
