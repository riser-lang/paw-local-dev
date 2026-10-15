import sqlite3

conn = sqlite3.connect("data/workspace.db")
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
tables = [r[0] for r in cur.fetchall()]

print(f"{'Table':<35} {'Row Count':<10}")
print("-" * 50)
for t in sorted(tables):
    try:
        cnt = conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
        print(f"{t:<35} {cnt:<10}")
    except Exception as e:
        print(f"{t:<35} ERROR: {e}")
conn.close()
