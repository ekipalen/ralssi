#!/usr/bin/env python3
"""Enrich va_grants with oneliner, tags, concreteness using GPT-4.1-nano."""

import json
import os
import sqlite3
import sys
import time

from openai import OpenAI

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)
DB_PATH = os.path.join(ROOT, "data", "funding.db")

SECRETS_PATH = os.path.expanduser("~/.config/voice-bot/secrets.env")

def load_api_key():
    with open(SECRETS_PATH) as f:
        for line in f:
            if line.startswith("OPENAI_REALTIME_KEY="):
                return line.split("=", 1)[1].strip()
    raise RuntimeError("OPENAI_REALTIME_KEY not found")


SYSTEM_PROMPT = """\
You enrich Finnish public funding grant records. For each grant, produce:
1. oneliner: A short (max 15 words) Finnish summary of what the grant funds.
2. tags: 2-4 lowercase Finnish tags as a JSON array. Use consistent tags like: "tutkimus", "koulutus", "kulttuuri", "urheilu", "nuorisotyö", "mielenterveys", "ympäristö", "kehitysyhteistyö", "tasa-arvo", "maahanmuutto", "terveys", "vertaistuki", "puoluetuki", "taide", "liikunta", "vammaistyö", "ihmisoikeudet", "saamelaistuki", "rauhanjärjestö", "elokuva", "media", "kotoutuminen", "asuminen".
3. concreteness: Integer 1-5 (1=abstract/general, 5=very concrete/specific action).

Reply with valid JSON only: {"oneliner": "...", "tags": [...], "concreteness": N}"""

BATCH_SIZE = 20


def build_user_prompt(grants):
    lines = []
    for g in grants:
        lines.append(
            f"ID={g['id']} | {g['organisation']} | {g['grantor']} | "
            f"{g['granted_eur']:.0f}€ | {g['purpose'][:200]}"
        )
    return (
        "Enrich these grants. Reply with a JSON array of objects, one per grant, "
        "in the same order. Each object: {\"id\": N, \"oneliner\": \"...\", \"tags\": [...], \"concreteness\": N}\n\n"
        + "\n".join(lines)
    )


def main():
    api_key = load_api_key()
    client = OpenAI(api_key=api_key)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("DROP TABLE IF EXISTS va_enrichments")
    cur.execute("""
        CREATE TABLE va_enrichments (
            grant_id INTEGER PRIMARY KEY,
            oneliner TEXT,
            tags TEXT,
            concreteness INTEGER
        )
    """)
    conn.commit()

    grants = [dict(r) for r in conn.execute(
        "SELECT id, organisation, grantor, granted_eur, purpose FROM va_grants"
    ).fetchall()]

    total = len(grants)
    done = 0
    errors = 0

    print(f"Enriching {total} grants in batches of {BATCH_SIZE}...")

    for i in range(0, total, BATCH_SIZE):
        batch = grants[i : i + BATCH_SIZE]
        prompt = build_user_prompt(batch)

        for attempt in range(3):
            try:
                resp = client.chat.completions.create(
                    model="gpt-4.1-nano",
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.2,
                    max_tokens=4000,
                )
                text = resp.choices[0].message.content.strip()
                if text.startswith("```"):
                    text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                results = json.loads(text)
                break
            except Exception as e:
                if attempt == 2:
                    print(f"  FAILED batch {i}-{i+len(batch)}: {e}")
                    errors += len(batch)
                    results = []
                else:
                    time.sleep(2 ** attempt)

        for r in results:
            try:
                cur.execute(
                    "INSERT OR REPLACE INTO va_enrichments (grant_id, oneliner, tags, concreteness) VALUES (?, ?, ?, ?)",
                    (r["id"], r.get("oneliner", ""), json.dumps(r.get("tags", []), ensure_ascii=False), r.get("concreteness", 3)),
                )
                done += 1
            except Exception as e:
                errors += 1

        if (i + BATCH_SIZE) % 200 == 0 or i + BATCH_SIZE >= total:
            conn.commit()
            print(f"  {min(i + BATCH_SIZE, total)}/{total} ({done} ok, {errors} errors)")

    conn.commit()
    conn.close()

    print(f"\nDone. Enriched: {done}, Errors: {errors}")


if __name__ == "__main__":
    main()
