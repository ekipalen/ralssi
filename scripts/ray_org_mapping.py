#!/usr/bin/env python3
"""Create org_mapping rows for the RAY source (source='ray').

Links each distinct RAY org to an existing org_id when the same legal entity is
already in org_mapping — primarily by y_tunnus, falling back to exact normalized
name — so RAY grants count toward the same cross-source organisation. Orgs with
no match get a fresh org_id (RAY-only). Sector is inherited from the matched org.

Writes rows to local SQLite and dumps /tmp/ray_org_mapping.json for Supabase.
"""
import sqlite3, json, re

DB = "/home/eki/ralssi/data/funding.db"

def norm(s):
    s = (s or "").lower().strip()
    s = re.sub(r"\(.*?\)", " ", s)
    s = s.replace(".", " ").replace(",", " ").replace("-", " ")
    s = re.sub(r"\b(r\s*y|ry|rf|r\s*f|sr|s\s*r)\b", " ", s)
    s = re.sub(r"[^a-zåäö0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def main(apply=False):
    con = sqlite3.connect(DB)
    cur = con.cursor()

    # existing y_tunnus -> canonical org_id (the org_id with most rows for that yt)
    yt_rows = {}
    yt_sector = {}
    for oid, yt, sec in cur.execute(
        "SELECT org_id, y_tunnus, sector FROM org_mapping WHERE y_tunnus IS NOT NULL AND y_tunnus<>''"
    ):
        yt_rows.setdefault(yt, {})
        yt_rows[yt][oid] = yt_rows[yt].get(oid, 0) + 1
        if sec and yt not in yt_sector:
            yt_sector[yt] = sec
    yt_org = {yt: max(d, key=d.get) for yt, d in yt_rows.items()}

    # existing normalized name -> (org_id, sector, y_tunnus)
    name_org = {}
    for oid, nm, sec, yt in cur.execute(
        "SELECT org_id, source_name, sector, y_tunnus FROM org_mapping"
    ):
        n = norm(nm)
        if n:
            name_org.setdefault(n, (oid, sec, yt))

    next_id = cur.execute("SELECT MAX(org_id) FROM org_mapping").fetchone()[0] + 1

    # distinct RAY orgs with their (enriched) y_tunnus
    ray_orgs = cur.execute(
        "SELECT jarjesto, MAX(y_tunnus) FROM ray_grants GROUP BY jarjesto"
    ).fetchall()

    rows = []
    by_yt = by_name = fresh = 0
    for nm, yt in ray_orgs:
        yt = yt or None
        sector = None
        if yt and yt in yt_org:
            oid = yt_org[yt]; sector = yt_sector.get(yt); by_yt += 1
        else:
            n = norm(nm)
            if n in name_org:
                oid, sector, yt2 = name_org[n]
                if not yt:
                    yt = yt2
                by_name += 1
            else:
                oid = next_id; next_id += 1; fresh += 1
        rows.append((oid, "ray", nm, yt, "name", 0, sector))

    print(f"RAY orgs: {len(ray_orgs)} | linked by y_tunnus: {by_yt} | by name: {by_name} | fresh org_id: {fresh}")
    json.dump([
        {"org_id": r[0], "source": r[1], "source_name": r[2], "y_tunnus": r[3],
         "confidence": r[4], "is_category": bool(r[5]), "sector": r[6]}
        for r in rows
    ], open("/tmp/ray_org_mapping.json", "w"), ensure_ascii=False)
    print("wrote /tmp/ray_org_mapping.json")

    if apply:
        cur.executemany(
            "INSERT OR REPLACE INTO org_mapping(org_id,source,source_name,y_tunnus,confidence,is_category,sector) "
            "VALUES(?,?,?,?,?,?,?)", rows)
        con.commit()
        n = cur.execute("SELECT COUNT(*) FROM org_mapping WHERE source='ray'").fetchone()[0]
        print(f"SQLite: inserted {n} ray org_mapping rows.")

if __name__ == "__main__":
    import sys
    main(apply="--apply" in sys.argv)
