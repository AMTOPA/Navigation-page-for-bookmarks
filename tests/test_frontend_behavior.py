import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_node(script: str) -> str:
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_naive_database_datetime_is_treated_as_utc():
    api_js = (ROOT / "frontend/assets/api.js").as_posix()
    output = run_node(
        f"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync({api_js!r}, "utf8");
const start = source.indexOf("function formatTime");
const end = source.indexOf("\\nfunction initTheme", start);
vm.runInThisContext(source.slice(start, end));
console.log(formatTime("2026-06-07 13:23:22.922293"));
"""
    )
    assert "21:23:22" in output


def test_search_style_control_stays_outside_collapsible_panel():
    html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
    control_start = html.index('<section class="control-panel"')
    control_end = html.index("</section>", control_start)
    select_position = html.index('id="searchBarMode"')
    assert select_position < control_start or select_position > control_end
    assert 'id="groupRailToggle"' in html
