"""The shipped UI displays every batch review failure as Chinese text."""
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_batch_failure_formatter_shows_each_reason_and_chinese_fallbacks():
    script = r'''
      const format = require(process.argv[1]).formatBatchFailureDetails;
      const actual = format([
        {id: "internal-a", error: "标题不能为空"},
        {id: "internal-b", error: ""},
        {id: "internal-c", error: "database is locked"},
        {id: "internal-d", error: "unmapped technical detail"}
      ]);
      process.stdout.write(JSON.stringify(actual));
    '''
    result = subprocess.run(
        ["node", "-e", script, str(ROOT / "frontend/dist/batch_review_errors.js")],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    shown = json.loads(result.stdout)
    assert shown.splitlines() == [
        "第 1 条：标题不能为空",
        "第 2 条：未知原因",
        "第 3 条：本地数据暂时被占用，请稍后重试。",
        "第 4 条：未知原因",
    ]
    assert "internal-" not in shown


def test_index_html_versions_the_app_and_batch_review_scripts_for_rc3():
    html = (ROOT / "frontend/dist/index.html").read_text(encoding="utf-8")
    assert "/assets/index-qIwUsXqw.js?v=2.5.0-rc3" in html
    assert "/paw_console.js?v=2.5.0-rc3" in html
