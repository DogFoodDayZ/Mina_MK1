"""One-shot: purge permanently-failed empty-text items from pending_memories."""
import sqlite3
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

if __name__ == "__main__":
    db = os.path.join(os.path.dirname(__file__), "..", "memory", "memory.db")
    conn = sqlite3.connect(db)
    cur = conn.cursor()

    cur.execute("SELECT id, text, last_error, attempts FROM pending_memories")
    rows = cur.fetchall()
    print(f"Pending items before: {len(rows)}")
    for row in rows:
        print(f"  id={row[0]} error={row[2]} attempts={row[3]} text={repr(str(row[1])[:60])}")

    cur.execute(
        "DELETE FROM pending_memories WHERE trim(coalesce(text,'')) = '' OR last_error = 'embed_empty_text'"
    )
    deleted = cur.rowcount
    conn.commit()

    cur.execute("SELECT COUNT(*) FROM pending_memories")
    remaining = cur.fetchone()[0]
    conn.close()

    print(f"Purged: {deleted} | Remaining: {remaining}")

conn = sqlite3.connect(db)
cur = conn.cursor()

cur.execute("SELECT id, text, last_error, attempts FROM pending_memories")
rows = cur.fetchall()
print(f"Pending items before: {len(rows)}")
for row in rows:
    print(f"  id={row[0]} error={row[2]} attempts={row[3]} text={repr(str(row[1])[:60])}")

cur.execute(
    "DELETE FROM pending_memories WHERE trim(coalesce(text,'')) = '' OR last_error = 'embed_empty_text'"
)
deleted = cur.rowcount
conn.commit()

cur.execute("SELECT COUNT(*) FROM pending_memories")
remaining = cur.fetchone()[0]
conn.close()

print(f"Purged: {deleted} | Remaining: {remaining}")
