import sqlite3

conn = sqlite3.connect("template/workspace_template.db")
print("PRAGMA integrity_check:", conn.execute("PRAGMA integrity_check").fetchone()[0])
tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
print(f"Total tables: {len(tables)}")
print(f"{'Table Name':<35} {'Count':<10}")
print("-" * 48)
for t in sorted(tables):
    cnt = conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
    print(f"{t:<35} {cnt:<10}")

conn.close()
