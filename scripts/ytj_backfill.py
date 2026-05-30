"""Backfill y_tunnus for no-y_tunnus org_ids via the PRH avoindata YTJ API.

PRH name search is FUZZY (name=Nokia returns 985 incl. unrelated), so we only
accept a result whose normalized registered name EXACTLY equals our normalized
org name. That keeps precision high; recall is whatever PRH happens to hold
(ry/säätiö are included, but only the open business register — many small ry's
are absent). Output is a candidate list for review, NOT auto-applied.

Usage:
  uv run scripts/ytj_backfill.py            # sample 60, show hits
  uv run scripts/ytj_backfill.py --limit 0  # all no-ytunnus orgs -> JSON
"""

import json
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict

DB = "/home/eki/ralssi/data/funding.db"
API = "https://avoindata.prh.fi/opendata-ytj-api/v3/companies"

# strip legal-form suffixes + bilingual tails so "Pakolaisneuvonta ry" matches
# the registry's "Pakolaisneuvonta r.y." etc.
SUFFIX = re.compile(
    r"\b(r\.?y\.?|r\.?f\.?|ry|rf|oyj|oy|ab|osk|sr|säätiö|stiftelse|stiftelsen|"
    r"foundation|förening| rf| ry)\b",
    re.IGNORECASE,
)


def fi_segment(name):
    """Bilingual source names pack FI/SV/EN into one string separated by
    '*' (fts), '/' or ',' (helsinki). The first segment is the Finnish name."""
    for sep in ("*", "/", ","):
        if sep in name:
            name = name.split(sep, 1)[0]
    return name.strip()


def norm(name):
    s = fi_segment(name).lower()
    s = re.sub(r"[-/–—]", " ", s)
    s = SUFFIX.sub(" ", s)
    s = re.sub(r"[^\wåäö ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def prh_search(name):
    q = urllib.parse.urlencode({"name": name, "maxResults": 30})
    req = urllib.request.Request(f"{API}?{q}", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except Exception as e:
        return {"_err": str(e)}


def best_match(our_name, data):
    target = norm(our_name)
    if not target:
        return None
    for c in data.get("companies", []):
        bid = (c.get("businessId") or {}).get("value")
        for n in c.get("names", []):
            if norm(n.get("name", "")) == target:
                return bid, n.get("name")
    return None


def main():
    limit = 60
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT org_id, source_name, y_tunnus FROM org_mapping WHERE is_category=0"
    ).fetchall()
    has_yt, names = defaultdict(bool), defaultdict(set)
    for oid, nm, yt in rows:
        if (yt or "").strip():
            has_yt[oid] = True
        names[oid].add(nm)
    noyt = [oid for oid in names if not has_yt[oid]]
    domestic_only = "--domestic" in sys.argv
    if domestic_only:
        # orgs whose name carries a Finnish legal form -> likely in PRH register
        form = re.compile(r"\b(ry|r\.?y\.?|s[äa]{1,2}ti[öo]|sr|osk|oyj?)\b", re.I)
        noyt = [o for o in noyt if any(form.search(fi_segment(n)) for n in names[o])]
    # longest name first (more searchable / canonical-looking)
    targets = sorted(noyt, key=lambda o: -max(len(n) for n in names[o]))
    if limit:
        targets = targets[:limit]

    print(f"y-tunnuksettomia org_id: {len(noyt)}; haetaan: {len(targets)}")
    hits = []
    for i, oid in enumerate(targets):
        # try each distinct name until a hit
        found = None
        for nm in sorted(names[oid], key=len, reverse=True):
            data = prh_search(fi_segment(nm))
            if "_err" in data:
                time.sleep(1)
                continue
            m = best_match(nm, data)
            if m:
                found = {"org_id": oid, "y_tunnus": m[0], "our": nm, "prh": m[1]}
                break
            time.sleep(0.1)
        if found:
            hits.append(found)
            print(f"  ✓ {oid} {found['y_tunnus']}  {found['our'][:40]} ≈ {found['prh'][:40]}")
        if (i + 1) % 20 == 0:
            print(f"  ... {i+1}/{len(targets)}, osumia {len(hits)}")

    print(f"\nOSUMIA: {len(hits)}/{len(targets)}  ({100*len(hits)//max(1,len(targets))}%)")
    json.dump(hits, open("/tmp/ytj_hits.json", "w"), ensure_ascii=False, indent=1)
    print("-> /tmp/ytj_hits.json")


if __name__ == "__main__":
    main()
