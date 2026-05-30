#!/usr/bin/env python3
"""Apply confirmed org merges treating pairs as UNDIRECTED edges (union-find).

merge_org_apply.py used directed from->to chains and silently DROPPED
bidirectional pairs (A->B and B->A) via cycle protection. The verification
workflow emits both directions for same-entity pairs, so most merges were lost.
This version builds connected components, picks one canonical org_id per
component, and remaps all other members — in SQLite and in Supabase (PATCH by
org_mapping PK (source, source_name), so it is idempotent and corrects any
partial earlier apply).

Input: JSON [{"from": id, "to": id, ...}, ...]. Dry-run unless --apply.
"""
import json, sys, urllib.request, urllib.error
from pathlib import Path

DB = Path("/home/eki/ralssi/data/funding.db")
CREDS = Path("/home/eki/.config/avustusdata/credentials.env")
import sqlite3

def load_creds():
    env = {}
    for line in CREDS.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env

class UF:
    def __init__(self): self.p = {}
    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]; x = self.p[x]
        return x
    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb: self.p[ra] = rb

def main(apply=False):
    pairs = json.load(open(sys.argv[1]))
    uf = UF()
    for p in pairs:
        uf.union(p["from"], p["to"])

    con = sqlite3.connect(DB)
    cur = con.cursor()

    # components
    comps = {}
    for x in list(uf.p.keys()):
        comps.setdefault(uf.find(x), set()).add(x)

    # canonical per component = member with most grant-bearing org_mapping rows,
    # tie-break smallest id (stable, deterministic).
    def rowcount(oid):
        return cur.execute("SELECT COUNT(*) FROM org_mapping WHERE org_id=?", (oid,)).fetchone()[0]

    remap = {}  # org_id -> canonical
    for root, members in comps.items():
        members = sorted(members)
        canon = max(members, key=lambda o: (rowcount(o), -o))
        for m in members:
            if m != canon:
                remap[m] = canon

    print(f"pairs: {len(pairs)} | components: {len(comps)} | org_ids remapped: {len(remap)}")
    # collect affected org_mapping rows (by PK) for Supabase
    affected = []  # (source, source_name, canon)
    for old, canon in remap.items():
        for src, nm in cur.execute("SELECT source, source_name FROM org_mapping WHERE org_id=?", (old,)):
            affected.append((src, nm, canon))
    print(f"org_mapping rows to update: {len(affected)}")

    if not apply:
        print("\n[dry-run] aja --apply.")
        for old, canon in list(remap.items())[:15]:
            print(f"  {old} -> {canon}")
        return

    # backup
    import shutil
    shutil.copy(DB, str(DB) + ".bak-merge-components")

    # SQLite
    for old, canon in remap.items():
        cur.execute("UPDATE org_mapping SET org_id=? WHERE org_id=?", (canon, old))
    con.commit()
    print(f"SQLite: {len(remap)} org_id remaps applied.")

    # Supabase: PATCH each affected row by PK
    env = load_creds()
    base = env["SUPABASE_URL"].rstrip("/") + "/rest/v1/org_mapping"
    key = env.get("SUPABASE_SERVICE_ROLE_KEY") or env.get("SERVICE_ROLE_KEY")
    ok = 0; fail = 0
    for src, nm, canon in affected:
        import urllib.parse
        q = f"?source=eq.{urllib.parse.quote(src)}&source_name=eq.{urllib.parse.quote(nm)}"
        body = json.dumps({"org_id": canon}).encode()
        req = urllib.request.Request(base + q, data=body, method="PATCH", headers={
            "apikey": key, "Authorization": f"Bearer {key}",
            "Content-Type": "application/json", "Prefer": "return=minimal",
        })
        try:
            urllib.request.urlopen(req, timeout=30); ok += 1
        except urllib.error.HTTPError as e:
            fail += 1
            if fail <= 5: print("  FAIL", src, nm[:30], e.code, e.read()[:120])
    print(f"Supabase: {ok} ok, {fail} fail")

if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
