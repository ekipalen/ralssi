#!/usr/bin/env python3
"""Upload RAY enrichments + embeddings to Supabase (idempotent).

- Creates ray_enrichments (read-only RLS) and copies rows from local SQLite.
- Inserts ray embeddings into grant_embeddings (source='ray'), ON CONFLICT DO
  NOTHING (re-embedding requires deleting source='ray' rows first).

Run: uv run --with psycopg2-binary --with numpy python scripts/upload_ray_to_supabase.py
"""
import json
import os
import sqlite3

import numpy as np
import psycopg2
import psycopg2.extras

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "funding.db")
NPY = os.path.join(ROOT, "data", "ray_embeddings.npy")
IDS = os.path.join(ROOT, "data", "ray_embedding_ids.json")
CREDS = "/home/eki/.config/avustusdata/credentials.env"
BATCH = 500


def pg_dsn():
    for line in open(CREDS):
        if line.startswith("PG_DSN="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("PG_DSN not found")


def main():
    pg = psycopg2.connect(pg_dsn())
    cur = pg.cursor()

    # 1. ray_enrichments table (read-only), like va_enrichments
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ray_enrichments (
            grant_id integer PRIMARY KEY,
            oneliner text,
            tags text,
            concreteness integer
        );
    """)
    cur.execute("ALTER TABLE ray_enrichments ENABLE ROW LEVEL SECURITY;")
    cur.execute("DROP POLICY IF EXISTS public_read_ray_enrichments ON ray_enrichments;")
    cur.execute("CREATE POLICY public_read_ray_enrichments ON ray_enrichments FOR SELECT USING (true);")
    cur.execute("GRANT SELECT ON ray_enrichments TO anon, authenticated;")
    pg.commit()

    # 2. copy enrichments from SQLite
    sl = sqlite3.connect(DB)
    rows = sl.execute("SELECT grant_id, oneliner, tags, concreteness FROM ray_enrichments").fetchall()
    psycopg2.extras.execute_batch(
        cur,
        "INSERT INTO ray_enrichments (grant_id, oneliner, tags, concreteness) "
        "VALUES (%s,%s,%s,%s) ON CONFLICT (grant_id) DO UPDATE SET "
        "oneliner=EXCLUDED.oneliner, tags=EXCLUDED.tags, concreteness=EXCLUDED.concreteness",
        rows, page_size=BATCH,
    )
    pg.commit()
    print(f"ray_enrichments: {len(rows)} rows uploaded")

    # 3. embeddings -> grant_embeddings (source='ray')
    if os.path.exists(NPY):
        emb = np.load(NPY)
        ids = json.load(open(IDS))
        insert = ("INSERT INTO grant_embeddings (source, grant_id, embedding) "
                  "VALUES (%s,%s,%s) ON CONFLICT DO NOTHING")
        data = []
        inserted = 0
        for i, gid in enumerate(ids):
            vec = "[" + ",".join(f"{x:.6f}" for x in emb[i]) + "]"
            data.append(("ray", str(gid), vec))
            if len(data) >= BATCH:
                psycopg2.extras.execute_batch(cur, insert, data, page_size=BATCH)
                inserted += len(data); data = []
        if data:
            psycopg2.extras.execute_batch(cur, insert, data, page_size=BATCH)
            inserted += len(data)
        pg.commit()
        print(f"grant_embeddings (ray): {inserted} vectors uploaded")
    else:
        print(f"WARN: {NPY} not found — skipping embeddings")

    cur.execute("SELECT source, count(*) FROM grant_embeddings WHERE source='ray' GROUP BY source;")
    print("verify:", cur.fetchall())
    pg.close(); sl.close()


if __name__ == "__main__":
    main()
