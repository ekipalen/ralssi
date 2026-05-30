#!/usr/bin/env python3
"""Final y_tunnus application to ray_grants, combining:
  - candidate decisions: y_tunnus from our verified pool, PRH used only to reject
    a positive contradiction (id exists in trade register with a different name).
  - known decisions: AUTHORITATIVE ids from PRH name search
    (/tmp/ray_known_resolved.json); agent-recalled digits are discarded.

Local SQLite only. No Supabase, no push.
"""
import sqlite3, json, re, time, urllib.request, urllib.parse, sys

DB = "/home/eki/ralssi/data/funding.db"
PRH = "https://avoindata.prh.fi/opendata-ytj-api/v3/companies?businessId="
STOP = {"ry","rf","sr","säätiö","saatio","liitto","yhdistys","suomen","ja",
        "förening","r","y","f","keskusliitto","keskus","the","of"}
VALID = re.compile(r"^\d{7}-\d$")

def toks(s):
    s = (s or "").lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^a-zåäö0-9 ]", " ", s)
    return set(t for t in s.split() if len(t) > 2 and t not in STOP)

def prh_name(yt, cache):
    if yt in cache:
        return cache[yt]
    try:
        with urllib.request.urlopen(PRH + urllib.parse.quote(yt), timeout=20) as r:
            d = json.load(r)
        names = []
        for c in (d.get("companies") or []):
            for n in (c.get("names") or []):
                if n.get("name"):
                    names.append(n["name"])
        cache[yt] = " | ".join(names) if names else None
    except Exception:
        cache[yt] = None
    time.sleep(0.12)
    return cache[yt]

def main(apply=False):
    hits = json.load(open("/tmp/ray_hits.json"))
    known_res = {r["ray_name"]: r for r in json.load(open("/tmp/ray_known_resolved.json"))}
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cache = {}
    final = {}   # ray_name -> (ytunnus, source)

    # candidates
    for h in hits:
        if h["decision"] != "candidate":
            continue
        yt = (h.get("ytunnus") or "").strip()
        if not VALID.match(yt):
            continue
        pn = prh_name(yt, cache)
        if pn and not (toks(pn) & (toks(h["ray_name"]) | toks(h.get("current_name","")))):
            continue  # PRH contradiction -> skip
        if h.get("confidence") in ("high", "medium"):
            final[h["ray_name"]] = (yt, "agent-candidate")

    # known -> authoritative PRH ids
    for nm, r in known_res.items():
        if VALID.match(r["ytunnus"]):
            final[nm] = (r["ytunnus"], "prh-name-search")

    # euro covered
    eur = 0
    for nm in final:
        eur += cur.execute("SELECT COALESCE(SUM(myonnetty),0) FROM ray_grants WHERE jarjesto=?", (nm,)).fetchone()[0]
    print(f"final y_tunnus assignments: {len(final)} orgs, {eur/1e9:.2f} mrd granted")

    if apply:
        for nm, (yt, src) in final.items():
            cur.execute(
                "UPDATE ray_grants SET y_tunnus=?, yt_source=? "
                "WHERE jarjesto=? AND (y_tunnus IS NULL OR y_tunnus='')",
                (yt, src, nm),
            )
        con.commit()
        tot = cur.execute("SELECT COALESCE(SUM(myonnetty),0) FROM ray_grants").fetchone()[0]
        ytt = cur.execute("SELECT COALESCE(SUM(myonnetty),0) FROM ray_grants WHERE y_tunnus IS NOT NULL AND y_tunnus<>''").fetchone()[0]
        rows_tot = cur.execute("SELECT COUNT(*) FROM ray_grants WHERE myonnetty>0").fetchone()[0]
        rows_yt = cur.execute("SELECT COUNT(*) FROM ray_grants WHERE myonnetty>0 AND y_tunnus IS NOT NULL AND y_tunnus<>''").fetchone()[0]
        print(f"APPLIED.")
        print(f"  euro coverage:  {ytt/1e9:.2f} / {tot/1e9:.2f} mrd ({100*ytt/tot:.0f}%)")
        print(f"  granted rows:   {rows_yt} / {rows_tot} ({100*rows_yt/rows_tot:.0f}%)")
        for src, in [("local-name",),("agent-candidate",),("prh-name-search",)]:
            c = cur.execute("SELECT COUNT(DISTINCT jarjesto) FROM ray_grants WHERE yt_source=?", (src,)).fetchone()[0]
            print(f"  source {src:18}: {c} orgs")

if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
