import json

from client.edge_sync import read_edge_bookmarks, snapshot


def test_edge_json_reader(tmp_path):
    path = tmp_path / "Bookmarks"
    path.write_text(json.dumps({"roots": {"bookmark_bar": {"name": "收藏夹栏", "children": [
        {"type": "folder", "name": "工具", "children": [
            {"type": "url", "guid": "abc", "name": "站点", "url": "https://example.com", "date_added": "13300000000000000"}
        ]}
    ]}}}, ensure_ascii=False), encoding="utf-8")
    records = read_edge_bookmarks(path)
    assert records[0]["folder_path"] == "收藏夹栏/工具"
    assert snapshot(records)[0] == snapshot(records)[0]
