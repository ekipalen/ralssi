"""Estimate Fingo-like splits: one real entity spread across multiple org_ids
because variant names (Finnish/English/structural) lack a shared y_tunnus.

PAIRWISE, strict (no transitive clustering — that produced a useless blob):
a candidate pair (no-y_tunnus org -> y_tunnus anchor) must share enough
DISTINCTIVE name tokens (rare, proper-noun-like) to be plausibly the same
entity. The result is a HIGH-RECALL candidate set whose true-positive rate is
estimated separately by adversarial verification.
"""

import json
import re
import sqlite3
from collections import defaultdict

DB = "/home/eki/ralssi/data/funding.db"

STOP = set("""
ry rf oy oyj sr osk ab ky tmi rs ry. r.y. r.f. rf.
suomen suomi finland finlands finnish ngo nordic the and för och in of for
yhdistys yhdistyksen förening sällskap seura säätiö stiftelse stiftelsen foundation
liitto liiton förbund keskus center centre central centralen palvelu palvelut
kannatusyhdistys tuki paikallisyhdistys piiri osasto aluejärjestö järjestö
development ngos service servicecentralen utvecklingssamarbete kehitysyhteistyön
association rf, ry, helsingfors helsingin association
""".split())

TOKEN_RE = re.compile(r"[a-zåäö0-9]{4,}", re.IGNORECASE)


def toks(name):
    return {t for t in TOKEN_RE.findall(name.lower()) if t not in STOP}


def main():
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT org_id, source_name, y_tunnus FROM org_mapping WHERE is_category=0"
    ).fetchall()

    has_yt = defaultdict(bool)
    names_by_org = defaultdict(list)
    org_toks = defaultdict(set)
    for org_id, name, yt in rows:
        names_by_org[org_id].append(name)
        if (yt or "").strip():
            has_yt[org_id] = True
        org_toks[org_id] |= toks(name)

    # document frequency per token (distinct org_ids)
    df = defaultdict(int)
    for org_id, tset in org_toks.items():
        for t in tset:
            df[t] += 1
    # distinctive token = used by 2..6 distinct org_ids
    def distinctive(t):
        return 2 <= df[t] <= 6

    # candidate generation: only for no-y_tunnus orgs (the split fragments).
    # For each, find anchor org_ids sharing distinctive tokens; score by
    # Jaccard over each org's distinctive-token set.
    org_dtoks = {o: {t for t in ts if distinctive(t)} for o, ts in org_toks.items()}
    # inverted index over distinctive tokens
    tok_orgs = defaultdict(set)
    for o, ts in org_dtoks.items():
        for t in ts:
            tok_orgs[t].add(o)

    pairs = []  # (noyt_org, partner_org, jaccard, shared_tokens)
    for o, dts in org_dtoks.items():
        if has_yt[o] or not dts:
            continue  # only consider no-y_tunnus fragments with a distinctive token
        # candidate partners: orgs sharing any distinctive token
        cand = set()
        for t in dts:
            cand |= tok_orgs[t]
        cand.discard(o)
        best = None
        for c in cand:
            shared = dts & org_dtoks[c]
            if not shared:
                continue
            union = dts | org_dtoks[c]
            jac = len(shared) / len(union) if union else 0
            # strict: require >=2 shared distinctive tokens. A single shared
            # rare word is mostly coincidence ("Apaja ry" vs "Savutuvan Apaja
            # Oy") or a same-brand-different-chapter case (AIESEC Helsinki vs
            # AIESEC-Suomi) — both are NOT the same registered entity.
            strong = len(shared) >= 2
            if not strong:
                continue
            # prefer a y_tunnus anchor, then higher jaccard
            score = (1 if has_yt[c] else 0, jac, len(shared))
            if best is None or score > best[0]:
                best = (score, c, jac, sorted(shared))
        if best:
            _, c, jac, shared = best
            pairs.append((o, c, round(jac, 2), shared))

    anchored = [p for p in pairs if has_yt[p[1]]]
    noyt_noyt = [p for p in pairs if not has_yt[p[1]]]

    print(f"distinct org_ids: {len(names_by_org)}  (y-tunnus {sum(has_yt.values())}, ilman {len(names_by_org)-sum(has_yt.values())})")
    print(f"\nKANDIDAATTIPARIT (y-tunnukseton fragmentti -> paras vastine):")
    print(f"  yhteensä:                      {len(pairs)}")
    print(f"  -> y-tunnukselliseen ankkuriin: {len(anchored)}")
    print(f"  -> toiseen y-tunnuksettomaan:   {len(noyt_noyt)}")

    # dump anchored pairs with ALL names per org (for adversarial verification)
    yt_by_org = {}
    for org_id, name, yt in rows:
        if (yt or "").strip():
            yt_by_org[org_id] = yt.strip()
    sample = []
    for i, (o, c, jac, shared) in enumerate(anchored):
        sample.append({
            "i": i, "from": o, "to": c, "jac": jac, "shared": shared,
            "y": yt_by_org.get(c),
            "fn": sorted(set(names_by_org[o]))[:6],
            "tn": sorted(set(names_by_org[c]))[:6],
        })
    json.dump(sample, open("/tmp/split_pairs.json", "w"), ensure_ascii=False)
    print(f"\n{len(sample)} ankkuroitua paria -> /tmp/split_pairs.json")

    # dump noyt<->noyt pairs too (neither side has a y_tunnus anchor) for review
    nn = []
    for i, (o, c, jac, shared) in enumerate(noyt_noyt):
        nn.append({
            "i": i, "from": o, "to": c, "jac": jac, "shared": shared,
            "fn": sorted(set(names_by_org[o]))[:6],
            "tn": sorted(set(names_by_org[c]))[:6],
        })
    json.dump(nn, open("/tmp/split_pairs_noyt.json", "w"), ensure_ascii=False)
    print(f"{len(nn)} noyt<->noyt paria -> /tmp/split_pairs_noyt.json")


if __name__ == "__main__":
    main()
