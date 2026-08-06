import sys, sqlite3
sys.path.insert(0, ".")
db = "memory/memory.db"
conn = sqlite3.connect(db)
cur = conn.cursor()
cur.execute("""
    SELECT text, kind, tags FROM memories
    WHERE lower(text) LIKE '%brother%'
       OR lower(text) LIKE '%mom%'
       OR lower(text) LIKE '%mother%'
       OR lower(text) LIKE '%travis%'
    ORDER BY timestamp DESC LIMIT 10
""")
rows = cur.fetchall()
print(f"Family-related memories: {len(rows)}")
for r in rows:
    print(f"  [{r[1]}] {r[0][:100]}")
conn.close()
