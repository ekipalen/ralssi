#!/usr/bin/env python3
"""Prepare per-org candidate files for the RAY y_tunnus resolution workflow.

For each of the top-N unmatched-by-eur RAY orgs, find token-overlap candidates
from our local y_tunnus pool (org_mapping). Writes /tmp/rayc/NNN.json so each
workflow agent can Read its own org + candidate shortlist.
"""
import sqlite3, json, re, os, math
from collections import defaultdict

DB = "/home/eki/ralssi/data/funding.db"
N = 300
OUT = "/tmp/rayc"

STOP = {"ry","rf","sr","säätiö","saatio","liitto","yhdistys","suomen","ja",
        "förening","r","y","f"}

def norm(s):
    s = (s or "").lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^a-zåäö0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def toks(s):
    return [t for t in norm(s).split() if len(t) > 2]

def main():
    os.makedirs(OUT, exist_ok=True)
    con = sqlite3.connect(DB)

    # local pool: distinct (name, ytunnus); keep best (first) ytunnus per name
    pool = []  # (name, ytunnus, tokenset)
    inv = defaultdict(list)  # token -> list of pool idx
    seen = set()
    for nm, yt in con.execute(
        "SELECT DISTINCT source_name, y_tunnus FROM org_mapping "
        "WHERE y_tunnus IS NOT NULL AND y_tunnus<>''"
    ):
        key = (norm(nm), yt)
        if key in seen:
            continue
        seen.add(key)
        ts = set(toks(nm))
        if not ts:
            continue
        idx = len(pool)
        pool.append((nm, yt, ts))
        for t in ts:
            if t not in STOP:
                inv[t].append(idx)

    unmatched = json.load(open("/tmp/ray_unmatched.json"))[:N]
    for i, o in enumerate(unmatched):
        rt = set(t for t in toks(o["name"]) if t not in STOP)
        scores = defaultdict(float)
        for t in rt:
            # idf-ish weight: rare tokens count more
            w = 1.0 / math.log(2 + len(inv.get(t, [])))
            for pi in inv.get(t, []):
                scores[pi] += w
        ranked = sorted(scores.items(), key=lambda x: -x[1])[:8]
        cands = []
        for ci, (pi, sc) in enumerate(ranked):
            nm, yt, _ = pool[pi]
            cands.append({"i": ci, "name": nm, "ytunnus": yt})
        json.dump(
            {"idx": i, "ray_name": o["name"], "eur": o["eur"], "candidates": cands},
            open(f"{OUT}/{i:03d}.json", "w"), ensure_ascii=False,
        )
    print(f"wrote {len(unmatched)} candidate files to {OUT}/")
    # quick stats: how many have at least one candidate
    havec = sum(1 for i in range(len(unmatched))
                if json.load(open(f"{OUT}/{i:03d}.json"))["candidates"])
    print(f"orgs with >=1 candidate: {havec}/{len(unmatched)}")

if __name__ == "__main__":
    main()
