import sqlite3
from pathlib import Path

src_conn = sqlite3.connect("data/workspace.db")
tpl_dir = Path("template")
tpl_dir.mkdir(exist_ok=True)
tpl_path = tpl_dir / "workspace_template.db"
if tpl_path.exists():
    tpl_path.unlink()

dst_conn = sqlite3.connect(tpl_path)

# Extract schema
schema_queries = []
for row in src_conn.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'retrieval_document_fts_%'"):
    schema_queries.append(row[0] + ";")

for row in src_conn.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND type in ('index', 'trigger', 'view')"):
    schema_queries.append(row[0] + ";")

dst_cur = dst_conn.cursor()
for q in schema_queries:
    try:
        dst_cur.execute(q)
    except Exception as e:
        print("Error executing DDL:", e, q[:60])

# Copy ONLY schema_meta
try:
    meta_rows = src_conn.execute("SELECT * FROM schema_meta").fetchall()
    for r in meta_rows:
        dst_cur.execute("INSERT INTO schema_meta VALUES (?, ?, ?)", r)
except Exception as e:
    print("schema_meta copy:", e)

dst_conn.commit()
dst_conn.execute("VACUUM")
print("Clean template DB created at:", tpl_path, "Size:", tpl_path.stat().st_size)

# Inspect table counts in clean template DB
cur = dst_conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
tables = [r[0] for r in cur.fetchall()]
print(f"Total tables: {len(tables)}")
non_zero = {}
for t in tables:
    cnt = dst_conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
    if cnt > 0:
        non_zero[t] = cnt

print("Non-zero tables in template DB:", non_zero)

src_conn.close()
dst_conn.close()
