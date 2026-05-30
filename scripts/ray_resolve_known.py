#!/usr/bin/env python3
"""Authoritative resolution for agent 'known' decisions.

Agent recall of y-tunnus digits proved unreliable (several wrong ids). So we
keep only the agent's *name* identification and look up the AUTHORITATIVE
business id via PRH name search (companies?name=). Associations (ry/rf) are in
this API; foundations (säätiö) often are not and will remain unresolved.

Reads /tmp/ray_hits.json (decision=='known'), writes /tmp/ray_known_resolved.json
with {ray_name, ytunnus, prh_name, current_name, match} for confident matches.
"""
import json, re, time, urllib.request, urllib.parse, sys

PRH = "https://avoindata.prh.fi/opendata-ytj-api/v3/companies?name="

def norm(s):
    s = (s or "").lower()
    s = re.sub(r"\*.*$", "", s)          # drop *ENGLISH ALT after star
    s = re.sub(r",.*$", "", s)           # drop ", ruotsiksi ..." bilingual tail
    s = re.sub(r"\(.*?\)", " ", s)
    s = s.replace(".", " ").replace("-", " ")
    s = re.sub(r"\b(ry|rf|sr)\b", " ", s)
    s = re.sub(r"[^a-zåäö0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def prh_search(name):
    url = PRH + urllib.parse.quote(name)
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            d = json.load(r)
    except Exception:
        return []
    out = []
    for c in (d.get("companies") or []):
        bid = c.get("businessId")
        bid = bid.get("value") if isinstance(bid, dict) else bid
        for n in (c.get("names") or []):
            if n.get("name"):
                out.append((bid, n["name"]))
    return out

VALID = re.compile(r"^\d{7}-\d$")

def best_match(query, results):
    """Return businessId whose PRH name normalizes EXACTLY to the query, else None."""
    qn = norm(query)
    if not qn:
        return None, None
    for bid, nm in results:
        if norm(nm) == qn and bid and VALID.match(bid):
            return bid, nm
    return None, None

def main():
    hits = json.load(open("/tmp/ray_hits.json"))
    known = [h for h in hits if h["decision"] == "known"]
    resolved, unresolved = [], []
    for h in known:
        # try the agent's current_name first, then the raw RAY name
        queries = []
        for q in (h.get("current_name"), h["ray_name"]):
            qn = norm(q)
            if qn and qn not in queries:
                queries.append(qn)
        found = None
        for q in queries:
            res = prh_search(q)
            time.sleep(0.15)
            bid, nm = best_match(q, res)
            if bid:
                found = (bid, nm); break
        if found:
            agent_yt = h.get("ytunnus", "")
            resolved.append({
                "ray_name": h["ray_name"],
                "ytunnus": found[0],
                "prh_name": found[1],
                "current_name": h.get("current_name", ""),
                "agent_ytunnus": agent_yt,
                "agent_was_right": agent_yt == found[0],
            })
        else:
            unresolved.append(h["ray_name"])

    json.dump(resolved, open("/tmp/ray_known_resolved.json", "w"), ensure_ascii=False, indent=1)
    print(f"known total: {len(known)}")
    print(f"PRH-resolved (authoritative id): {len(resolved)}")
    print(f"  of which agent id was correct: {sum(1 for r in resolved if r['agent_was_right'])}")
    print(f"  agent id was WRONG: {sum(1 for r in resolved if not r['agent_was_right'])}")
    print(f"unresolved (likely säätiö / defunct): {len(unresolved)}")
    print("--- resolved ---")
    for r in resolved:
        flag = "ok " if r["agent_was_right"] else "FIX"
        print(f"  [{flag}] {r['ray_name'][:40]:40} -> {r['ytunnus']}  ({r['prh_name'][:40]})")
    print("--- unresolved ---")
    for u in unresolved:
        print("   ", u)

if __name__ == "__main__":
    main()
