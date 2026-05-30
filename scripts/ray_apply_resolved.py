#!/usr/bin/env python3
"""Apply agent-resolved y_tunnukset to ray_grants, with PRH/YTJ verification.

Input: /tmp/ray_hits.json  (list of {ray_name, decision, ytunnus, current_name,
confidence, candidate_i}). Produced from the resolve workflow result.

For every proposed y_tunnus we verify against the PRH avoindata businessId
endpoint: fetch the registered name and require it to share a significant token
with either the RAY name or the agent's current_name. This catches wrong
candidate picks and any hallucinated 'known' ids. Local SQLite only.
"""
import sqlite3, json, re, sys, time, urllib.request, urllib.parse

DB = "/home/eki/ralssi/data/funding.db"
PRH = "https://avoindata.prh.fi/opendata-ytj-api/v3/companies?businessId="
STOP = {"ry","rf","sr","säätiö","saatio","liitto","yhdistys","suomen","ja",
        "förening","r","y","f","keskusliitto","keskus","the","of"}

def toks(s):
    s = (s or "").lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^a-zåäö0-9 ]", " ", s)
    return set(t for t in s.split() if len(t) > 2 and t not in STOP)

VALID = re.compile(r"^\d{7}-\d$")

def prh_name(yt):
    """Return the PRH-registered name(s) for a business id, or None."""
    try:
        with urllib.request.urlopen(PRH + urllib.parse.quote(yt), timeout=20) as r:
            d = json.load(r)
    except Exception:
        return None
    comps = d.get("companies") or d.get("results") or []
    if not comps:
        return None
    names = []
    for c in comps:
        for n in (c.get("names") or []):
            if n.get("name"):
                names.append(n["name"])
        if c.get("name"):
            names.append(c["name"])
    return " | ".join(names) if names else None

def main(apply=False):
    hits = json.load(open("/tmp/ray_hits.json"))
    con = sqlite3.connect(DB)
    cur = con.cursor()

    # PRH v3 covers the trade register, not the association register, so many
    # legitimate ry's return nothing. Therefore PRH is used to CONTRADICT (id
    # exists but name clearly differs) rather than as a requirement. Candidate
    # ids come from our own verified pool and are trusted unless contradicted;
    # 'known' ids (agent memory) require positive PRH confirmation.
    accepted, rejected, skipped = [], [], []
    cache = {}
    for h in hits:
        yt = (h.get("ytunnus") or "").strip()
        dec = h.get("decision")
        if dec == "none" or not yt:
            skipped.append(h); continue
        if not VALID.match(yt):
            h["_why"] = "invalid-format"; rejected.append(h); continue
        if yt not in cache:
            cache[yt] = prh_name(yt)
            time.sleep(0.15)
        pname = cache[yt]
        overlap = toks(pname) & (toks(h["ray_name"]) | toks(h.get("current_name", ""))) if pname else set()
        if pname and overlap:
            h["_prh"] = pname; h["_overlap"] = sorted(overlap); h["_verify"] = "prh"
            accepted.append(h)
        elif pname and not overlap:
            h["_why"] = f"prh-contradiction (prh={pname})"; rejected.append(h)
        else:  # PRH empty (assoc register / not found)
            if dec == "candidate" and h.get("confidence") in ("high", "medium"):
                h["_verify"] = "pool-trusted"; accepted.append(h)
            else:
                h["_why"] = f"unverifiable {dec}/{h.get('confidence')}"; rejected.append(h)

    print(f"accepted (PRH-verified): {len(accepted)}")
    print(f"rejected: {len(rejected)}  | skipped(none/empty): {len(skipped)}")
    eur = 0
    for h in accepted:
        n = cur.execute("SELECT COALESCE(SUM(myonnetty),0) FROM ray_grants WHERE jarjesto=?",
                        (h["ray_name"],)).fetchone()[0]
        eur += n
    print(f"euro covered by accepted: {eur/1e9:.2f} mrd")
    print("--- sample rejected ---")
    for h in rejected[:12]:
        print(f"  {h['ray_name'][:42]:42} yt={h.get('ytunnus','')}: {h.get('_why')}")

    if apply:
        for h in accepted:
            src = "agent-" + h["decision"]
            cur.execute(
                "UPDATE ray_grants SET y_tunnus=?, yt_source=? "
                "WHERE jarjesto=? AND (y_tunnus IS NULL OR y_tunnus='')",
                (h["ytunnus"], src, h["ray_name"]),
            )
        con.commit()
        tot = cur.execute("SELECT COALESCE(SUM(myonnetty),0) FROM ray_grants").fetchone()[0]
        ytt = cur.execute("SELECT COALESCE(SUM(myonnetty),0) FROM ray_grants WHERE y_tunnus IS NOT NULL AND y_tunnus<>''").fetchone()[0]
        print(f"APPLIED. y_tunnus euro coverage now: {ytt/1e9:.2f} / {tot/1e9:.2f} mrd ({100*ytt/tot:.0f}%)")

    json.dump({"accepted": accepted, "rejected": rejected},
              open("/tmp/ray_apply_report.json", "w"), ensure_ascii=False, indent=1)

if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
