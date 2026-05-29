"""
Merge org_ids in org_mapping that share the same Y-tunnus.

Root cause: dedup_org_mapping.py merges only by exact (case-insensitive)
source_name. Bilingual name variants ("ry - Flyktingrådgivningen rf",
"ry;Flyktingrådgivningen rf", …) stay split even when the Y-tunnus is
identical. This script unions org_ids by shared non-empty y_tunnus and
remaps every row of each component to the lowest org_id.

Applies the SAME old->new mapping to both local SQLite and Supabase
(REST PATCH with service-role key) so the two stay consistent.

Usage:
  uv run scripts/merge_org_by_ytunnus.py            # dry-run (no writes)
  uv run scripts/merge_org_by_ytunnus.py --apply     # write SQLite + Supabase
"""

import json
import shutil
import sqlite3
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

DB_PATH = Path("/home/eki/ralssi/data/funding.db")
CREDS = Path("/home/eki/.config/avustusdata/credentials.env")


def load_creds():
    env = {}
    for line in CREDS.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def find(parent, x):
    root = x
    while parent[root] != root:
        root = parent[root]
    while parent[x] != root:
        parent[x], x = root, parent[x]
    return root


def union(parent, a, b):
    ra, rb = find(parent, a), find(parent, b)
    if ra != rb:
        # keep the smaller id as the root
        lo, hi = (ra, rb) if ra < rb else (rb, ra)
        parent[hi] = lo


def compute_mapping(conn):
    """Return {old_org_id: new_org_id} for org_ids that must change."""
    rows = conn.execute(
        "SELECT org_id, y_tunnus FROM org_mapping"
        " WHERE y_tunnus IS NOT NULL AND TRIM(y_tunnus) != ''"
    ).fetchall()

    # group org_ids by y_tunnus
    by_yt = defaultdict(set)
    for org_id, yt in rows:
        by_yt[yt.strip()].add(org_id)

    # union-find across all org_ids
    parent = {}
    for org_id, _ in rows:
        parent.setdefault(org_id, org_id)
    for yt, ids in by_yt.items():
        ids = sorted(ids)
        for other in ids[1:]:
            union(parent, ids[0], other)

    mapping = {}
    for org_id in parent:
        root = find(parent, org_id)
        if root != org_id:
            mapping[org_id] = root
    return mapping, by_yt


def apply_sqlite(conn, mapping):
    cur = conn.cursor()
    conn.execute("BEGIN")
    for old, new in mapping.items():
        cur.execute("UPDATE org_mapping SET org_id = ? WHERE org_id = ?", (new, old))
    # PK is (source, source_name) so org_id remap cannot violate it, but verify.
    viol = cur.execute(
        "SELECT source, source_name, COUNT(*) c FROM org_mapping"
        " GROUP BY source, source_name HAVING c > 1"
    ).fetchall()
    if viol:
        conn.rollback()
        raise SystemExit(f"PK violation ({len(viol)}), rolled back.")
    conn.commit()


def supabase_patch_org_id(base, key, old, new):
    url = f"{base}/org_mapping?org_id=eq.{old}"
    body = json.dumps({"org_id": new}).encode()
    req = urllib.request.Request(url, data=body, method="PATCH")
    req.add_header("apikey", key)
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Prefer", "return=minimal")
    with urllib.request.urlopen(req) as resp:
        return resp.status


def main():
    apply = "--apply" in sys.argv
    conn = sqlite3.connect(DB_PATH)

    mapping, by_yt = compute_mapping(conn)
    split = {yt: ids for yt, ids in by_yt.items() if len(ids) > 1}

    print(f"Y-tunnuksia jotka jakautuvat >1 org_id:lle: {len(split)}")
    print(f"org_id-uudelleenkartoituksia (old->new):    {len(mapping)}\n")

    # detailed log
    for yt, ids in sorted(split.items()):
        ids = sorted(ids)
        canonical = min(find_canonical(mapping, i) for i in ids)
        print(f"  {yt}: {ids}  ->  {canonical}")

    if not mapping:
        print("\nEi mitään yhdistettävää.")
        return

    if not apply:
        print("\n[dry-run] Ei kirjoituksia. Aja --apply tehdäksesi muutokset.")
        return

    # --- SQLite ---
    bak = DB_PATH.with_suffix(f".db.bak-merge-ytunnus")
    shutil.copy2(DB_PATH, bak)
    print(f"\nVarmuuskopio: {bak}")
    apply_sqlite(conn, mapping)
    print(f"SQLite: {len(mapping)} org_id-riviryhmää päivitetty.")

    # --- Supabase ---
    env = load_creds()
    base = env["SUPABASE_URL"].rstrip("/") + "/rest/v1"
    key = env["SUPABASE_SERVICE_ROLE_KEY"]
    ok = 0
    for old, new in mapping.items():
        try:
            supabase_patch_org_id(base, key, old, new)
            ok += 1
        except urllib.error.HTTPError as e:
            print(f"  Supabase PATCH {old}->{new} FAILED: {e.code} {e.read().decode()[:200]}")
    print(f"Supabase: {ok}/{len(mapping)} org_id-päivitystä onnistui.")

    conn.close()


def find_canonical(mapping, x):
    """Resolve old->new chain (mapping only ever points to a final root)."""
    return mapping.get(x, x)


if __name__ == "__main__":
    main()
