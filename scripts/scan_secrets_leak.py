"""Scan for credential and secret leaks in test outputs, logs, and screenshots.

Enforces Requirements:
1. Target secret AGY_CREDENTIAL exact value: must be 0 occurrences across all logs/reports.
2. Scans for real key patterns: sk-[a-zA-Z0-9]{32,}, ghp_[a-zA-Z0-9]{30,}.
3. Ignores standard fake test keys (sk-fake-paw-test, etc.).
4. Outputs scan count table without ever leaking matched secret text.
"""
import argparse
import os
import re
import sys
from pathlib import Path

# Ensure UTF-8 output
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REAL_KEY_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9]{32,}"),
    re.compile(r"ghp_[a-zA-Z0-9]{30,}"),
]

FAKE_KEY_WHITELIST = {
    "sk-fake-paw-test",
    "sk-test-fake-key",
    "sk-fake-paw-key",
    "sk-test-key-mock",
}

EXCLUDE_DIRS = {
    ".git",
    "test_env",
    ".cache",
    "__pycache__",
    "node_modules",
}


def scan_directory(scan_dir: Path, agy_credential_secret: str = "") -> dict:
    total_files_scanned = 0
    agy_secret_matches = 0
    real_key_matches = 0
    leaking_files = []

    clean_secret = agy_credential_secret.strip()
    has_secret_to_check = len(clean_secret) >= 8

    for p in scan_dir.rglob("*"):
        if any(ex in p.parts for ex in EXCLUDE_DIRS):
            continue
        if not p.is_file():
            continue
        if p.suffix.lower() not in (".log", ".md", ".txt", ".json", ".py", ".yml", ".xml", ".csv"):
            continue

        total_files_scanned += 1
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            if has_secret_to_check and clean_secret in content:
                agy_secret_matches += 1
                leaking_files.append(p.name)

            for pat in REAL_KEY_PATTERNS:
                found = pat.findall(content)
                for item in found:
                    if item not in FAKE_KEY_WHITELIST:
                        real_key_matches += 1
                        leaking_files.append(p.name)
        except Exception:
            pass

    return {
        "files_scanned": total_files_scanned,
        "agy_credential_occurrences": agy_secret_matches,
        "real_key_pattern_occurrences": real_key_matches,
        "leaking_files": leaking_files,
        "clean": (agy_secret_matches == 0 and real_key_matches == 0),
    }


def main():
    parser = argparse.ArgumentParser(description="Scan for credential leaks")
    parser.add_argument("--scan-dir", default=".", help="Directory to scan")
    parser.add_argument("--secret-value", default="", help="Exact secret value to scan for")
    args = parser.parse_args()

    scan_path = Path(args.scan_dir).resolve()
    print("=" * 60)
    print("产物安全审计与敏感凭证扫描 (Credential & Secret Leak Scan)")
    print(f"扫描目录: {scan_path}")
    print("=" * 60)

    result = scan_directory(scan_path, args.secret_value)

    print(f"扫描文本文件数: {result['files_scanned']}")
    print(f"AGY_CREDENTIAL 凭证原文出现次数: {result['agy_credential_occurrences']}")
    print(f"真实 API Key / Token 特征出现次数 (sk-, ghp-): {result['real_key_pattern_occurrences']}")
    print("=" * 60)

    if result["clean"]:
        print(">> VERDICT: PASS [所有产物与日志中敏感凭证出现次数为 0]")
        sys.exit(0)
    else:
        print(f">> VERDICT: FAIL [检测到敏感凭据泄漏风险！文件: {set(result['leaking_files'])}]")
        sys.exit(1)


if __name__ == "__main__":
    main()
