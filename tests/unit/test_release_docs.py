"""Release documents must ship with the program and stay in sync.

R-250 requires the full user guide inside the install directory and a start-menu
entry for it.  The installed copy is checked end to end in B1-01, but that only
runs on Windows; these checks catch a packaging regression on every push.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "docs" / "使用说明.txt"
NOTES = ROOT / "docs" / "版本说明.txt"
ISS = ROOT / "installer" / "paw_setup.iss"


def _iss() -> str:
    return ISS.read_text(encoding="utf-8")


def test_both_release_documents_exist_and_are_utf8_with_bom():
    for path in (GUIDE, NOTES):
        raw = path.read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf"), f"{path.name} must start with a UTF-8 BOM"
        text = raw.decode("utf-8-sig")
        assert "\ufffd" not in text, f"{path.name} must not contain replacement characters"
        assert "2.5.0" in text, f"{path.name} must name the release version"


def test_installer_ships_the_full_guide_from_docs():
    text = _iss()
    assert "使用说明.txt" in text
    # The guide is taken from docs/, not generated into dist/, so there is one
    # copy to keep correct.
    assert 'Source: "..\\docs\\使用说明.txt"' in text
    assert 'DestDir: "{app}"' in text


def test_installer_adds_a_start_menu_entry_for_the_guide():
    text = _iss()
    assert 'Name: "{group}\\使用说明"' in text
    assert 'Filename: "{app}\\使用说明.txt"' in text


def test_installer_no_longer_ships_a_generated_stub_guide():
    """The old 12-line stub must not be written into dist/ any more."""
    source = (ROOT / "scripts" / "package_app.py").read_text(encoding="utf-8")
    assert 'DIST_DIR / "使用说明.txt", "w"' not in source
    assert "readme_content" not in source


def test_version_notes_keep_only_evidenced_fixes():
    """Only the two fixes proven by negative controls are claimed."""
    text = NOTES.read_text(encoding="utf-8-sig")
    fixes = text.split("二、修复", 1)[1].split("三、已知问题", 1)[0]
    items = [line for line in fixes.splitlines() if line.strip().startswith(("1.", "2.", "3.", "4."))]
    assert len(items) == 2, f"expected 2 evidenced fixes, found {len(items)}"
    assert "巡航" in items[0]
    assert "新旧版本并列" in items[1]


def test_version_notes_state_the_real_backup_location_and_reference_page_limit():
    text = NOTES.read_text(encoding="utf-8-sig")
    # The install-time snapshot is workspace_pre_update.db, not the backups dir;
    # both are named so the reader can find whichever exists.
    assert "workspace_pre_update.db" in text
    assert "%LOCALAPPDATA%\\PAW\\backups" in text
    assert "200" in text and "资料" in text
    # Rolling back to 2.3.9 was never tested, so it must not be promised.
    assert "回退" not in text
