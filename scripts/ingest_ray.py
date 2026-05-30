#!/usr/bin/env python3
"""Ingest RAY (Raha-automaattiyhdistys) 2000-2016 grants from the STEA-published
Excel into a new local `ray_grants` table, and backfill y_tunnus by exact
normalized name-match against our existing local y_tunnus pool (org_mapping).

Local SQLite only. Does not touch existing grant tables or Supabase.
"""
import sqlite3, openpyxl, re, sys, json

DB = "/home/eki/ralssi/data/funding.db"
XLSX = "/home/eki/ralssi/data/RAY-aineisto.xlsx"

def norm(s):
    if not s:
        return ""
    s = s.lower().strip()
    s = re.sub(r"\(.*?\)", "", s)            # drop parenthetical alt-names
    s = s.replace(".", " ").replace(",", " ").replace("-", " ")
    s = re.sub(r"\b(r\s*y|ry|rf|r\s*f|sr|s\s*r)\b", "", s)  # legal forms
    s = re.sub(r"[^a-zåäö0-9 ]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def main():
    con = sqlite3.connect(DB)
    cur = con.cursor()

    # local y_tunnus pool (all sources) keyed by normalized name
    pool = {}
    for nm, yt in cur.execute(
        "SELECT source_name, y_tunnus FROM org_mapping "
        "WHERE y_tunnus IS NOT NULL AND y_tunnus<>''"
    ):
        n = norm(nm)
        if n:
            pool.setdefault(n, yt)
    print(f"local y_tunnus pool: {len(pool)} normalized names")

    cur.executescript("""
        DROP TABLE IF EXISTS ray_grants;
        CREATE TABLE ray_grants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            jarjesto TEXT NOT NULL,
            y_tunnus TEXT,
            vuosi INTEGER NOT NULL,
            kayttotarkoitus TEXT,
            avustuslaji TEXT,
            alue TEXT,
            jarjestoluokka TEXT,
            alaryhma TEXT,
            toimintoluokka TEXT,
            haettu INTEGER DEFAULT 0,
            myonnetty INTEGER DEFAULT 0,
            yt_source TEXT          -- 'local-name' | NULL (pending agent)
        );
    """)

    wb = openpyxl.load_workbook(XLSX, read_only=True)
    rows = 0
    matched = 0
    unmatched_eur = {}  # raw name -> total granted eur (for agent phase)
    for sh in wb.sheetnames:
        ws = wb[sh]
        it = ws.iter_rows(values_only=True)
        hdr = list(next(it))
        idx = {h: i for i, h in enumerate(hdr)}
        def g(r, key):
            i = idx.get(key)
            return r[i] if i is not None and i < len(r) else None
        for r in it:
            if not r or not r[0]:
                continue
            name = str(r[0]).strip()
            myon = g(r, "Myönnetty") or 0
            myon = int(myon) if isinstance(myon, (int, float)) else 0
            haet = g(r, "Haettu") or 0
            haet = int(haet) if isinstance(haet, (int, float)) else 0
            yt = pool.get(norm(name))
            cur.execute(
                "INSERT INTO ray_grants(jarjesto,y_tunnus,vuosi,kayttotarkoitus,"
                "avustuslaji,alue,jarjestoluokka,alaryhma,toimintoluokka,haettu,"
                "myonnetty,yt_source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (name, yt, int(g(r, "Vuosi")), g(r, "Käyttötarkoitus"),
                 g(r, "Av.laji"), g(r, "Maant.alue"), g(r, "Toimiala"),
                 g(r, "Alaryhmä"), g(r, "Toimintoluokka"), haet, myon,
                 "local-name" if yt else None),
            )
            rows += 1
            if yt:
                matched += 1
            elif myon > 0:
                unmatched_eur[name] = unmatched_eur.get(name, 0) + myon

    con.commit()
    con.execute("CREATE INDEX idx_ray_jarjesto ON ray_grants(jarjesto)")
    con.execute("CREATE INDEX idx_ray_ytunnus ON ray_grants(y_tunnus)")
    con.commit()

    # stats
    g_rows = cur.execute("SELECT COUNT(*) FROM ray_grants WHERE myonnetty>0").fetchone()[0]
    g_eur = cur.execute("SELECT COALESCE(SUM(myonnetty),0) FROM ray_grants").fetchone()[0]
    g_eur_yt = cur.execute("SELECT COALESCE(SUM(myonnetty),0) FROM ray_grants WHERE y_tunnus IS NOT NULL").fetchone()[0]
    print(f"inserted rows: {rows}  (granted>0: {g_rows})")
    print(f"y_tunnus via local-name: {matched} rows")
    print(f"granted eur total: {g_eur/1e9:.2f} mrd | with y_tunnus: {g_eur_yt/1e9:.2f} mrd ({100*g_eur_yt/g_eur:.0f}%)")
    print(f"unmatched distinct granted orgs: {len(unmatched_eur)}")

    # dump top unmatched by eur for the agent workflow
    top = sorted(unmatched_eur.items(), key=lambda x: -x[1])
    with open("/tmp/ray_unmatched.json", "w") as f:
        json.dump([{"name": n, "eur": e} for n, e in top], f, ensure_ascii=False)
    print(f"wrote /tmp/ray_unmatched.json ({len(top)} orgs, "
          f"{sum(e for _,e in top)/1e9:.2f} mrd unmatched)")

if __name__ == "__main__":
    main()
