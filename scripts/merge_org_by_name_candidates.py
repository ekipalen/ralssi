"""Generate SAFE merge candidates for orgs lacking y_tunnus (name-based).

Companion to merge_org_by_ytunnus.py (which handles the easy same-y_tunnus
case) and merge_org_apply.py (which applies a confirmed list). Pipeline:
  1. this script  -> /tmp/cand_tierA.json + /tmp/cand_tierB.json
  2. adversarial LLM verification (2 independent judges/candidate)
  3. merge_org_apply.py --apply  (confirmed pairs -> SQLite + Supabase)

Key insight: the " - " / ";" separator is OVERLOADED. It separates a
bilingual Swedish translation ("X ry - X rf") but ALSO a local chapter
from its parent ("Liitto - Turun paikallisyhdistys ry"). Stripping blindly
collapses distinct chapters -> false merge.

Distinguisher: a bilingual tail ends in a SWEDISH legal form (rf / r.f. / ab);
a chapter tail ends in a FINNISH form (ry) or none. We only strip a tail we
can positively classify as Swedish. This is Tier A (auto-safe).

Everything caught by a looser strip (any tail) but NOT Tier A is Tier B,
emitted separately for adversarial LLM verification.
"""

import json
import re
import sqlite3
from collections import defaultdict

DB = "/home/eki/ralssi/data/funding.db"
SEPS = [";", " - ", " – ", " — ", " / ", "/"]
SWED_TAIL = re.compile(r"\b(rf|r\.f\.|ab)\.?$")
FI_FORMS = {"ry", "säätiö", "oy", "oyj", "osk"}


def base_norm(name: str) -> str:
    s = name.lower().strip()
    s = s.replace("r.y.", "ry").replace("r.f.", "rf")
    return re.sub(r"\s+", " ", s).strip()


def split_parts(s: str):
    for sep in SEPS:
        if sep in s:
            head, tail = s.split(sep, 1)
            return head.strip(), tail.strip(), sep
    return s, None, None


def is_swedish_tail(tail: str) -> bool:
    if not tail:
        return False
    return bool(SWED_TAIL.search(tail.strip()))


def ends_in_fi_form(head: str) -> bool:
    toks = head.split()
    return bool(toks) and toks[-1] in FI_FORMS


def norm_safe(name: str) -> str:
    """Tier A: strip only a positively-Swedish bilingual tail, and only when
    the surviving head still ends in a Finnish legal form (so we never cut
    mid-name, e.g. 'Amabile - Lasten... ry - Amabile - Fören... rf')."""
    s = base_norm(name)
    head, tail, sep = split_parts(s)
    if tail is not None and is_swedish_tail(tail) and ends_in_fi_form(head):
        return head
    # no-separator bilingual: 'pakolaisneuvonta ry flyktingrådgivningen rf'
    toks = s.split()
    for i, t in enumerate(toks):
        if t in FI_FORMS and i < len(toks) - 1:
            rest = " ".join(toks[i + 1 :])
            if is_swedish_tail(rest):
                return " ".join(toks[: i + 1])
            break
    return s


def norm_loose(name: str) -> str:
    """Tier B pool: strip any tail after the first separator (recall-heavy)."""
    s = base_norm(name)
    head, tail, sep = split_parts(s)
    if tail is not None:
        return head
    return s


def rows_for(con):
    return con.execute(
        "SELECT org_id, source, source_name, y_tunnus FROM org_mapping WHERE is_category=0"
    ).fetchall()


def build(rows, normfn):
    anchor = defaultdict(set)
    anchor_yt = {}
    names_by_org = defaultdict(list)
    for org_id, source, name, yt in rows:
        names_by_org[org_id].append((source, name))
        if (yt or "").strip():
            anchor[normfn(name)].add(org_id)
            anchor_yt[org_id] = yt.strip()
    hits = defaultdict(set)
    for org_id, source, name, yt in rows:
        if not (yt or "").strip():
            n = normfn(name)
            if n in anchor:
                hits[org_id] |= anchor[n]
    cands = {}
    ambiguous = []
    for org_id, hs in hits.items():
        if len(hs) == 1:
            tgt = next(iter(hs))
            if tgt != org_id:
                cands[org_id] = tgt
        elif len(hs) > 1:
            ambiguous.append(org_id)
    return cands, ambiguous, anchor_yt, names_by_org


def main():
    con = sqlite3.connect(DB)
    rows = rows_for(con)

    safe, safe_amb, anchor_yt, names = build(rows, norm_safe)
    loose, loose_amb, _, _ = build(rows, norm_loose)

    tierB = {k: v for k, v in loose.items() if k not in safe}

    print(f"TIER A (auto-safe, ruotsi-häntä):  {len(safe)}  (ambiguous skip: {len(safe_amb)})")
    print(f"TIER B (löysä, vaatii verifioinnin): {len(tierB)}  (loose ambiguous skip: {len(loose_amb)})")

    def dump(d, path):
        out = []
        for frm, to in d.items():
            out.append({
                "from": frm, "to": to, "to_ytunnus": anchor_yt.get(to),
                "from_names": names[frm], "to_names": names[to],
            })
        json.dump(out, open(path, "w"), ensure_ascii=False, indent=0)
        return out

    a = dump(safe, "/tmp/cand_tierA.json")
    b = dump(tierB, "/tmp/cand_tierB.json")

    print("\n===== TIER A — KAIKKI (tarkista käsin) =====")
    for c in a:
        print(f"[{c['from']}->{c['to']} yt {c['to_ytunnus']}]")
        print(f"   from: {[n for _,n in c['from_names']]}")
        print(f"   to:   {[n for _,n in c['to_names']]}")

    print("\n===== TIER B — näyte (12) =====")
    for c in b[:12]:
        print(f"[{c['from']}->{c['to']} yt {c['to_ytunnus']}]")
        print(f"   from: {[n for _,n in c['from_names']]}")
        print(f"   to:   {[n for _,n in c['to_names']]}")


if __name__ == "__main__":
    main()
