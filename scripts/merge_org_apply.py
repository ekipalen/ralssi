"""
Apply a confirmed list of org_id merges to BOTH local SQLite and Supabase.

Input: JSON file [{"from": <old_org_id>, "to": <canonical_org_id>}, ...]
(e.g. the verified 'merge' set from the adversarial verification workflow).

Mirrors merge_org_by_ytunnus.py: backup, transactional SQLite update with a
PK-integrity check, then idempotent REST PATCH against Supabase. Safe to
re-run. Dry-run by default.

Usage:
  uv run scripts/merge_org_apply.py /tmp/confirmed_merges.json
  uv run scripts/merge_org_apply.py /tmp/confirmed_merges.json --apply
"""

import json
import shutil
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

DB_PATH = Path("/home/eki/ralssi/data/funding.db")
CREDS = Path("/home/eki/.config/avustusdata/credentials.env")


def load_creds():
    env = {}
    for line in CREDS.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def resolve_chains(pairs):
    """Collapse from->to chains so every 'from' points at a final canonical id."""
    nxt = {p["from"]: p["to"] for p in pairs}
    out = {}
    for frm in nxt:
        seen, cur = set(), frm
        while cur in nxt and cur not in seen:
            seen.add(cur)
            cur = nxt[cur]
        if cur != frm:
            out[frm] = cur
    return out


def main():
    if len(sys.argv) < 2:
        raise SystemExit("anna confirmed-merges JSON polku")
    apply = "--apply" in sys.argv
    pairs = json.load(open(sys.argv[1]))
    mapping = resolve_chains(pairs)

    print(f"vahvistettuja yhdistämisiä: {len(pairs)}  ->  org_id-remap: {len(mapping)}")
    for old, new in sorted(mapping.items()):
        print(f"  {old} -> {new}")

    if not mapping:
        return
    if not apply:
        print("\n[dry-run] aja --apply tehdäksesi muutokset SQLiteen + Supabaseen.")
        return

    # --- SQLite ---
    bak = DB_PATH.with_suffix(".db.bak-merge-name")
    shutil.copy2(DB_PATH, bak)
    print(f"\nVarmuuskopio: {bak}")
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    con.execute("BEGIN")
    for old, new in mapping.items():
        cur.execute("UPDATE org_mapping SET org_id=? WHERE org_id=?", (new, old))
    viol = cur.execute(
        "SELECT source, source_name, COUNT(*) c FROM org_mapping"
        " GROUP BY source, source_name HAVING c>1"
    ).fetchall()
    if viol:
        con.rollback()
        raise SystemExit(f"PK violation ({len(viol)}), rolled back.")
    con.commit()
    print(f"SQLite: {len(mapping)} org_id-ryhmää päivitetty.")

    # --- Supabase ---
    env = load_creds()
    base = env["SUPABASE_URL"].rstrip("/") + "/rest/v1"
    key = env["SUPABASE_SERVICE_ROLE_KEY"]
    ok = 0
    for old, new in mapping.items():
        req = urllib.request.Request(
            f"{base}/org_mapping?org_id=eq.{old}",
            data=json.dumps({"org_id": new}).encode(), method="PATCH",
        )
        req.add_header("apikey", key)
        req.add_header("Authorization", f"Bearer {key}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Prefer", "return=minimal")
        try:
            urllib.request.urlopen(req)
            ok += 1
        except urllib.error.HTTPError as e:
            print(f"  Supabase {old}->{new} FAILED: {e.code} {e.read().decode()[:200]}")
    print(f"Supabase: {ok}/{len(mapping)} päivitystä onnistui.")
    con.close()


if __name__ == "__main__":
    main()
