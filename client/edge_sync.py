import argparse
import hashlib
import json
import mimetypes
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen


CHROME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


def chrome_time(value: str | int | None):
    try:
        return (CHROME_EPOCH + timedelta(microseconds=int(value))).isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def edge_bookmarks_path(profile: str) -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "Edge" / "User Data" / profile / "Bookmarks"


def read_edge_bookmarks(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    records = []

    def walk(node, folders):
        if node.get("type") == "url":
            records.append(
                {
                    "guid": node["guid"],
                    "url": node.get("url", ""),
                    "title": node.get("name", ""),
                    "folder_path": "/".join(folders),
                    "favorited_at": chrome_time(node.get("date_added")),
                }
            )
            return
        next_folders = folders + ([node.get("name", "")] if node.get("name") else [])
        for child in node.get("children", []):
            walk(child, next_folders)

    for root in data.get("roots", {}).values():
        walk(root, [])
    return sorted(records, key=lambda item: item["guid"])


def fingerprint(item: dict) -> str:
    compact = {key: item.get(key) for key in ("guid", "url", "title", "folder_path", "favorited_at")}
    path = file_url_to_path(item.get("url", ""))
    if path and path.is_file():
        stat = path.stat()
        compact["local_file"] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    return hashlib.sha256(json.dumps(compact, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def snapshot(records: list[dict]) -> tuple[str, dict]:
    state = {item["guid"]: fingerprint(item) for item in records}
    digest = hashlib.sha256(json.dumps(state, sort_keys=True).encode("utf-8")).hexdigest()
    return digest, state


def request_json(url: str, token: str, payload: dict) -> dict:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def file_url_to_path(url: str) -> Path | None:
    parts = urlsplit(url)
    if parts.scheme != "file":
        return None
    path = unquote(parts.path)
    if os.name == "nt" and path.startswith("/") and len(path) > 2 and path[2] == ":":
        path = path[1:]
    return Path(path)


def upload_file(url: str, token: str, guid: str, path: Path):
    boundary = "----BookmarkSync" + uuid.uuid4().hex
    content = path.read_bytes()
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    chunks = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"guid\"\r\n\r\n{guid}\r\n".encode(),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"upload\"; filename=\"{path.name}\"\r\nContent-Type: {mime}\r\n\r\n".encode(),
        content,
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    request = Request(
        url,
        data=b"".join(chunks),
        headers={"Authorization": f"Bearer {token}", "Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urlopen(request, timeout=300) as response:
        return json.loads(response.read().decode("utf-8"))


def load_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def main():
    parser = argparse.ArgumentParser(description="检测并同步 Edge 收藏")
    parser.add_argument("--config", default=str(Path(__file__).with_name("sync_config.json")))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    config_path = Path(args.config)
    config = load_json(config_path, {})
    required = [key for key in ("server_url", "api_token") if not config.get(key)]
    if required:
        raise SystemExit(f"配置缺少: {', '.join(required)}。请参考 sync_config.example.json")
    profile = config.get("edge_profile", "Default")
    bookmarks_path = Path(config.get("bookmarks_path") or edge_bookmarks_path(profile))
    state_path = Path(config.get("state_file") or config_path.with_name(".edge_sync_state.json"))
    records = read_edge_bookmarks(bookmarks_path)
    digest, current = snapshot(records)
    previous = load_json(state_path, {"snapshot_hash": "", "items": {}})
    pending_attachments = set(previous.get("pending_attachments", []))
    if not args.force and digest == previous.get("snapshot_hash") and not pending_attachments:
        print("收藏无变化，不连接服务器。")
        return
    old_items = previous.get("items", {})
    changed_guids = {guid for guid, value in current.items() if old_items.get(guid) != value}
    removed_guids = sorted(set(old_items) - set(current))
    upserts = [item for item in records if item["guid"] in changed_guids]
    batch_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + digest[:12]
    base_url = config["server_url"].rstrip("/")
    if args.force or digest != previous.get("snapshot_hash"):
        result = request_json(
            base_url + "/api/sync/edge",
            config["api_token"],
            {"batch_id": batch_id, "snapshot_hash": digest, "upserts": upserts, "removed_guids": removed_guids},
        )
        print(f"同步完成：新增 {result['added']}，更新 {result['updated']}，移除 {result['removed']}")
    if config.get("upload_local_files", True):
        max_bytes = int(config.get("max_attachment_mb", 100)) * 1024 * 1024
        allowed = {value.lower() for value in config.get("allowed_extensions", [".pdf", ".txt", ".md", ".html", ".htm", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".png", ".jpg", ".jpeg", ".zip", ".7z"])}
        upload_guids = changed_guids | pending_attachments
        pending_attachments = set()
        for item in (record for record in records if record["guid"] in upload_guids):
            path = file_url_to_path(item["url"])
            if not path or not path.is_file():
                continue
            if path.suffix.lower() not in allowed or path.stat().st_size > max_bytes:
                print(f"跳过附件：{path}")
                continue
            try:
                upload_file(base_url + "/api/sync/attachment", config["api_token"], item["guid"], path)
                print(f"已上传附件：{path.name}")
            except Exception as exc:
                pending_attachments.add(item["guid"])
                print(f"附件上传失败 {path}: {exc}", file=sys.stderr)
    state_path.write_text(
        json.dumps(
            {
                "snapshot_hash": digest,
                "items": current,
                "pending_attachments": sorted(pending_attachments),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
