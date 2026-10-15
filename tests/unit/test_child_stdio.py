"""Reader children must survive Chinese output on an English-locale console.

Reproduces the cloud failure "'charmap' codec can't encode characters" by
forcing the child's stdio to cp1252, the code page of English Windows runners.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

MODULE = Path(__file__).resolve().parents[2] / "backend" / "pm" / "child_stdio.py"
NAME = "冲刺项目周报.docx"

CHILD = f"""
import importlib.util, sys
spec = importlib.util.spec_from_file_location("child_stdio", {str(MODULE)!r})
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
if sys.argv[1] == "fixed":
    mod.force_utf8_stdio()
    sys.stderr.write("Worker parse error: {NAME}\\n")
    mod.emit_json({{"ok": True, "result_json": "C:/data/{NAME}"}})
else:
    sys.stdout.write('{{"result_json": "C:/data/{NAME}"}}')
"""


def run_child(mode):
    env = dict(os.environ, PYTHONIOENCODING="cp1252", PYTHONUTF8="0")
    return subprocess.run([sys.executable, "-c", CHILD, mode], capture_output=True, env=env, timeout=30)


def test_unfixed_child_crashes_under_cp1252():
    # Negative control: proves this test reproduces the cloud failure.
    proc = run_child("raw")
    assert proc.returncode != 0
    assert b"charmap" in proc.stderr


def test_fixed_child_writes_utf8_and_ascii_json():
    proc = run_child("fixed")
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert proc.stdout.isascii()
    assert json.loads(proc.stdout.decode("ascii"))["result_json"] == f"C:/data/{NAME}"
    assert NAME in proc.stderr.decode("utf-8")
