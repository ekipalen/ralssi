#!/usr/bin/env python3
"""Generate 384-dim embeddings for fts_grants (third-sector only) using OpenAI text-embedding-3-small."""

import json
import os
import sqlite3
import sys
import time

import numpy as np
from openai import OpenAI

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)
DB_PATH = os.path.join(ROOT, "data", "funding.db")
OUT_NPY = os.path.join(ROOT, "data", "fts_embeddings.npy")
OUT_IDS = os.path.join(ROOT, "data", "fts_embedding_ids.json")

SECRETS_PATH = os.path.expanduser("~/.config/voice-bot/secrets.env")
BATCH_SIZE = 500
DIMENSIONS = 384

THIRD_SECTOR_FILTER = """
    (
        is_ngo = 1
        OR organisation LIKE '% RY%'
        OR organisation LIKE '% LIITTO%'
        OR organisation LIKE '%YHDISTYS%'
        OR organisation LIKE '% SEURA%'
        OR (
            (organisation LIKE '%SAATIO%' OR organisation LIKE '%SAEAETIOE%')
            AND organisation NOT LIKE '%KORKEAKOULU%'
            AND organisation NOT LIKE '%YLIOPISTO%'
            AND organisation NOT LIKE '%UNIVERSITY%'
            AND organisation NOT LIKE '%AMMATTIKORKEAKOULU%'
        )
    )
    AND organisation NOT LIKE '%VTT%'
    AND organisation NOT LIKE '%TEKNOLOGIAN TUTKIMUSKESKUS%'
    AND organisation NOT LIKE '%BUSINESS FINLAND%'
    AND organisation NOT LIKE '%CSC-TIETEEN%'
"""


def load_api_key():
    with open(SECRETS_PATH) as f:
        for line in f:
            if line.startswith("OPENAI_REALTIME_KEY="):
                return line.split("=", 1)[1].strip()
    raise RuntimeError("OPENAI_REALTIME_KEY not found")


def build_text(row):
    """Build embedding text: organisation | programme | (no subject in this dataset)"""
    parts = [row["organisation"], row["programme"]]
    return " | ".join(p for p in parts if p)


def main():
    api_key = load_api_key()
    client = OpenAI(api_key=api_key)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    grants = [dict(r) for r in conn.execute(
        f"SELECT id, organisation, programme FROM fts_grants WHERE {THIRD_SECTOR_FILTER} ORDER BY id"
    ).fetchall()]
    conn.close()

    total = len(grants)
    print(f"Generating embeddings for {total} third-sector grants...")

    ids = [g["id"] for g in grants]
    texts = [build_text(g) for g in grants]
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

    arr = np.array(all_embeddings, dtype=np.float32)
    np.save(OUT_NPY, arr)
    with open(OUT_IDS, "w") as f:
        json.dump(ids, f)

    print(f"\nDone. Shape: {arr.shape}")
    print(f"  {OUT_NPY}")
    print(f"  {OUT_IDS}")


if __name__ == "__main__":
    main()
