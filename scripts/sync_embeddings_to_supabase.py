#!/usr/bin/env python3
"""Synkkaa embedding-vektorit paikallisista .npy-tiedostoista Supabasen grant_embeddings-tauluun.

Sivuston semanttinen haku lukee vektorit Supabasesta. Taulua ei korvata kokonaan
(HNSW-indeksin uudelleenrakennus), vaan kullekin lähteelle:
  - upsert jokainen paikallinen vektori (INSERT ... ON CONFLICT DO UPDATE)
  - poista Supabasesta ne rivit, joita paikallisesti ei enää ole
Kaikki lähteet yhdessä transaktiossa. Oletuksena kuivaharjoitus; kirjoitus --apply.

  uv run --with psycopg2-binary --with numpy scripts/sync_embeddings_to_supabase.py va eura fts
  ... --apply

Ota ensin varmuuskopio: avustusdata/scripts/backup-supabase.sh
"""
import argparse
import json
import os
import sys

import numpy as np
import psycopg2
import psycopg2.extras

sys.path.insert(0, "/home/eki/avustusdata")
from migrate_to_supabase import PG_DSN  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BATCH = 500


def load(source):
    E = np.load(os.path.join(ROOT, "data", f"{source}_embeddings.npy"))
    ids = [str(i) for i in json.load(open(os.path.join(ROOT, "data", f"{source}_embedding_ids.json")))]
    if len(ids) != len(E):
        sys.exit(f"{source}: id-lista ({len(ids)}) ja vektorit ({len(E)}) eri pituisia — pysähdytään")
    if len(set(ids)) != len(ids):
        sys.exit(f"{source}: id-listassa duplikaatteja — pysähdytään")
    return ids, E


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sources", nargs="+")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    pg = psycopg2.connect(PG_DSN.replace(":6543/", ":5432/"))
    pg.autocommit = False
    cur = pg.cursor()
    data = {}
    for s in a.sources:
        ids, E = load(s)
        cur.execute("select grant_id from grant_embeddings where source=%s", (s,))
        remote = {r[0] for r in cur.fetchall()}
        local = set(ids)
        print(f"{s:6} paikallinen {len(local):>6}  Supabase {len(remote):>6}  "
              f"uusia {len(local - remote):>5}  päivitettäviä {len(local & remote):>6}  "
              f"poistettavia {len(remote - local):>5}  ulottuvuus {E.shape[1]}")
        data[s] = (ids, E, remote - local)

    if not a.apply:
        print("\nKuivaharjoitus — mitään ei kirjoitettu. Aja uudelleen lipulla --apply.")
        return
    try:
        for s, (ids, E, stale) in data.items():
            rows = [(s, gid, "[" + ",".join(f"{x:.6f}" for x in vec) + "]") for gid, vec in zip(ids, E)]
            for i in range(0, len(rows), BATCH):
                psycopg2.extras.execute_values(
                    cur,
                    "insert into grant_embeddings (source, grant_id, embedding) values %s "
                    "on conflict (source, grant_id) do update set embedding = excluded.embedding",
                    rows[i:i + BATCH], template="(%s, %s, %s::vector)", page_size=BATCH)
            if stale:
                cur.execute("delete from grant_embeddings where source=%s and grant_id = any(%s)", (s, list(stale)))
            cur.execute("select count(*) from grant_embeddings where source=%s", (s,))
            got = cur.fetchone()[0]
            if got != len(ids):
                raise RuntimeError(f"{s}: odotettiin {len(ids)}, Supabasessa {got}")
            print(f"  {s}: {got} vektoria ok")
        pg.commit()
        print("COMMIT — vektorit päivitetty.")
    except Exception as e:
        pg.rollback()
        sys.exit(f"ROLLBACK — mitään ei muutettu: {e}")


if __name__ == "__main__":
    main()
