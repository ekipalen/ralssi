#!/usr/bin/env python3
"""Generate 384-dim embeddings for eura_all using OpenAI text-embedding-3-small.
Mirrors embed_va.py; EURA = EU structural fund projects (hankekoodi is the PK,
a text id, not an int).

Text template follows the project convention documented in NEW_SOURCE_GUIDE.md
("org | grantor | purpose[:300] | call_name[:100]", adapted to EURA's fields):
toteuttaja (implementer) | viranomainen (funding authority) | nimi (title) |
tiivistelma[:300] (abstract). Older 2014-2020 projects have no tiivistelma, so
they fall back to toteuttaja | viranomainen | nimi only (see AGENTS.md coverage
note: "vanhemmat hankkeet indeksoitu nimella").

Incremental by default: rows already present in eura_embedding_ids.json are
skipped, new ones are appended to the existing .npy/.json (order preserved,
old vectors untouched). Pass --full to recompute everything from scratch
(Kaikki EURA-vektorit laskettiin uudelleen --full-ajolla 26.9.2026: vanhat
toukokuun vektorit eivät vastanneet hankkeiden tekstejä lainkaan, ja semanttinen haku
antoi EURA:sta satunnaisia tuloksia. Kustannus koko kannalle noin 0,05 USD.)
"""

import json
import os
import sqlite3
import sys
import time

import numpy as np
from openai import OpenAI

from _openai_key import load_api_key

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)
DB_PATH = os.path.join(ROOT, "data", "funding.db")
OUT_NPY = os.path.join(ROOT, "data", "eura_embeddings.npy")
OUT_IDS = os.path.join(ROOT, "data", "eura_embedding_ids.json")

BATCH_SIZE = 500
DIMENSIONS = 384


def build_text(row):
    parts = [row["toteuttaja"], row["viranomainen"], row["nimi"]]
    if row["tiivistelma"]:
        parts.append(row["tiivistelma"][:300])
    return " | ".join(p for p in parts if p)


def main():
    api_key = load_api_key()
    client = OpenAI(api_key=api_key)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    grants = [dict(r) for r in conn.execute(
        "SELECT hankekoodi, toteuttaja, viranomainen, nimi, tiivistelma "
        "FROM eura_all ORDER BY hankekoodi"
    ).fetchall()]
    conn.close()

    full = "--full" in sys.argv
    existing_ids = []
    existing_arr = None
    if not full and os.path.exists(OUT_IDS) and os.path.exists(OUT_NPY):
        with open(OUT_IDS) as f:
            existing_ids = json.load(f)
        existing_arr = np.load(OUT_NPY)
        print(f"Found {len(existing_ids)} existing embeddings, appending only missing rows (--full to regenerate all).")

    existing_set = set(existing_ids)
    todo = [g for g in grants if g["hankekoodi"] not in existing_set]


    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
        todo = todo[:limit]

    total = len(todo)
    print(f"Generating embeddings for {total} projects...")
    if total == 0:
        print("Nothing to do.")
        return

    ids = [g["hankekoodi"] for g in todo]
    texts = [build_text(g) for g in todo]
    all_embeddings = []

    for i in range(0, total, BATCH_SIZE):
        batch_texts = texts[i : i + BATCH_SIZE]

        for attempt in range(3):
            try:
                resp = client.embeddings.create(
                    model="text-embedding-3-small",
                    input=batch_texts,
                    dimensions=DIMENSIONS,
                )
                batch_emb = [item.embedding for item in resp.data]
                all_embeddings.extend(batch_emb)
                break
            except Exception as e:
                if attempt == 2:
                    print(f"  FAILED batch {i}: {e}", file=sys.stderr)
                    all_embeddings.extend([[0.0] * DIMENSIONS] * len(batch_texts))
                else:
                    time.sleep(2 ** attempt)

        done = min(i + BATCH_SIZE, total)
        print(f"  {done}/{total}")

    new_arr = np.array(all_embeddings, dtype=np.float32)
    if existing_arr is not None:
        arr = np.concatenate([existing_arr, new_arr], axis=0)
        ids = existing_ids + ids
    else:
        arr = new_arr

    np.save(OUT_NPY, arr)
    with open(OUT_IDS, "w") as f:
        json.dump(ids, f)

    print(f"\nDone. Shape: {arr.shape} ({len(new_arr)} new)")
    print(f"  {OUT_NPY}")
    print(f"  {OUT_IDS}")


if __name__ == "__main__":
    main()
