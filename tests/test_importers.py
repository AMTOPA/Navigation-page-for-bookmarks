from app.importers import parse_edge_html
from app.crawler import validate_public_url
from app.services import normalize_url
import pytest


def test_html_parser_preserves_nested_folders():
    html = b"""<!doctype NETSCAPE-Bookmark-file-1><DL><p>
    <DT><H3>Study</H3><DL><p><DT><H3>Math</H3><DL><p>
    <DT><A HREF="https://example.com" ADD_DATE="1700000000">Example</A>
    </DL><p></DL><p></DL><p>"""
    records = parse_edge_html(html)
    assert len(records) == 1
    assert records[0]["folder_path"] == "Study/Math"


def test_normalize_url_removes_tracking_but_keeps_real_query():
    value = normalize_url("HTTPS://Example.COM:443/a/../b/?utm_source=x&id=2#part")
    assert value == "https://example.com/b/?id=2"


def test_crawler_blocks_loopback():
    with pytest.raises(ValueError):
        validate_public_url("http://127.0.0.1/private")
