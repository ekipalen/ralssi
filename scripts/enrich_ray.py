#!/usr/bin/env python3
"""Enrich ray_grants (granted rows only) with oneliner, tags, concreteness using
GPT-4.1-nano. Mirrors enrich_va.py but for RAY (STEA-era social/health grants).

Shard support for parallel runs: --shard N/M processes only grants where
id % M == N. Resume-safe (INSERT OR REPLACE, skips already-done ids), so shards
never collide and the job can be re-run.

  uv run scripts/enrich_ray.py                 # whole set, sequential
  uv run scripts/enrich_ray.py --shard 0/8     # one of 8 parallel workers
"""

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
WHERE = "myonnetty > 0"
BATCH_SIZE = 20


def load_api_key():
    with open(SECRETS_PATH) as f:
        for line in f:
            if line.startswith("OPENAI_REALTIME_KEY="):
                return line.split("=", 1)[1].strip()
    raise RuntimeError("OPENAI_REALTIME_KEY not found")


SYSTEM_PROMPT = """\
You enrich Finnish social- and health-sector grant records (RAY / Raha-automaattiyhdistys, the predecessor of STEA — grants to associations and foundations). For each grant, produce:
1. oneliner: A short (max 15 words) Finnish summary of what the grant funds.
2. tags: 2-4 lowercase Finnish tags as a JSON array. Use consistent tags like: "mielenterveys", "päihdetyö", "vammaistyö", "vanhustyö", "lastensuojelu", "nuorisotyö", "vertaistuki", "terveys", "kuntoutus", "asuminen", "työllisyys", "kriisiapu", "omaishoito", "liikunta", "kulttuuri", "kansalaistoiminta", "ennaltaehkäisy", "yhdenvertaisuus", "maahanmuutto", "perhetyö", "sosiaalipalvelut", "vapaaehtoistoiminta".
3. concreteness: Integer 1-5 (1=abstract/general, 5=very concrete/specific action).

Reply with valid JSON only."""


def build_user_prompt(grants):
    lines = []
    for g in grants:
        lines.append(
            f"ID={g['id']} | {g['jarjesto']} | {g['kayttotarkoitus']} | "
            f"{(g['myonnetty'] or 0):.0f}€ | {g['vuosi']}"
        )
    return (
        "Enrich these RAY grants to Finnish social/health organisations. Reply with a JSON array of objects, "
        "one per grant, in the same order. Each object: {\"id\": N, \"oneliner\": \"...\", \"tags\": [...], \"concreteness\": N}\n\n"
        + "\n".join(lines)
    )


def parse_shard():
    for i, a in enumerate(sys.argv):
        if a == "--shard" and i + 1 < len(sys.argv):
            n, m = sys.argv[i + 1].split("/")
            return int(n), int(m)
    return 0, 1


def main():
    shard_n, shard_m = parse_shard()
    api_key = load_api_key()
    client = OpenAI(api_key=api_key)

    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=120000")
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS ray_enrichments (
            grant_id INTEGER PRIMARY KEY,
            oneliner TEXT,
            tags TEXT,
            concreteness INTEGER
        )
    """)
    conn.commit()

    already_done = {r[0] for r in cur.execute("SELECT grant_id FROM ray_enrichments")}

    grants = [dict(r) for r in conn.execute(
        f"SELECT id, jarjesto, kayttotarkoitus, myonnetty, vuosi FROM ray_grants WHERE {WHERE} ORDER BY id"
    ).fetchall()]
    grants = [g for g in grants if g["id"] % shard_m == shard_n and g["id"] not in already_done]

    total = len(grants)
    done = errors = 0
    tag = f"[shard {shard_n}/{shard_m}] " if shard_m > 1 else ""
    print(f"{tag}Enriching {total} granted RAY grants in batches of {BATCH_SIZE}...")

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
                # tolerate a {"grants":[...]} or bare-array reply
                parsed = json.loads(text)
                results = parsed if isinstance(parsed, list) else next(
                    (v for v in parsed.values() if isinstance(v, list)), [])
                break
            except Exception as e:
                if attempt == 3:
                    print(f"  {tag}FAILED batch {i}-{i+len(batch)}: {e}")
                    errors += len(batch)
                else:
                    time.sleep(2 ** attempt)

        for r in results:
            try:
                cur.execute(
                    "INSERT OR REPLACE INTO ray_enrichments (grant_id, oneliner, tags, concreteness) VALUES (?, ?, ?, ?)",
                    (r["id"], r.get("oneliner", ""),
                     json.dumps(r.get("tags", []), ensure_ascii=False),
                     r.get("concreteness", 3)),
                )
                done += 1
            except Exception:
                errors += 1

        if (i + BATCH_SIZE) % 200 == 0 or i + BATCH_SIZE >= total:
            conn.commit()
            print(f"  {tag}{min(i + BATCH_SIZE, total)}/{total} ({done} ok, {errors} err)")

    conn.commit()
    conn.close()
    print(f"\n{tag}Done. Enriched: {done}, Errors: {errors}")


if __name__ == "__main__":
    main()
