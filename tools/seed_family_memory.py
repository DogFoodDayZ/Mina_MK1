"""Seed family facts for Travis directly into SQLite memory DB."""
import sqlite3
import json
import time
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

db = os.path.join(os.path.dirname(__file__), "..", "memory", "memory.db")
conn = sqlite3.connect(db)

cur = conn.cursor()
tags = json.dumps(["user_memory", "long_term", "family"])
now = time.time()

facts = [
    "Travis has a brother named Rodger. Rodger is a bail bondsman in Spokane Washington.",
    "Travis has a mom named Reichel. Reichel lives in Phoenix Arizona.",
    "Travis has a sister named Alisha. Alisha lives in Phoenix Arizona with their mom Reichel and her kids.",
    "Travis family summary: brother Rodger in Spokane WA (bail bondsman), mom Reichel in Phoenix AZ, sister Alisha in Phoenix AZ with Reichel and her kids.",
]

# Insert into both tables so keyword search finds them immediately,
# and embedding will promote them to FAISS when the server comes back up
for fact in facts:
    # memories table with NULL embeddings — keyword search hits this
    try:
        cur.execute(
            "INSERT INTO memories (text, kind, tags, timestamp, task_status, emb_small, dim_small, emb_base, dim_base) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (fact, "fact", tags, now, "stored", b"", 0, b"", 0),
        )
        print("Inserted to memories:", fact[:70])
    except Exception as e:
        print(f"memories insert failed ({e}), queuing pending instead")
        cur.execute(
            "INSERT INTO pending_memories (text, kind, tags, created_at, attempts, last_error) VALUES (?, ?, ?, ?, ?, ?)",
            (fact, "fact", tags, now, 0, None),
        )
        print("Queued to pending:", fact[:70])

conn.commit()
conn.close()
print("Done.")
