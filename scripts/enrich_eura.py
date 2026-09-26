#!/usr/bin/env python3
"""Enrich eura_all with tags + concreteness using GPT-4.1-nano.

NOTE: eura_enrichments has NO oneliner column (unlike va/fts/stea/ray
enrichments) -- schema is (hankekoodi TEXT PRIMARY KEY, tags TEXT,
concreteness INTEGER). Existing rows use free-form Finnish snake_case topic
tags (not a fixed vocabulary), e.g. ["kestava_kehitys", "pk_yritykset",
"digitalisaatio"] -- this mirrors that style rather than va/fts's fixed tag
list.

Existing eura_enrichments only covers the 2021-2027 period (8091/8748 rows
before the 2026-09-26 import); the 2014-2020 period (11787 rows) has never
been enriched -- this is a pre-existing gap, out of scope here (only the
657 newly-imported 2021-2027 projects are processed).

Resume-safe (INSERT OR REPLACE, skips already-done hankekoodi), mirrors
enrich_ray.py's tolerant JSON parsing.
"""

import json
import os
import sqlite3
import sys
import time

from openai import OpenAI

from _openai_key import load_api_key

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)
DB_PATH = os.path.join(ROOT, "data", "funding.db")

BATCH_SIZE = 20

SYSTEM_PROMPT = """\
You enrich Finnish EU structural fund project records (EURA 2021-2027: EAKR/ESR+/JTF). For each project, produce:
1. tags: 2-5 lowercase Finnish topic tags as a JSON array, snake_case style (underscore-joined when a tag has multiple words), e.g. "kestava_kehitys", "pk_yritykset", "digitalisaatio", "tutkimus_kehitys", "tyollisyys", "kiertotalous", "koulutus", "innovaatiot". Pick tags that describe the actual topic/sector/target of THIS project (not generic EU-programme boilerplate).
2. concreteness: Integer 1-5 (1=abstract/general, 5=very concrete/specific action).

Reply with valid JSON only: {"hankekoodi": "...", "tags": [...], "concreteness": N}"""


def build_user_prompt(grants):
    lines = []
    for g in grants:
        summary = (g["tiivistelma"] or "")[:200]
        lines.append(
            f"hankekoodi={g['hankekoodi']} | {g['toteuttaja']} | {g['rahasto']} | "
            f"{g['nimi']} | {summary}"
        )
    return (
        "Enrich these EU structural fund projects. Reply with a JSON array of objects, one per project, "
        "in the same order. Each object: {\"hankekoodi\": \"...\", \"tags\": [...], \"concreteness\": N}\n\n"
        + "\n".join(lines)
    )


def main():
    api_key = load_api_key()
    client = OpenAI(api_key=api_key)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS eura_enrichments (
            hankekoodi TEXT PRIMARY KEY,
            tags TEXT,
            concreteness INTEGER
        )
    """)
    conn.commit()

    already_done = {r[0] for r in cur.execute("SELECT hankekoodi FROM eura_enrichments")}
    print(f"Resuming: {len(already_done)} already enriched")

    # Scope: 2021-2027 period only. The 2014-2020 period (11787 rows) has NEVER
    # been enriched -- a pre-existing gap, out of scope for "new rows" work.
    # Restricting to this period (rather than just "not already_done") avoids
    # silently sweeping that old gap in here too.
    grants = [dict(r) for r in conn.execute(
        "SELECT hankekoodi, toteuttaja, rahasto, nimi, tiivistelma FROM eura_all "
        "WHERE ohjelmakausi = '2021-2027' ORDER BY hankekoodi"
    ).fetchall()]
    grants = [g for g in grants if g["hankekoodi"] not in already_done]

    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
        grants = grants[:limit]

    total = len(grants)
    done = 0
    errors = 0

    print(f"Enriching {total} projects in batches of {BATCH_SIZE}...")

    for i in range(0, total, BATCH_SIZE):
        batch = grants[i : i + BATCH_SIZE]
        prompt = build_user_prompt(batch)

        results = []
        for attempt in range(4):
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
                parsed = json.loads(text)
                results = parsed if isinstance(parsed, list) else next(
                    (v for v in parsed.values() if isinstance(v, list)), [])
                break
            except Exception as e:
                if attempt == 3:
                    print(f"  FAILED batch {i}-{i+len(batch)}: {e}")
                    errors += len(batch)
                    results = []
                else:
                    time.sleep(2 ** attempt)

        for r in results:
            try:
                cur.execute(
                    "INSERT OR REPLACE INTO eura_enrichments (hankekoodi, tags, concreteness) VALUES (?, ?, ?)",
                    (r["hankekoodi"], json.dumps(r.get("tags", []), ensure_ascii=False), r.get("concreteness", 3)),
                )
                done += 1
            except Exception:
                errors += 1

        if (i + BATCH_SIZE) % 200 == 0 or i + BATCH_SIZE >= total:
            conn.commit()
            print(f"  {min(i + BATCH_SIZE, total)}/{total} ({done} ok, {errors} errors)")

    conn.commit()
    conn.close()

    print(f"\nDone. Enriched: {done}, Errors: {errors}")


if __name__ == "__main__":
    main()
