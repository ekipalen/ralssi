#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["numpy"]
# ///
"""ralssi - Finnish funding data explorer. Zero external dependencies."""

import argparse
import csv
import glob
import io
import json
import os
import sqlite3
import subprocess
import sys
import textwrap
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "data", "funding.db")

# Sectors excluded by --third-sector filter (default ON)
EXCLUDED_SECTORS = {"company", "government", "university", "research", "international"}

# Name patterns indicating non-third-sector orgs (used when sector is NULL)
_NON_THIRD_SECTOR_PATTERNS = [
    " oy", " ab", " oyj", " ltd", " ky", " tmi",
    "kaupunki", "stad", "yliopisto", "universitet", "university",
    "ammattikorkeakoulu", "korkeakoulusäätiö", "avoin yhtiö", "kommandiittiyhtiö",
]

SOURCES = {
    "stea":     {"table": "grants",         "name": "jarjesto",    "amount": "myonnetty",          "year": "vuosi", "desc": "STEA (avustuskeskus)"},
    "ray":      {"table": "ray_grants",     "name": "jarjesto",    "amount": "myonnetty",          "year": "vuosi", "desc": "RAY (Raha-automaattiyhdistys 2000-2016)"},
    "eura":     {"table": "eura_all",       "name": "toteuttaja",  "amount": "myonnetty_eu_valtio","year": None,    "desc": "EU structural funds (EURA)"},
    "bf":       {"table": "bf_awarded",     "name": "organisation","amount": "total_eur",          "year": "year",  "desc": "Business Finland"},
    "um":       {"table": "um_grants",      "name": "organisation","amount": "amount",             "year": "year",  "desc": "UM/IATI dev cooperation"},
    "helsinki":	{"table": "helsinki_grants", "name": "hakija",      "amount": "myonnetty",          "year": "vuosi", "desc": "Helsinki municipal"},
    "va":       {"table": "va_grants",      "name": "organisation","amount": "granted_eur",        "year": "year",  "desc": "Valtionavustukset (haeavustuksia.fi)"},
    "fts":      {"table": "fts_grants",     "name": "organisation","amount": "amount",             "year": "year",  "desc": "EU FTS (Financial Transparency System)"},
}

# Canonical source order = SOURCES insertion order. Derive every source-list from this
# (never hardcode the list of sources) so adding a source is a single edit to SOURCES above.
SOURCE_ORDER = list(SOURCES.keys())

# Sources with a vector index (vsearch). Keys must be a subset of SOURCES; a source
# without embeddings simply isn't listed here and shows "no index" in coverage.
EMB_FILES = {
    "stea": ("embeddings.npy", "embedding_ids.json"),
    "ray":  ("ray_embeddings.npy", "ray_embedding_ids.json"),
    "eura": ("eura_embeddings.npy", "eura_embedding_ids.json"),
    "um":   ("um_embeddings.npy", "um_embedding_ids.json"),
    "va":   ("va_embeddings.npy", "va_embedding_ids.json"),
    "fts":  ("fts_embeddings.npy", "fts_embedding_ids.json"),
}


def connect():
    if not os.path.exists(DB_PATH):
        die(f"Database not found: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def die(msg):
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(1)


def _prune_missing_sources(conn):
    """Drop any registered source whose table is absent from this database.

    Reference data is distributed separately from the code, so a given machine's
    funding.db may lag behind (e.g. RAY not yet loaded). Rather than crash, adapt
    the tool to whatever data is actually present and note what's missing on stderr.
    Mutates SOURCES / SEARCH_FIELDS / SOURCE_ORDER in place so every command follows.
    """
    present = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    missing = [s for s in SOURCE_ORDER if SOURCES[s]["table"] not in present]
    if not missing:
        return
    for s in missing:
        SOURCES.pop(s, None)
        SEARCH_FIELDS.pop(s, None)
        EMB_FILES.pop(s, None)
    SOURCE_ORDER[:] = [s for s in SOURCE_ORDER if s not in missing]
    print(f"Note: source(s) not loaded in this database, skipping: {', '.join(missing)} "
          f"(run setup / update funding.db to include them)", file=sys.stderr)


def _is_non_third_sector_name(name):
    """Heuristic: return True if name looks like a non-third-sector org."""
    if not name:
        return False
    lower = name.lower()
    for pat in _NON_THIRD_SECTOR_PATTERNS:
        if pat in lower:
            return True
    return False


def _third_sector_sql_for_source(source, third_sector):
    """Return (WHERE fragment, params) to filter a source table by third sector.

    Uses org_mapping.sector when available, falls back to name heuristic.
    Returns ("", []) when third_sector is False (no filtering).
    """
    if not third_sector:
        return "", []
    s = SOURCES[source]
    name_col = s["name"]
    # Subquery: exclude names whose org_mapping sector is in EXCLUDED_SECTORS
    excluded_list = ",".join(f"'{s}'" for s in EXCLUDED_SECTORS)
    clause = (
        f"{name_col} NOT IN ("
        f"SELECT source_name FROM org_mapping "
        f"WHERE source = ? AND sector IN ({excluded_list})"
        f")"
    )
    return clause, [source]


def _third_sector_filter_rows(rows, name_key, conn, source, third_sector):
    """Filter a list of row dicts/Row objects, removing non-third-sector orgs.

    Uses org_mapping.sector, falls back to name heuristic for names not in mapping.
    Returns filtered list when third_sector is True.
    """
    if not third_sector:
        return rows
    # Build a cache of sector by (source, name)
    names = set(r[name_key] for r in rows if r[name_key])
    if not names:
        return rows
    ph = ",".join("?" for _ in names)
    sector_rows = conn.execute(
        f"SELECT source_name, sector FROM org_mapping WHERE source = ? AND source_name IN ({ph})",
        [source] + list(names),
    ).fetchall()
    sector_map = {r["source_name"]: r["sector"] for r in sector_rows}
    result = []
    for r in rows:
        name = r[name_key]
        sector = sector_map.get(name)
        if sector and sector in EXCLUDED_SECTORS:
            continue
        if sector is None and _is_non_third_sector_name(name):
            continue
        result.append(r)
    return result


def _third_sector_name_excluded(name, conn, source=None):
    """Check if an org name should be excluded by third-sector filter.

    If source given, checks org_mapping for that source.
    Otherwise checks all sources. Falls back to name heuristic.
    """
    if source:
        row = conn.execute(
            "SELECT sector FROM org_mapping WHERE source = ? AND source_name = ?",
            [source, name],
        ).fetchone()
        if row:
            return row["sector"] in EXCLUDED_SECTORS
    else:
        rows = conn.execute(
            "SELECT DISTINCT sector FROM org_mapping WHERE source_name = ?",
            [name],
        ).fetchall()
        if rows:
            return all(r["sector"] in EXCLUDED_SECTORS for r in rows)
    return _is_non_third_sector_name(name)


def _third_sector_org_excluded(conn, org_id):
    """Check if an org_id's sector(s) are all in EXCLUDED_SECTORS."""
    rows = conn.execute(
        "SELECT DISTINCT sector FROM org_mapping WHERE org_id = ? AND COALESCE(is_category, 0) = 0",
        [org_id],
    ).fetchall()
    if not rows:
        return False
    return all(r["sector"] in EXCLUDED_SECTORS for r in rows if r["sector"])


def _escape_like(s):
    """Escape SQL LIKE wildcards in user input."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def fmt_money(amount):
    if amount is None:
        return "-"
    amount = int(round(amount))
    if amount < 0:
        return "-" + fmt_money(-amount)
    s = str(amount)
    parts = []
    while s:
        parts.append(s[-3:])
        s = s[:-3]
    return " ".join(reversed(parts)) + " €"


def _fmt_num(n):
    """Format number with space as thousands separator."""
    return f"{n:,}".replace(",", " ")


def print_table(headers, rows):
    if not rows:
        print("  (no data)")
        return
    widths = [len(h) for h in headers]
    str_rows = []
    for row in rows:
        sr = [str(v) if v is not None else "-" for v in row]
        str_rows.append(sr)
        for i, v in enumerate(sr):
            if i < len(widths):
                widths[i] = max(widths[i], len(v))
    fmt = "  ".join("{:%d}" % w for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*["-" * w for w in widths]))
    for sr in str_rows:
        print(fmt.format(*sr))


def resolve_org(conn, name):
    """Resolve org name via org_mapping. Returns list of org groups.

    Each group is {"org_id": int, "match": str, "sources": {source: set(names)}}.
    Groups are sorted: exact match first, then by shortest matched name (most specific).
    """
    rows = conn.execute(
        "SELECT source, source_name, org_id FROM org_mapping "
        "WHERE source_name LIKE ? ESCAPE '\\' AND COALESCE(is_category, 0) = 0",
        [f"%{_escape_like(name)}%"],
    ).fetchall()
    if not rows:
        return []
    groups = {}
    for r in rows:
        oid = r["org_id"]
        if oid not in groups:
            groups[oid] = {"org_id": oid, "match": r["source_name"], "sources": {}}
        if len(r["source_name"]) < len(groups[oid]["match"]):
            groups[oid]["match"] = r["source_name"]
    for oid, g in groups.items():
        for p in conn.execute("SELECT source, source_name FROM org_mapping WHERE org_id = ? AND COALESCE(is_category, 0) = 0", [oid]):
            g["sources"].setdefault(p["source"], set()).add(p["source_name"])

    def sort_key(g):
        m = g["match"].lower()
        n = name.lower()
        if m == n:
            return (0, len(m))
        if m.startswith(n) or m.endswith(n):
            return (1, len(m))
        return (2, len(m))

    return sorted(groups.values(), key=sort_key)


def query_source(conn, source, names):
    """Query source table with exact name matching (IN clause)."""
    s = SOURCES[source]
    if not names:
        return []
    ph = ",".join("?" for _ in names)
    return conn.execute(
        f"SELECT * FROM {s['table']} WHERE {s['name']} IN ({ph})", list(names)
    ).fetchall()


def source_summary(source, rows):
    """Returns (count, total_amount, year_range_str)."""
    s = SOURCES[source]
    total = sum(r[s["amount"]] or 0 for r in rows)
    if s["year"]:
        years = [r[s["year"]] for r in rows if r[s["year"]] is not None]
        yr = f"{min(years)}-{max(years)}" if years else "-"
    elif source == "eura":
        yy = [r["aloituspvm"][:4] for r in rows if r["aloituspvm"]]
        yy += [r["paattymispvm"][:4] for r in rows if r["paattymispvm"]]
        yr = f"{min(yy)}-{max(yy)}" if yy else "-"
    else:
        yr = "-"
    return len(rows), total, yr


def _detail_grants(source, rows, limit):
    """Return a list of detail dicts for individual grants, source-appropriate columns."""
    s = SOURCES[source]
    details = []
    for r in rows:
        if source in ("stea", "ray"):
            details.append({
                "year": r["vuosi"], "amount": r["myonnetty"],
                "kayttotarkoitus": r["kayttotarkoitus"] if "kayttotarkoitus" in r.keys() else None,
            })
        elif source == "eura":
            details.append({
                "hankekoodi": r["hankekoodi"], "start": r["aloituspvm"],
                "amount": r["myonnetty_eu_valtio"],
                "nimi": r["nimi"] if "nimi" in r.keys() else None,
            })
        elif source == "bf":
            # bf_awarded has no title; show the funding-type breakdown instead.
            parts = []
            for col, lbl in (("grants_eur", "grant"), ("loans_eur", "loan"),
                             ("research_eur", "research"), ("eu_structural_eur", "eu")):
                v = r[col] if col in r.keys() else None
                if v:
                    parts.append(f"{lbl} {fmt_money(v)}")
            details.append({
                "year": r["year"], "amount": r["total_eur"],
                "breakdown": ", ".join(parts) or None,
            })
        elif source == "um":
            details.append({
                "year": r["year"], "amount": r["amount"],
                "title": r["title"] if "title" in r.keys() else None,
            })
        elif source == "helsinki":
            details.append({
                "year": r["vuosi"], "amount": r["myonnetty"],
                "hakemustyyppi": r["hakemustyyppi"] if "hakemustyyppi" in r.keys() else None,
            })
        elif source == "va":
            details.append({
                "year": r["year"], "amount": r["granted_eur"],
                "grantor": r["grantor"] if "grantor" in r.keys() else None,
                "purpose": r["purpose"] if "purpose" in r.keys() else None,
            })
        elif source == "fts":
            details.append({
                "year": r["year"], "amount": r["amount"],
                "programme": r["programme"] if "programme" in r.keys() else None,
            })
    # Sort by amount descending
    details.sort(key=lambda d: -(d.get("amount") or 0))
    return details[:limit]


def _print_detail_table(source, details):
    """Print a source-appropriate detail table."""
    if not details:
        print("    (no grants)")
        return
    if source in ("stea", "ray"):
        print_table(["Year", "Amount", "Kayttotarkoitus"], [
            [str(d["year"] or "-"), fmt_money(d["amount"]),
             textwrap.shorten(d["kayttotarkoitus"] or "-", 60, placeholder="...")]
            for d in details
        ])
    elif source == "eura":
        print_table(["Hankekoodi", "Start", "Amount", "Nimi"], [
            [str(d["hankekoodi"] or "-"), str(d["start"] or "-"), fmt_money(d["amount"]),
             textwrap.shorten(d["nimi"] or "-", 50, placeholder="...")]
            for d in details
        ])
    elif source == "bf":
        print_table(["Year", "Amount", "Breakdown"], [
            [str(d["year"] or "-"), fmt_money(d["amount"]),
             textwrap.shorten(d["breakdown"] or "-", 60, placeholder="...")]
            for d in details
        ])
    elif source == "um":
        print_table(["Year", "Amount", "Title"], [
            [str(d["year"] or "-"), fmt_money(d["amount"]),
             textwrap.shorten(d["title"] or "-", 60, placeholder="...")]
            for d in details
        ])
    elif source == "helsinki":
        print_table(["Year", "Amount", "Hakemustyyppi"], [
            [str(d["year"] or "-"), fmt_money(d["amount"]),
             textwrap.shorten(d["hakemustyyppi"] or "-", 60, placeholder="...")]
            for d in details
        ])
    elif source == "va":
        print_table(["Year", "Amount", "Grantor", "Purpose"], [
            [str(d["year"] or "-"), fmt_money(d["amount"]),
             textwrap.shorten(d["grantor"] or "-", 20, placeholder="..."),
             textwrap.shorten(d["purpose"] or "-", 45, placeholder="...")]
            for d in details
        ])
    elif source == "fts":
        print_table(["Year", "Amount", "Programme"], [
            [str(d["year"] or "-"), fmt_money(d["amount"]),
             textwrap.shorten(d["programme"] or "-", 60, placeholder="...")]
            for d in details
        ])


def _summarize_org_group(conn, source_names, json_mode, detail=False, detail_source=None, detail_limit=50):
    """Summarize one org group across sources. Returns (output_list, combined_total)."""
    combined_total = 0
    output = []
    for src in SOURCE_ORDER:
        if src not in source_names:
            continue
        rows = query_source(conn, src, source_names[src])
        if not rows:
            continue
        count, total, yr = source_summary(src, rows)
        combined_total += total
        entry = {"source": src, "names": sorted(source_names[src]),
                 "count": count, "total": total, "year_range": yr}
        if detail and (detail_source is None or detail_source == src):
            entry["grants"] = _detail_grants(src, rows, detail_limit)
        output.append(entry)
    return output, combined_total


def cmd_org(args, conn):
    name = args.name
    third_sector = args.third_sector
    org_groups = resolve_org(conn, name)

    # Third-sector filter: remove org groups whose sector is excluded
    if third_sector and org_groups:
        org_groups = [g for g in org_groups
                      if g["org_id"] is None or not _third_sector_org_excluded(conn, g["org_id"])]

    if not org_groups:
        source_names = {}
        for src, s in SOURCES.items():
            found = conn.execute(
                f"SELECT DISTINCT {s['name']} FROM {s['table']} WHERE {s['name']} LIKE ? ESCAPE '\\'",
                [f"%{_escape_like(name)}%"],
            ).fetchall()
            if found:
                names_set = set(r[0] for r in found)
                if third_sector:
                    names_set = {n for n in names_set
                                 if not _third_sector_name_excluded(n, conn, src)}
                if names_set:
                    source_names[src] = names_set
        if source_names:
            org_groups = [{"org_id": None, "match": name, "sources": source_names}]

    if not org_groups:
        die(f'No org found matching "{name}"')

    if args.merge and len(org_groups) > 1:
        merged = {"org_id": None, "match": name + " (merged)", "sources": {}}
        oids = []
        for g in org_groups:
            oids.append(str(g["org_id"]))
            for src, names in g["sources"].items():
                merged["sources"].setdefault(src, set()).update(names)
        if not args.json:
            print(f"Merging {len(org_groups)} org groups: {', '.join(oids)}", file=sys.stderr)
        org_groups = [merged]

    if len(org_groups) > 1 and not args.json:
        print(f'Found {len(org_groups)} distinct organizations matching "{name}":', file=sys.stderr)
        for g in org_groups:
            print(f'  - {g["match"]} (org_id {g["org_id"]})', file=sys.stderr)
        print(f'Use --merge to combine them into one view.', file=sys.stderr)
        print(file=sys.stderr)

    detail = args.detail
    detail_source = args.source
    detail_limit = args.limit

    all_results = []
    for g in org_groups:
        output, combined_total = _summarize_org_group(
            conn, g["sources"], args.json,
            detail=detail, detail_source=detail_source, detail_limit=detail_limit,
        )
        if output:
            all_results.append({
                "org_id": g["org_id"], "match": g["match"],
                "sources": output, "combined_total": combined_total,
            })

    # Optional public-contracts (HILMA) section — a separate money stream, kept out of the
    # grant combined_total (grants and contract value are different money types).
    contracts = None
    if getattr(args, "contracts", False):
        # Resolve org_ids fresh (independent of --merge, which collapses org_id to None).
        contracts = _contracts_summary(conn, _contract_org_ids(conn, name))

    if args.json:
        if len(all_results) == 1:
            r = all_results[0]
            out = {"query": name, "org_id": r["org_id"], "match": r["match"],
                   "sources": r["sources"], "combined_total": r["combined_total"]}
        else:
            out = {"query": name, "results": all_results}
        if contracts is not None:
            out["contracts"] = contracts
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    for i, r in enumerate(all_results):
        if len(all_results) > 1:
            print(f'--- {r["match"]} (org_id {r["org_id"]}) ---\n')
        else:
            print(f'Org search: "{name}"\n')
        for item in r["sources"]:
            print(f"  {item['source'].upper()} ({SOURCES[item['source']]['desc']})")
            if len(item["names"]) > 1 or item["names"][0].lower() != name.lower():
                print(f"    Names: {', '.join(item['names'])}")
            print(f"    Grants: {item['count']}  Total: {fmt_money(item['total'])}  Years: {item['year_range']}\n")
            if detail and "grants" in item:
                _print_detail_table(item["source"], item["grants"])
                if len(item["grants"]) == detail_limit:
                    print(f"    (limited to {detail_limit} grants, use --limit to show more)")
                print()
        print(f"  Combined grant total: {fmt_money(r['combined_total'])}")
        if i < len(all_results) - 1:
            print()

    if contracts is not None:
        c = contracts
        print(f"\n  Public contracts (HILMA): {c['win_count']} wins | "
              f"attributable {fmt_money(c['attributable_total'])} | "
              f"+{c['shared_win_count']} shared-win (not attributable)")
        for t in c["top"]:
            label = f"{t['buyer'] or '-'} — {t['title'] or '-'}"
            print(f"    {(t['year'] or '-'):4s} {fmt_money(t['value']):>14s}  "
                  f"{textwrap.shorten(label, 58, placeholder='...')}")


def _sources_with_ytunnus(conn):
    """Sources whose grant table has a y_tunnus column (aggregated directly by y_tunnus).

    Derived from the schema, not hardcoded — a new source with a y_tunnus column is
    picked up automatically; sources without one (um, helsinki) fall back to org_mapping.
    """
    result = set()
    for src, s in SOURCES.items():
        cols = conn.execute(f"PRAGMA table_info({s['table']})").fetchall()
        if any(c["name"] == "y_tunnus" for c in cols):
            result.add(src)
    return result


def _aggregate_by_ytunnus(conn, src, since=None, until=None):
    """Aggregate grants by y_tunnus for a source that has y_tunnus column."""
    s = SOURCES[src]
    where_parts = ["y_tunnus != ''", "y_tunnus IS NOT NULL"]
    params = []
    yr_clause, yr_params = _year_where(src, since, until)
    if yr_clause:
        where_parts.append(yr_clause)
        params.extend(yr_params)
    where = " AND ".join(where_parts)
    return conn.execute(
        f"SELECT y_tunnus, {s['name']} as org_name, SUM({s['amount']}) as total "
        f"FROM {s['table']} WHERE {where} GROUP BY y_tunnus",
        params,
    ).fetchall()


# RAY (2000-2016) is a known lower bound: ~24 % of RAY euros have no y_tunnus and are
# unlinked, so any org/family total that includes RAY can UNDERcount (never overcount).
RAY_LOWER_BOUND_NOTE = ("Note: total includes RAY (2000-2016); ~24 % of RAY euros are unlinked "
                        "to an organisation, so RAY-era figures are a LOWER BOUND.")


def _ray_caveat(sources, json_mode):
    """Print the RAY lower-bound caveat (part of the answer) if 'ray' contributes to a total."""
    if not json_mode and "ray" in sources:
        print(f"  {RAY_LOWER_BOUND_NOTE}")


def _org_id_aggregate(conn, sources=None, since=None, until=None):
    """Aggregate grant money by org_id, summing ALL name variants per source.

    This is the canonical org-level aggregation — identical semantics to the website's
    org_id-based RPCs and to the `org`/`profile` commands (which sum every org_mapping
    name variant for an org_id). Efficient: one GROUP BY per source plus an in-memory fold,
    so it does not issue a query per organisation.

    Returns {org_id: {"display": str, "y_tunnus": str|None, "sectors": set(str),
                       "source_totals": {src: eur}, "total": eur}}.
    Grant-table names with no org_mapping row are NOT attributed to any org_id (unmapped
    orphans). Honors the year filter; does NOT apply the third-sector filter (caller decides).
    """
    srcs = [s for s in SOURCE_ORDER if (sources is None or s in sources)]
    name_to_oid = {s: {} for s in srcs}          # src -> {source_name: org_id}
    oid_names = {}                               # org_id -> set(display-candidate names)
    oid_yt, oid_sectors = {}, {}
    for row in conn.execute(
        "SELECT org_id, source, source_name, y_tunnus, sector FROM org_mapping "
        "WHERE COALESCE(is_category, 0) = 0"
    ):
        src = row["source"]
        if src not in name_to_oid:
            continue
        oid = row["org_id"]
        # Key by lower-cased name to mirror the website RPCs' LOWER(source_name)=LOWER(raw_name)
        # join — grant-table casing can differ from org_mapping (esp. bf/um).
        name_to_oid[src][(row["source_name"] or "").lower()] = oid
        oid_names.setdefault(oid, set()).add(row["source_name"])
        if row["y_tunnus"]:
            oid_yt.setdefault(oid, row["y_tunnus"])
        if row["sector"]:
            oid_sectors.setdefault(oid, set()).add(row["sector"])

    result = {}
    for src in srcs:
        s = SOURCES[src]
        yr_clause, yr_params = _year_where(src, since, until)
        where = f"WHERE {yr_clause}" if yr_clause else ""
        for r in conn.execute(
            f"SELECT {s['name']} as name, SUM({s['amount']}) as total "
            f"FROM {s['table']} {where} GROUP BY {s['name']}", yr_params
        ):
            oid = name_to_oid[src].get((r["name"] or "").lower())
            if oid is None:
                continue
            tot = r["total"] or 0
            if tot == 0:
                continue
            e = result.setdefault(oid, {"display": None, "y_tunnus": oid_yt.get(oid),
                                        "sectors": oid_sectors.get(oid, set()),
                                        "source_totals": {}, "total": 0})
            e["source_totals"][src] = e["source_totals"].get(src, 0) + tot
            e["total"] += tot
    for oid, e in result.items():
        names = oid_names.get(oid)
        e["display"] = min(names, key=len) if names else f"org_{oid}"
    return result


def _sectors_excluded(sectors):
    """Mirror _third_sector_org_excluded using a pre-collected sector set.

    True if every non-null sector is in EXCLUDED_SECTORS (all-NULL also counts as excluded,
    matching _third_sector_org_excluded's behaviour)."""
    return all(s in EXCLUDED_SECTORS for s in sectors if s)


def cmd_hunters(args, conn):
    min_sources, limit, sort_by = args.min, args.limit, args.sort
    verbose = getattr(args, "verbose", False)
    third_sector = args.third_sector
    since, until = _resolve_year_bounds(args)
    source_filter = set(s.strip().lower() for s in args.sources.split(",")) if getattr(args, "sources", None) else None
    if source_filter:
        unknown = source_filter - set(SOURCES.keys())
        if unknown:
            die(f"Unknown source(s): {', '.join(sorted(unknown))}. Use: {', '.join(SOURCES)}")
        args.min = min(args.min, len(source_filter))
        min_sources = args.min
    # Canonical aggregation: by org_id, summing ALL name variants per source (same semantics
    # as the website's org_id RPCs and the org/profile commands). This replaces the older
    # y_tunnus-keyed path that silently dropped grants filed under a blank/NULL y_tunnus and
    # whose default vs --verbose totals disagreed. Now --verbose only changes the display.
    agg = _org_id_aggregate(conn, sources=source_filter, since=since, until=until)

    results = []
    for oid, d in agg.items():
        srcs = set(d["source_totals"].keys())
        # When a source filter is set, require the org to appear in ALL named sources.
        if source_filter and not (srcs >= source_filter):
            continue
        if len(srcs) < min_sources:
            continue
        if third_sector and _sectors_excluded(d["sectors"]):
            continue
        flags = []
        if "stea" in srcs and "bf" in srcs:
            flags.append("ngo+company")
        if "um" in srcs and (srcs & {"stea", "helsinki"}):
            flags.append("dev+domestic")
        if "helsinki" in srcs and "stea" in srcs:
            flags.append("municipal+national")
        results.append({
            "name": d["display"],
            "y_tunnus": d["y_tunnus"] or "-",
            "sources": sorted(srcs), "source_count": len(srcs),
            "total": d["total"], "flags": flags,
            "source_totals": d["source_totals"],
        })

    key_fn = (lambda r: (-r["source_count"], -r["total"])) if sort_by == "sources" else (lambda r: -r["total"])
    results.sort(key=key_fn)
    results = results[:limit]

    if args.json:
        if verbose:
            print(json.dumps(results, ensure_ascii=False, indent=2))
        else:
            print(json.dumps([{k: v for k, v in r.items() if k != "source_totals"} for r in results],
                              ensure_ascii=False, indent=2))
        return

    if source_filter:
        header = f"Funding hunters (sources: {','.join(sorted(source_filter))}, top {limit} by {sort_by})"
    else:
        header = f"Funding hunters (min {min_sources} sources, top {limit} by {sort_by})"
    yr_label = _year_filter_label(args)
    if yr_label:
        header += f" ({yr_label})"
    if third_sector:
        header += " [third-sector]"
    print(f"{header}\n")
    if verbose:
        for r in results:
            print(f"  {r['name']}")
            for src in sorted(r["source_totals"]):
                print(f"    {src.upper():10s} {fmt_money(r['source_totals'][src]):>18s}")
            print(f"    {'TOTAL':10s} {fmt_money(r['total']):>18s}")
            if r["flags"]:
                print(f"    Flags: {' '.join(r['flags'])}")
            print()
    else:
        print_table(["Name", "Sources", "Total", "Flags"], [
            [textwrap.shorten(r["name"], 40, placeholder="..."),
             ",".join(r["sources"]), fmt_money(r["total"]),
             " ".join(r["flags"])]
            for r in results
        ])


def cmd_top(args, conn):
    source, n = args.source, args.n
    since, until = _resolve_year_bounds(args)
    yr_label = _year_filter_label(args)
    third_sector = args.third_sector

    if source:
        if source not in SOURCES:
            die(f"Unknown source: {source}. Use: {', '.join(SOURCES)}")
        s = SOURCES[source]
        yr_clause, yr_params = _year_where(source, since, until)
        where = f"WHERE {yr_clause}" if yr_clause else ""
        # org_mapping lookup for this source: fold name variants of one org into a single
        # org_id bucket (so an org split across spellings ranks as one row, matching the
        # website's get_*_top). Names absent from org_mapping stay as their own entry so no
        # recipient is dropped.
        # Lower-cased keys mirror the website RPCs' LOWER() join (grant casing can differ from org_mapping).
        name_to_oid, sector_by_name = {}, {}
        for r in conn.execute(
            "SELECT source_name, org_id, sector FROM org_mapping WHERE source = ? AND COALESCE(is_category,0)=0",
            [source],
        ):
            name_to_oid[(r["source_name"] or "").lower()] = r["org_id"]
            if r["sector"]:
                sector_by_name.setdefault((r["source_name"] or "").lower(), set()).add(r["sector"])
        buckets = {}
        for r in conn.execute(
            f"SELECT {s['name']} as name, SUM({s['amount']}) as total, COUNT(*) as cnt "
            f"FROM {s['table']} {where} GROUP BY {s['name']}", yr_params,
        ):
            nm = r["name"]
            if not nm or nm.strip() in ("", "-"):   # skip placeholder/empty recipient names
                continue
            nl = nm.lower()
            oid = name_to_oid.get(nl)
            key = ("oid", oid) if oid is not None else ("name", nm)
            b = buckets.setdefault(key, {"name": nm, "_top": -1, "total": 0, "cnt": 0, "sectors": set()})
            b["total"] += r["total"] or 0
            b["cnt"] += r["cnt"]
            b["sectors"].update(sector_by_name.get(nl, set()))
            if (r["total"] or 0) > b["_top"]:          # display = the largest-contributing variant
                b["_top"], b["name"] = (r["total"] or 0), nm
        rows = list(buckets.values())
        if third_sector:
            def _keep(b):
                if b["sectors"]:
                    return not _sectors_excluded(b["sectors"])
                return not _is_non_third_sector_name(b["name"])   # unmapped name -> heuristic
            rows = [b for b in rows if _keep(b)]
        rows.sort(key=lambda b: -b["total"])
        rows = rows[:n]
        if args.json:
            print(json.dumps([{"name": b["name"], "total": b["total"], "grants": b["cnt"]} for b in rows],
                              ensure_ascii=False, indent=2))
            return
        title = f"Top {n} recipients — {source.upper()}"
        if yr_label:
            title += f" ({yr_label})"
        if third_sector:
            title += " [third-sector]"
        print(f"{title}\n")
        print_table(["#", "Name", "Total", "Grants"], [
            [str(i), textwrap.shorten(b["name"] or "-", 50, placeholder="..."),
             fmt_money(b["total"]), str(b["cnt"])]
            for i, b in enumerate(rows, 1)
        ])
        _ray_caveat({source}, args.json)
    else:
        # Accurate cross-source ranking: aggregate by org_id, summing every name variant
        # across all sources (no longer the old name-string approximation).
        agg = _org_id_aggregate(conn, since=since, until=until)
        rows = [{"name": d["display"], "total": d["total"]}
                for d in agg.values()
                if not (third_sector and _sectors_excluded(d["sectors"]))]
        rows.sort(key=lambda x: -x["total"])
        rows = rows[:n]
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
            return
        title = f"Top {n} recipients — all sources (by org_id)"
        if yr_label:
            title += f" ({yr_label})"
        if third_sector:
            title += " [third-sector]"
        print(f"{title}\n")
        print_table(["#", "Name", "Total"], [
            [str(i), textwrap.shorten(r["name"] or "-", 55, placeholder="..."), fmt_money(r["total"])]
            for i, r in enumerate(rows, 1)
        ])
        _ray_caveat(set(SOURCE_ORDER), args.json)



def cmd_verify(args, conn):
    name = args.name
    org_groups = resolve_org(conn, name)
    source_names = {}
    for g in org_groups:
        for src, names in g["sources"].items():
            source_names.setdefault(src, set()).update(names)

    print(f'Verify: "{name}"\n')

    if source_names:
        print("  Name variants (from org_mapping):")
        for src in sorted(source_names):
            for n in sorted(source_names[src]):
                print(f"    {src.upper():10s} {n}")
        print()
    else:
        print("  Not found in org_mapping. Trying direct search...\n")

    found_any = False
    for src in SOURCE_ORDER:
        names = source_names.get(src, set())
        if not names:
            s = SOURCES[src]
            direct = conn.execute(
                f"SELECT DISTINCT {s['name']} FROM {s['table']} WHERE {s['name']} LIKE ? ESCAPE '\\'",
                [f"%{_escape_like(name)}%"],
            ).fetchall()
            if direct:
                names = set(r[0] for r in direct)
        if not names:
            continue

        rows = query_source(conn, src, names)
        if not rows:
            continue
        found_any = True
        count, total, _ = source_summary(src, rows)
        print(f"  {src.upper()} DB: {count} grants, total {fmt_money(total)}")

        if src == "stea":
            ytunnus = next((r["y_tunnus"] for r in rows if r["y_tunnus"]), None)
            if ytunnus:
                print(f"    Y-tunnus: {ytunnus}")
                print(f"    Verify: https://avustukset.stea.fi (search org name)")
                print(f"    API: curl https://avustukset.stea.fi/api/organisation/{{org_id}}")
                print(f"    Note: API requires numeric org_id, not y-tunnus. Web UI shows org_id in URL.")

        if src == "um":
            iati_dir = os.path.join(SCRIPT_DIR, "data", "iati")
            if os.path.isdir(iati_dir):
                xml_files = glob.glob(os.path.join(iati_dir, "*.xml"))
                if xml_files:
                    try:
                        result = subprocess.run(
                            ["grep", "-li", name] + xml_files,
                            capture_output=True, text=True, timeout=10,
                        )
                        if result.stdout.strip():
                            matched = result.stdout.strip().split("\n")
                            print(f"  IATI XML: found in {len(matched)} file(s):")
                            for f in matched:
                                print(f"    {os.path.basename(f)}")
                    except Exception as e:
                        print(f"  IATI XML search failed: {e}")

        if src == "va":
            ytunnus = next((r["y_tunnus"] for r in rows if r["y_tunnus"]), None)
            if ytunnus:
                print(f"    Y-tunnus: {ytunnus}")
            print(f"    Verify: https://haeavustuksia.fi (tutkiavustuksia.fi)")
            print(f"    Raw data: data/okm/Myönteiset päätökset.xlsx")
        print()

    if not found_any and not source_names:
        print(f'  No data found for "{name}" in any source.')

    if "um" in source_names and len(source_names.get("um", set())) > 1:
        print("  WARNING: Multiple UM name variants found. UM/IATI data uses")
        print("  inconsistent org names across years. All variants are included.")


def cmd_sources(args, conn):
    data = []
    for src, s in SOURCES.items():
        count = conn.execute(f"SELECT COUNT(*) FROM {s['table']}").fetchone()[0]
        total = conn.execute(f"SELECT SUM({s['amount']}) FROM {s['table']}").fetchone()[0] or 0
        if s["year"]:
            yr = conn.execute(f"SELECT MIN({s['year']}), MAX({s['year']}) FROM {s['table']}").fetchone()
            year_range = f"{yr[0]}-{yr[1]}" if yr[0] else "-"
        elif src == "eura":
            yr = conn.execute("SELECT MIN(aloituspvm), MAX(paattymispvm) FROM eura_all").fetchone()
            year_range = f"{yr[0][:4]}-{yr[1][:4]}" if yr[0] else "-"
        else:
            year_range = "-"
        data.append({"source": src, "table": s["table"], "desc": s["desc"],
                      "rows": count, "total": total, "year_range": year_range})

    # Aggregate stats
    totals = {}
    for src, s in SOURCES.items():
        row = conn.execute(f"SELECT COUNT(*) as cnt, SUM({s['amount']}) as total FROM {s['table']}").fetchone()
        totals[src] = {"count": row["cnt"], "total": row["total"] or 0}

    cross = conn.execute(
        "SELECT COUNT(DISTINCT org_id) FROM org_mapping "
        "WHERE COALESCE(is_category, 0) = 0 AND org_id IN ("
        "SELECT org_id FROM org_mapping WHERE COALESCE(is_category, 0) = 0 "
        "GROUP BY org_id HAVING COUNT(DISTINCT source) > 1)"
    ).fetchone()[0]
    total_orgs = conn.execute("SELECT COUNT(DISTINCT org_id) FROM org_mapping WHERE COALESCE(is_category, 0) = 0").fetchone()[0]
    total_money = sum(v["total"] for v in totals.values())
    total_rows = sum(v["count"] for v in totals.values())

    if args.json:
        print(json.dumps({
            "sources": data,
            "totals": {"per_source": totals, "total_grants": total_rows,
                       "total_money": total_money, "mapped_orgs": total_orgs,
                       "cross_source_orgs": cross},
        }, ensure_ascii=False, indent=2))
        return

    print("Data sources\n")
    print_table(["Source", "Table", "Rows", "Total", "Years", "Description"],
                [[d["source"], d["table"], str(d["rows"]), fmt_money(d["total"]),
                  d["year_range"], d["desc"]] for d in data])

    print(f"\nAggregate statistics\n")
    print(f"  Total grant rows:    {total_rows:,}".replace(",", " "))
    print(f"  Total money:         {fmt_money(total_money)}")
    print(f"  Mapped orgs:         {total_orgs:,}".replace(",", " "))
    print(f"  Cross-source orgs:   {cross:,}".replace(",", " "))


def cmd_clusters(args, conn):
    cluster_id = args.cluster_id if args.cluster_id is not None else args.id
    if cluster_id is not None:
        cluster = conn.execute(
            "SELECT * FROM clusters WHERE id = ?", [cluster_id],
        ).fetchone()
        if not cluster:
            die(f"Cluster {cluster_id} not found")

        limit = args.limit or 50
        grants = conn.execute(
            "SELECT g.id, g.jarjesto, g.vuosi, g.myonnetty, e.oneliner "
            "FROM grant_clusters gc "
            "JOIN grants g ON g.id = gc.grant_id "
            "LEFT JOIN enrichments e ON e.grant_id = g.id "
            "WHERE gc.cluster_id = ? "
            "ORDER BY g.myonnetty DESC LIMIT ?",
            [cluster_id, limit],
        ).fetchall()

        if args.json:
            print(json.dumps({
                "id": cluster["id"], "name": cluster["name"],
                "size": cluster["size"],
                "avg_concreteness": round(cluster["avg_concreteness"], 2),
                "grants": [{"id": r["id"], "org": r["jarjesto"], "year": r["vuosi"],
                            "amount": r["myonnetty"], "oneliner": r["oneliner"]}
                           for r in grants],
            }, ensure_ascii=False, indent=2))
            return

        print(f"Cluster {cluster['id']}: {cluster['name']}")
        print(f"  Size: {cluster['size']}  Avg concreteness: {cluster['avg_concreteness']:.2f}\n")
        print_table(["#", "Year", "Amount", "Org", "Description"], [
            [str(i), str(r["vuosi"]), fmt_money(r["myonnetty"]),
             textwrap.shorten(r["jarjesto"] or "-", 30, placeholder="..."),
             textwrap.shorten(r["oneliner"] or "-", 50, placeholder="...")]
            for i, r in enumerate(grants, 1)
        ])
    else:
        limit = args.limit or 100
        rows = conn.execute(
            "SELECT id, name, size, avg_concreteness FROM clusters ORDER BY size DESC LIMIT ?",
            [limit],
        ).fetchall()

        if args.json:
            print(json.dumps([{"id": r["id"], "name": r["name"], "size": r["size"],
                               "avg_concreteness": round(r["avg_concreteness"], 2)}
                              for r in rows], ensure_ascii=False, indent=2))
            return

        print(f"Grant clusters (top {limit} by size)\n")
        print_table(["ID", "Name", "Size", "Avg Concreteness"], [
            [str(r["id"]), r["name"], str(r["size"]),
             f"{r['avg_concreteness']:.2f}"]
            for r in rows
        ])


def cmd_vsearch(args, conn):
    try:
        import numpy as np
    except ImportError:
        die("vsearch requires numpy. Install: uv pip install numpy")

    limit = args.limit
    dedup = not args.no_dedup

    data_dir = os.path.join(SCRIPT_DIR, "data")
    emb_files = EMB_FILES

    # Resolve item_id: either directly or via --text search
    if args.text:
        if args.item_id:
            die("Cannot use both item_id and --text at the same time")
        text_term = args.text
        words = text_term.split()
        # Preload embedded id-sets so the text seed only lands on grants that actually
        # have a vector (coverage is <100% for some sources, e.g. RAY 61%, FTS 40%).
        emb_id_sets = {}
        for src, (_, idf) in emb_files.items():
            if args.source and src != args.source:
                continue
            idp = os.path.join(data_dir, idf)
            if os.path.exists(idp):
                with open(idp) as f:
                    emb_id_sets[src] = set(json.load(f))
        best_match = None
        for src, sf in SEARCH_FIELDS.items():
            if src not in emb_files:
                continue
            if args.source and src != args.source:
                continue  # --source restricts the seed to that source (id stays consistent)
            all_cols = list(sf["text"]) + [sf["name"]]
            if len(words) > 1:
                word_clauses = []
                params = []
                for w in words:
                    col_conds = " OR ".join(f"{col} LIKE ? ESCAPE '\\'" for col in all_cols)
                    word_clauses.append(f"({col_conds})")
                    params.extend([f"%{_escape_like(w)}%" for _ in all_cols])
                where = " AND ".join(word_clauses)
            else:
                col_conds = " OR ".join(f"{col} LIKE ? ESCAPE '\\'" for col in all_cols)
                where = col_conds
                params = [f"%{_escape_like(text_term)}%" for _ in all_cols]
            cand_rows = conn.execute(
                f"SELECT {sf['id']} as item_id, {sf['name']} as org, {sf['amount']} as amount "
                f"FROM {sf['table']} WHERE {where} ORDER BY {sf['amount']} DESC LIMIT 25",
                params,
            ).fetchall()
            idset = emb_id_sets.get(src, set())
            row = next((r for r in cand_rows if r["item_id"] in idset), None)
            if row and (best_match is None or (row["amount"] or 0) > (best_match[2] or 0)):
                best_match = (src, row["item_id"], row["amount"], row["org"])
        if not best_match:
            die(f'No grants matching "{text_term}" found in indexed sources')
        text_src, item_id = best_match[0], str(best_match[1])
        seed_src = text_src   # the seed's real source; digit ids (RAY/VA/FTS) must NOT default to STEA
        if not args.json:
            print(f'--text "{text_term}" -> seed: [{text_src.upper()}] {item_id} ({best_match[3]})\n')
    elif args.item_id:
        item_id = args.item_id
        seed_src = None
    else:
        die("Either item_id or --text is required")

    # Auto-detect source: --source wins, then a --text seed's known source, then id-format guess.
    if args.source and args.source in emb_files:
        query_source_key = args.source
    elif seed_src:
        query_source_key = seed_src
    elif item_id.isdigit():
        query_source_key = "stea"
    elif item_id[:1].upper() in ("S", "A", "J"):
        query_source_key = "eura"
    else:
        query_source_key = "um"

    # Load query item's embedding
    npy_file, ids_file = emb_files[query_source_key]
    npy_path = os.path.join(data_dir, npy_file)
    ids_path = os.path.join(data_dir, ids_file)

    if not os.path.exists(npy_path) or not os.path.exists(ids_path):
        die(f"Embedding files not found for {query_source_key}: {npy_path}")

    embeddings = np.load(npy_path)
    with open(ids_path) as f:
        ids = json.load(f)

    # Find the query item index
    lookup_id = int(item_id) if query_source_key in ("stea", "ray", "va", "fts") else item_id
    try:
        idx = ids.index(lookup_id)
    except ValueError:
        die(f"Item {item_id} not found in {query_source_key} embeddings")

    query_vec = embeddings[idx]

    # Determine which sources to search
    cross_source_mode = False
    if args.source:
        search_sources = [args.source]
    else:
        # Exclude query's own source by default (cross-source search)
        search_sources = [s for s in emb_files.keys() if s != query_source_key]
        cross_source_mode = True

    # Compute cosine similarity across all target sources
    results = []
    for src in search_sources:
        nf, idf = emb_files[src]
        np_path = os.path.join(data_dir, nf)
        id_path = os.path.join(data_dir, idf)
        if not os.path.exists(np_path) or not os.path.exists(id_path):
            continue

        if src == query_source_key:
            src_emb = embeddings
            src_ids = ids
        else:
            src_emb = np.load(np_path)
            with open(id_path) as f:
                src_ids = json.load(f)

        # Cosine similarity: dot(q, E) / (|q| * |E|)
        norms = np.linalg.norm(src_emb, axis=1)
        q_norm = np.linalg.norm(query_vec)
        valid = (norms > 0) & (q_norm > 0)
        sims = np.zeros(len(src_ids))
        sims[valid] = src_emb[valid] @ query_vec / (norms[valid] * q_norm)

        for i in np.argsort(sims)[::-1][:limit * 3 + 5]:
            sid = src_ids[i]
            # Skip self
            if src == query_source_key and sid == lookup_id:
                continue
            results.append((float(sims[i]), src, sid))

    results.sort(key=lambda x: -x[0])

    # Fetch details from DB (before dedup, so we have org info)
    detailed = []
    for sim, src, sid in results:
        if src == "stea":
            row = conn.execute(
                "SELECT g.jarjesto as org, g.vuosi as year, g.myonnetty as amount, e.oneliner as desc "
                "FROM grants g LEFT JOIN enrichments e ON e.grant_id = g.id WHERE g.id = ?",
                [sid],
            ).fetchone()
        elif src == "ray":
            row = conn.execute(
                "SELECT g.jarjesto as org, g.vuosi as year, g.myonnetty as amount, "
                "COALESCE(e.oneliner, g.kayttotarkoitus) as desc "
                "FROM ray_grants g LEFT JOIN ray_enrichments e ON e.grant_id = g.id WHERE g.id = ?",
                [sid],
            ).fetchone()
        elif src == "eura":
            row = conn.execute(
                "SELECT toteuttaja as org, aloituspvm as year, myonnetty_eu_valtio as amount, nimi as desc "
                "FROM eura_all WHERE hankekoodi = ?",
                [sid],
            ).fetchone()
        elif src == "um":
            row = conn.execute(
                "SELECT organisation as org, year, amount, title as desc FROM um_grants WHERE activity_id = ?",
                [sid],
            ).fetchone()
        elif src == "va":
            row = conn.execute(
                "SELECT organisation as org, year, granted_eur as amount, "
                "grantor || ': ' || COALESCE(purpose, '') as desc FROM va_grants WHERE id = ?",
                [sid],
            ).fetchone()
        elif src == "fts":
            row = conn.execute(
                "SELECT organisation as org, year, amount, programme as desc FROM fts_grants WHERE id = ?",
                [sid],
            ).fetchone()
        else:
            row = None

        if row:
            yr = row["year"]
            if isinstance(yr, str) and len(yr) >= 4:
                yr = yr[:4]
            detailed.append({
                "source": src, "id": sid, "similarity": round(sim, 4),
                "amount": row["amount"], "year": yr,
                "org": row["org"], "desc": row["desc"],
            })

    # Third-sector filter
    if args.third_sector:
        detailed = [r for r in detailed
                    if not _third_sector_name_excluded(r["org"], conn, r["source"])]

    # Org deduplication: keep only highest-similarity result per (source, org)
    if dedup:
        seen_count = {}
        for r in detailed:
            key = (r["source"], r["org"])
            seen_count[key] = seen_count.get(key, 0) + 1
        deduped = []
        seen = set()
        for r in detailed:
            key = (r["source"], r["org"])
            if key not in seen:
                seen.add(key)
                extra = seen_count[key] - 1
                r["dedup_hidden"] = extra
                deduped.append(r)
        output = deduped[:limit]
    else:
        output = detailed[:limit]

    if args.json:
        for r in output:
            if "dedup_hidden" in r:
                r["similar_from_same_org"] = r.pop("dedup_hidden")
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    # Show what we're searching from
    if query_source_key == "stea":
        qrow = conn.execute(
            "SELECT g.jarjesto, e.oneliner FROM grants g LEFT JOIN enrichments e ON e.grant_id = g.id WHERE g.id = ?",
            [lookup_id],
        ).fetchone()
        q_desc = f"{qrow['jarjesto']}: {qrow['oneliner']}" if qrow else item_id
    elif query_source_key == "ray":
        qrow = conn.execute(
            "SELECT g.jarjesto, COALESCE(e.oneliner, g.kayttotarkoitus) as oneliner "
            "FROM ray_grants g LEFT JOIN ray_enrichments e ON e.grant_id = g.id WHERE g.id = ?",
            [lookup_id],
        ).fetchone()
        q_desc = f"{qrow['jarjesto']}: {qrow['oneliner']}" if qrow else item_id
    elif query_source_key == "eura":
        qrow = conn.execute("SELECT nimi, toteuttaja FROM eura_all WHERE hankekoodi = ?", [item_id]).fetchone()
        q_desc = f"{qrow['toteuttaja']}: {qrow['nimi']}" if qrow else item_id
    elif query_source_key == "va":
        qrow = conn.execute("SELECT organisation, purpose FROM va_grants WHERE id = ?", [lookup_id]).fetchone()
        q_desc = f"{qrow['organisation']}: {qrow['purpose']}" if qrow else item_id
    elif query_source_key == "fts":
        qrow = conn.execute("SELECT organisation, programme FROM fts_grants WHERE id = ?", [lookup_id]).fetchone()
        q_desc = f"{qrow['organisation']}: {qrow['programme']}" if qrow else item_id
    else:
        qrow = conn.execute("SELECT title, organisation FROM um_grants WHERE activity_id = ?", [item_id]).fetchone()
        q_desc = f"{qrow['organisation']}: {qrow['title']}" if qrow else item_id

    print(f"Similar to [{query_source_key.upper()}] {item_id}")
    print(f"  {textwrap.shorten(q_desc, 80, placeholder='...')}")
    if cross_source_mode:
        print(f"  Cross-source results (use --source {query_source_key} to search within same source)")
    print()
    def _org_label(r):
        org = r["org"] or "-"
        hidden = r.get("dedup_hidden", 0)
        if hidden > 0:
            org = f"{org} (+{hidden})"
        return textwrap.shorten(org, 30, placeholder="...")

    if not output:
        print("No results found.", file=sys.stderr)
    else:
        print_table(["#", "Source", "Sim", "Amount", "Year", "Org", "Description"], [
            [str(i),
             r["source"].upper(),
             f"{r['similarity']:.3f}",
             fmt_money(r["amount"]),
             str(r["year"] or "-"),
             _org_label(r),
             textwrap.shorten(r["desc"] or "-", 40, placeholder="...")]
            for i, r in enumerate(output, 1)
        ])
        if output[0]["similarity"] < 0.3:
            print(f"\nNote: Low similarity scores — results may not be meaningfully related.", file=sys.stderr)

    # Embedding coverage info
    coverage_parts = []
    all_sources_ordered = SOURCE_ORDER
    for src in all_sources_ordered:
        if src not in emb_files:
            tbl = SOURCES[src]["table"]
            total = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            coverage_parts.append(f"{src.upper()}: no index ({_fmt_num(total)} rows)")
            continue
        nf, idf = emb_files[src]
        id_path = os.path.join(data_dir, idf)
        if not os.path.exists(id_path):
            coverage_parts.append(f"{src.upper()}: no index")
            continue
        with open(id_path) as f:
            emb_count = len(json.load(f))
        tbl = SOURCES[src]["table"]
        total = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        if total > 0:
            pct = emb_count / total * 100
            coverage_parts.append(f"{src.upper()} {pct:.1f}% ({_fmt_num(emb_count)}/{_fmt_num(total)})")
        else:
            coverage_parts.append(f"{src.upper()} 0/0")
    print(f"\nCoverage: {' | '.join(coverage_parts)}")


SEARCH_FIELDS = {
    "stea":     {"table": "grants",         "text": ["kayttotarkoitus"],               "name": "jarjesto",    "amount": "myonnetty",          "year": "vuosi", "id": "id"},
    "ray":      {"table": "ray_grants",     "text": ["kayttotarkoitus", "avustuslaji"], "name": "jarjesto",    "amount": "myonnetty",          "year": "vuosi", "id": "id"},
    "eura":     {"table": "eura_all",       "text": ["nimi", "tiivistelma"],            "name": "toteuttaja",  "amount": "myonnetty_eu_valtio","year": "aloituspvm", "id": "hankekoodi"},
    "um":       {"table": "um_grants",      "text": ["title", "description"],           "name": "organisation","amount": "amount",             "year": "year", "id": "activity_id"},
    "helsinki":	{"table": "helsinki_grants", "text": ["hakemustyyppi", "avustuslaji"],   "name": "hakija",      "amount": "myonnetty",          "year": "vuosi", "id": "id"},
    "va":       {"table": "va_grants",      "text": ["purpose", "call_name"],            "name": "organisation","amount": "granted_eur",        "year": "year",  "id": "id"},
    "fts":      {"table": "fts_grants",     "text": ["programme"],                       "name": "organisation","amount": "amount",             "year": "year",  "id": "id"},
}


def cmd_search(args, conn):
    term = args.term
    if not term.strip():
        die("Search term cannot be empty")
    limit = args.limit
    source_filter = args.source
    since, until = _resolve_year_bounds(args)
    yr_label = _year_filter_label(args)
    third_sector = args.third_sector
    results = []

    for src, sf in SEARCH_FIELDS.items():
        if source_filter and src != source_filter:
            continue
        search_cols = sf["text"] + [sf["name"]]
        conditions = " OR ".join(f"{col} LIKE ? ESCAPE '\\'" for col in search_cols)
        params = [f"%{_escape_like(term)}%" for _ in search_cols]
        yr_clause, yr_params = _search_year_where(src, since, until)
        where = f"({conditions})"
        if yr_clause:
            where += f" AND ({yr_clause})"
            params.extend(yr_params)
        # Third-sector filter via org_mapping
        src_for_mapping = src
        ts_clause, ts_params = _third_sector_sql_for_source(src_for_mapping, third_sector)
        if ts_clause:
            where += f" AND ({ts_clause})"
            params.extend(ts_params)
        fetch_limit = limit * 3 if third_sector else limit
        rows = conn.execute(
            f"SELECT {sf['id']} as item_id, {sf['name']} as org, {sf['amount']} as amount, "
            f"{sf['year']} as yr, {', '.join(sf['text'])} FROM {sf['table']} "
            f"WHERE {where} ORDER BY {sf['amount']} DESC LIMIT ?",
            params + [fetch_limit],
        ).fetchall()
        for r in rows:
            if third_sector and _is_non_third_sector_name(r["org"]):
                continue
            txt = " | ".join(filter(None, [r[col] for col in sf["text"]]))
            yr = r["yr"]
            if isinstance(yr, str) and len(yr) >= 4:
                yr = yr[:4]
            results.append({
                "source": src, "id": r["item_id"], "org": r["org"],
                "amount": r["amount"], "year": yr,
                "text": txt,
            })

    results.sort(key=lambda r: -(r["amount"] or 0))
    results = results[:limit]

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
        return

    if not results:
        print(f'No results for "{term}"')
        return

    title = f'Search: "{term}" ({len(results)} results)'
    if yr_label:
        title += f" [{yr_label}]"
    if third_sector:
        title += " [third-sector]"
    print(f'{title}\n')
    print_table(["Source", "ID", "Year", "Amount", "Org", "Text"], [
        [r["source"].upper(), str(r["id"]), str(r["year"] or "-"), fmt_money(r["amount"]),
         textwrap.shorten(r["org"] or "-", 30, placeholder="..."),
         textwrap.shorten(r["text"] or "-", 45, placeholder="...")]
        for r in results
    ])

    sources_found = set(r["source"] for r in results)
    if not source_filter:
        if "um" not in sources_found:
            print(f'\nHint: UM data is in English. Try also: search "{term}" in English')
        if not (sources_found & {"stea", "ray", "eura"}):
            print(f'\nHint: STEA/RAY/EURA data is in Finnish. Try also: search "{term}" in Finnish')


def _year_filter_label(args):
    """Build a human-readable label for active year filters."""
    year = getattr(args, "year", None)
    since = getattr(args, "since", None)
    until = getattr(args, "until", None)
    if year:
        return str(year)
    if since and until:
        return f"{since}-{until}"
    if since:
        return f"{since}-"
    if until:
        return f"-{until}"
    return None


def _resolve_year_bounds(args):
    """Return (since, until) from args, handling --year as shorthand."""
    year = getattr(args, "year", None)
    since = getattr(args, "since", None)
    until = getattr(args, "until", None)
    if year:
        return year, year
    return since, until


def _year_where(source, since, until):
    """Build a SQL WHERE fragment and params for year filtering.

    Returns (clause_str, params_list).  clause_str is empty when no filter.
    For eura, uses CAST(substr(aloituspvm, 1, 4) AS INTEGER).
    For others, uses the year column from SOURCES.
    """
    if since is None and until is None:
        return "", []
    s = SOURCES[source]
    if source == "eura":
        year_expr = "CAST(substr(aloituspvm, 1, 4) AS INTEGER)"
    elif s["year"]:
        year_expr = s["year"]
    else:
        return "", []  # no year column, can't filter

    parts, params = [], []
    if since is not None:
        parts.append(f"{year_expr} >= ?")
        params.append(since)
    if until is not None:
        parts.append(f"{year_expr} <= ?")
        params.append(until)
    return " AND ".join(parts), params


def _search_year_where(src, since, until):
    """Year WHERE fragment using SEARCH_FIELDS year column (for cmd_search)."""
    if since is None and until is None:
        return "", []
    if src == "eura":
        year_expr = "CAST(substr(aloituspvm, 1, 4) AS INTEGER)"
    else:
        sf = SEARCH_FIELDS[src]
        year_expr = sf["year"]
    parts, params = [], []
    if since is not None:
        parts.append(f"{year_expr} >= ?")
        params.append(since)
    if until is not None:
        parts.append(f"{year_expr} <= ?")
        params.append(until)
    return " AND ".join(parts), params


def _add_year_args(parser):
    """Add --year, --since, --until arguments to a subparser."""
    parser.add_argument("--year", type=int, metavar="YYYY", help="Filter to a specific year")
    parser.add_argument("--since", type=int, metavar="YYYY", help="Filter from this year onward (inclusive)")
    parser.add_argument("--until", type=int, metavar="YYYY", help="Filter up to this year (inclusive)")


def _get_year(source, row):
    s = SOURCES[source]
    if s["year"]:
        return row[s["year"]]
    if source == "eura" and row["aloituspvm"]:
        return int(row["aloituspvm"][:4])
    return None


def cmd_profile(args, conn):
    name = args.name
    third_sector = args.third_sector
    org_groups = resolve_org(conn, name)

    # Third-sector filter
    if third_sector and org_groups:
        org_groups = [g for g in org_groups
                      if g["org_id"] is None or not _third_sector_org_excluded(conn, g["org_id"])]

    if not org_groups:
        die(f'No org found matching "{name}"')

    if args.merge and len(org_groups) > 1:
        merged = {"org_id": None, "match": name + " (merged)", "sources": {}}
        for g in org_groups:
            for src, names in g["sources"].items():
                merged["sources"].setdefault(src, set()).update(names)
        org_groups = [merged]

    json_results = []
    for gi, g in enumerate(org_groups):
        matrix = {}
        active_sources = []
        for src in SOURCE_ORDER:
            if src not in g["sources"]:
                continue
            rows = query_source(conn, src, g["sources"][src])
            if not rows:
                continue
            active_sources.append(src)
            for r in rows:
                yr = _get_year(src, r)
                if yr is None:
                    continue
                yr = int(yr) if not isinstance(yr, int) else yr
                matrix.setdefault(yr, {})[src] = matrix.get(yr, {}).get(src, 0) + (r[SOURCES[src]["amount"]] or 0)

        if not matrix:
            continue

        if args.json:
            json_results.append({
                "org_id": g["org_id"], "match": g["match"],
                "sources": active_sources,
                "years": {yr: data for yr, data in sorted(matrix.items())},
            })
            continue

        if len(org_groups) > 1:
            print(f'--- {g["match"]} (org_id {g["org_id"]}) ---\n')
        else:
            print(f'Profile: {g["match"]}\n')

        headers = ["Year"] + [s.upper() for s in active_sources] + ["Total"]
        rows_out = []
        col_totals = {s: 0 for s in active_sources}
        for yr in sorted(matrix.keys()):
            row = [str(yr)]
            yr_total = 0
            for src in active_sources:
                v = matrix[yr].get(src, None)
                col_totals[src] += v or 0
                yr_total += v or 0
                row.append(fmt_money(v) if v is not None else "-")
            row.append(fmt_money(yr_total))
            rows_out.append(row)
        totals_row = ["TOTAL"] + [fmt_money(col_totals[s]) for s in active_sources] + [fmt_money(sum(col_totals.values()))]
        rows_out.append(totals_row)
        print_table(headers, rows_out)

        if gi < len(org_groups) - 1:
            print()

    if args.json and json_results:
        if len(json_results) == 1:
            print(json.dumps(json_results[0], ensure_ascii=False, indent=2))
        else:
            print(json.dumps(json_results, ensure_ascii=False, indent=2))
        return


def cmd_sql(args, conn):
    query = args.query
    lower = query.strip().lower()
    if not (lower.startswith("select") or lower.startswith("with")):
        die("Only SELECT queries are allowed (must start with SELECT or WITH)")
    if "LIMIT" not in query.upper():
        effective_limit = args.limit if args.limit else 1000
        query = query.rstrip().rstrip(";") + f" LIMIT {effective_limit}"
    elif args.limit:
        query = query.rstrip().rstrip(";") + f" LIMIT {args.limit}"

    try:
        cursor = conn.execute(query)
    except sqlite3.Error as e:
        die(str(e))

    rows = cursor.fetchall()
    if not rows:
        print("(no results)")
        return

    headers = [desc[0] for desc in cursor.description]

    if args.csv_out:
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(headers)
        for r in rows:
            writer.writerow(list(r))
        print(out.getvalue(), end="")
        return

    if args.json:
        print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2, default=str))
        return

    print_table(headers, [[str(v) if v is not None else "NULL" for v in list(r)] for r in rows])


# Sectors the website's third-sector ALLOWLIST keeps (vs the CLI's wider denylist). Used by
# the contracts/lobbying commands, whose tables carry a denormalised `sector` column and
# whose website views filter to this allowlist.
THIRD_SECTOR_ALLOWLIST = ("association", "foundation", "cooperative", "church")

# Public-contract value is populated ONLY for sole-winner awards; a shared-win contract's
# value is not attributable to one org. A few corrupt rows have absurd values -> cap.
_CONTRACT_VALUE_CAP = 1e9


def _contracts_summary(conn, oids, top_n=5):
    """Summarise HILMA public-contract wins for a set of org_ids. Attributable euros count
    sole-winner contracts only (capped); shared wins are returned as a count. Grants and
    contract value are DIFFERENT money types and are never added together."""
    if not oids:
        return {"win_count": 0, "attributable_total": 0, "shared_win_count": 0, "top": []}
    ph = ",".join("?" for _ in oids)
    rows = conn.execute(
        f"SELECT buyer, title, value, sole_winner, is_suorahankinta, date_published "
        f"FROM org_public_contracts WHERE org_id IN ({ph})", list(oids)
    ).fetchall()
    sole = [r for r in rows if r["sole_winner"] and r["value"] and 0 <= r["value"] <= _CONTRACT_VALUE_CAP]
    sole.sort(key=lambda r: -(r["value"] or 0))
    return {
        "win_count": len(rows),
        "attributable_total": sum(r["value"] for r in sole),
        "shared_win_count": sum(1 for r in rows if not r["sole_winner"]),
        "top": [{"buyer": r["buyer"], "title": r["title"], "value": r["value"],
                 "year": (r["date_published"] or "")[:4]} for r in sole[:top_n]],
    }


def cmd_families(args, conn):
    """Federated org families (emojärjestöt): keskusjärjestö + member associations.

    LIST mode (no arg) ranks the 20 curated families by whole-movement total. DRILL mode
    (a keyword or name) breaks a family down by member org and gives the movement total —
    the canonical way to see funding for movements whose money is spread across dozens of
    local associations (e.g. omaishoitajat)."""
    name = getattr(args, "name", None)

    if not name:
        rows = conn.execute(
            "SELECT keyword, label, member_count, source_count, total_eur, top_pct, concentration "
            "FROM org_families ORDER BY total_eur DESC"
        ).fetchall()
        if args.json:
            print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2, default=str))
            return
        print("Federated families (emojärjestöt) — whole-movement totals\n")
        print_table(["#", "Family", "Members", "Srcs", "Total", "Top%", "Concentration"], [
            [str(i), textwrap.shorten(r["label"], 36, placeholder="..."), str(r["member_count"]),
             str(r["source_count"]), fmt_money(r["total_eur"]),
             f'{r["top_pct"]:.0f}' if r["top_pct"] is not None else "-", r["concentration"] or "-"]
            for i, r in enumerate(rows, 1)
        ])
        print(f"\n  {len(rows)} families.  Drill in:  ralssi.py families <keyword|name>")
        print("  Concentration: keskitetty = money concentrated in the central org; "
              "hajautunut = spread across local associations.")
        return

    fam = conn.execute(
        "SELECT * FROM org_families WHERE keyword = ? OR LOWER(label) LIKE LOWER(?) "
        "ORDER BY total_eur DESC LIMIT 1", [name, f"%{name}%"]
    ).fetchone()
    if not fam:
        if args.json:
            print(json.dumps({"error": f"No family matching '{name}'", "query": name}, ensure_ascii=False))
            return
        die(f'No family matching "{name}". List all families: ralssi.py families')
    keyword = fam["keyword"]
    member_yts = [r["y_tunnus"] for r in conn.execute(
        "SELECT y_tunnus FROM org_family_members WHERE keyword = ?", [keyword])]

    yt_to_oids = {}
    if member_yts:
        ph = ",".join("?" for _ in member_yts)
        for r in conn.execute(
            f"SELECT DISTINCT y_tunnus, org_id FROM org_mapping "
            f"WHERE y_tunnus IN ({ph}) AND COALESCE(is_category, 0) = 0", member_yts
        ):
            yt_to_oids.setdefault(r["y_tunnus"], set()).add(r["org_id"])

    agg = _org_id_aggregate(conn)   # org_id -> all-source totals (sums all name variants)
    members, contributing = [], set()
    for yt in member_yts:
        total, srcs, disp, best = 0, set(), None, -1
        for oid in yt_to_oids.get(yt, set()):
            d = agg.get(oid)
            if not d:
                continue
            total += d["total"]
            srcs |= set(d["source_totals"])
            if d["total"] > best:
                best, disp = d["total"], d["display"]
        contributing |= srcs
        members.append({"y_tunnus": yt, "name": disp or yt, "total": total, "sources": sorted(srcs)})
    members.sort(key=lambda m: -m["total"])
    movement_total = sum(m["total"] for m in members)

    if args.json:
        print(json.dumps({
            "keyword": keyword, "label": fam["label"], "description": fam["description"],
            "concentration": fam["concentration"], "top_pct": fam["top_pct"],
            "movement_total": movement_total, "member_count": len(members),
            "sources": sorted(contributing), "source_url": fam["source_url"],
            "verified_on": fam["verified_on"], "members": members,
        }, ensure_ascii=False, indent=2, default=str))
        return

    top_pct = fam["top_pct"]
    head = f'Family: {fam["label"]}'
    if fam["concentration"]:
        head += f'  ({fam["concentration"]}'
        head += f' — top org = {top_pct:.0f}% of movement)' if top_pct is not None else ')'
    print(head + "\n")
    print(f'  {len(members)} members across {len(contributing)} sources.  '
          f'Movement total: {fmt_money(movement_total)}')
    if fam["verified_on"]:
        print(f'  Members verified from official member lists / websites on {fam["verified_on"]}.')
    _ray_caveat(contributing, False)
    print()
    limit = getattr(args, "limit", None)
    shown = members[:limit] if limit else members
    print_table(["Member", "Y-tunnus", "Total", "Sources"], [
        [textwrap.shorten(m["name"] or "-", 44, placeholder="..."), m["y_tunnus"] or "-",
         fmt_money(m["total"]), ",".join(m["sources"])]
        for m in shown
    ])
    if limit and len(members) > limit:
        print(f"  ... ({len(members) - limit} more members; raise --limit to show)")


def _contract_org_ids(conn, name):
    """Resolve a name to the set of org_ids (third-sector-aware via resolve_org)."""
    oids = set()
    for g in resolve_org(conn, name):
        if g["org_id"] is not None:
            oids.add(g["org_id"])
    return oids


def cmd_contracts(args, conn):
    """Public procurement wins (HILMA, org_public_contracts) — a money stream separate from
    grants. Euro totals count ONLY sole-winner awards (a shared-win contract's value is not
    attributable to one org); shared wins are reported as a count."""
    third_sector = args.third_sector

    if getattr(args, "buyer", None):
        # Buyer view: what a contracting authority (e.g. Maahanmuuttovirasto) procured, and
        # from whom. No third-sector filter here — the point is the buyer's full procurement.
        rows = conn.execute(
            "SELECT winner_name, title, value, sole_winner, is_suorahankinta, date_published "
            "FROM org_public_contracts WHERE buyer LIKE ? ESCAPE '\\' "
            f"ORDER BY (CASE WHEN sole_winner=1 AND value BETWEEN 0 AND {_CONTRACT_VALUE_CAP} THEN value ELSE 0 END) DESC",
            [f"%{_escape_like(args.buyer)}%"]
        ).fetchall()
        attributable = sum(r["value"] for r in rows
                           if r["sole_winner"] and r["value"] and 0 <= r["value"] <= _CONTRACT_VALUE_CAP)
        shared = sum(1 for r in rows if not r["sole_winner"])
        limit = getattr(args, "limit", None) or 50
        if args.json:
            print(json.dumps({
                "buyer_query": args.buyer, "win_count": len(rows),
                "attributable_total": attributable, "shared_win_count": shared,
                "contracts": [{"winner": r["winner_name"], "title": r["title"], "value": r["value"],
                               "sole_winner": bool(r["sole_winner"]),
                               "is_suorahankinta": bool(r["is_suorahankinta"]),
                               "year": (r["date_published"] or "")[:4]} for r in rows[:limit]],
            }, ensure_ascii=False, indent=2, default=str))
            return
        if not rows:
            die(f'No public contracts with a buyer matching "{args.buyer}".')
        print(f'Public contracts by buyer matching "{args.buyer}"\n')
        print(f"  {len(rows)} contracts | attributable (sole-winner) total: {fmt_money(attributable)} "
              f"| +{shared} shared-win (value not attributable)\n")
        print_table(["Year", "Value", "Sole", "Suora", "Winner", "Title"], [
            [(r["date_published"] or "-")[:4],
             fmt_money(r["value"]) if (r["sole_winner"] and r["value"] and r["value"] <= _CONTRACT_VALUE_CAP) else "-",
             "yes" if r["sole_winner"] else "no", "yes" if r["is_suorahankinta"] else "no",
             textwrap.shorten(r["winner_name"] or "-", 28, placeholder="..."),
             textwrap.shorten(r["title"] or "-", 36, placeholder="...")]
            for r in rows[:limit]
        ])
        if len(rows) > limit:
            print(f"  ... ({len(rows) - limit} more; raise --limit)")
        return

    if args.top is not None:
        n = args.top or 20
        where, params = ["1=1"], []
        if args.suorahankinta:
            where.append("is_suorahankinta = 1")
        if third_sector:
            ph = ",".join("?" for _ in THIRD_SECTOR_ALLOWLIST)
            where.append(f"sector IN ({ph})")
            params += list(THIRD_SECTOR_ALLOWLIST)
        wsql = " AND ".join(where)
        rows = conn.execute(
            f"SELECT org_id, MAX(winner_name) as name, "
            f"SUM(CASE WHEN sole_winner = 1 AND value BETWEEN 0 AND {_CONTRACT_VALUE_CAP} THEN value ELSE 0 END) as attributable, "
            f"COUNT(*) as n, SUM(CASE WHEN sole_winner = 1 THEN 1 ELSE 0 END) as sole_n "
            f"FROM org_public_contracts WHERE {wsql} AND org_id IS NOT NULL "
            f"GROUP BY org_id ORDER BY attributable DESC LIMIT ?", params + [n]
        ).fetchall()
        if args.json:
            print(json.dumps([{"name": r["name"], "attributable_value": r["attributable"],
                               "contracts": r["n"], "sole_winner_contracts": r["sole_n"]}
                              for r in rows], ensure_ascii=False, indent=2))
            return
        title = "Top public-contract winners (HILMA)"
        if args.suorahankinta:
            title += " — direct awards (suorahankinta)"
        if third_sector:
            title += " [third-sector]"
        print(title + "\n")
        print_table(["#", "Org", "Contracts", "Attributable value"], [
            [str(i), textwrap.shorten(r["name"] or "-", 45, placeholder="..."),
             str(r["n"]), fmt_money(r["attributable"])]
            for i, r in enumerate(rows, 1)
        ])
        print("\n  'Attributable value' sums sole-winner contracts only; shared-win contract "
              "value is not attributable to one org.")
        return

    if not args.name:
        die("Usage: contracts <org-name>  |  contracts --top [N]  |  contracts --buyer <name>")
    oids = _contract_org_ids(conn, args.name)
    if not oids:
        die(f'No mapped org_id for "{args.name}" (contracts are keyed by org_id; try the exact name).')
    ph = ",".join("?" for _ in oids)
    limit = getattr(args, "limit", None) or 50

    def fetch(side_col):
        rows = conn.execute(
            f"SELECT winner_name, buyer, title, value, sole_winner, n_winners, is_suorahankinta, "
            f"date_published FROM org_public_contracts WHERE {side_col} IN ({ph}) "
            f"ORDER BY is_suorahankinta DESC, (CASE WHEN sole_winner = 1 THEN value ELSE 0 END) DESC",
            list(oids)).fetchall()
        attr = sum(r["value"] for r in rows
                   if r["sole_winner"] and r["value"] and 0 <= r["value"] <= _CONTRACT_VALUE_CAP)
        shared = sum(1 for r in rows if not r["sole_winner"])
        return rows, attr, shared

    won, won_attr, won_shared = fetch("org_id")            # org as winner (myynyt)
    bought, buy_attr, buy_shared = fetch("buyer_org_id")    # org as buyer (hankkinut)

    def ser(rows, other):
        return [{other: (r["buyer"] if other == "buyer" else r["winner_name"]),
                 "title": r["title"], "value": r["value"], "sole_winner": bool(r["sole_winner"]),
                 "n_winners": r["n_winners"], "is_suorahankinta": bool(r["is_suorahankinta"]),
                 "year": (r["date_published"] or "")[:4]} for r in rows[:limit]]
    if args.json:
        print(json.dumps({
            "query": args.name, "org_ids": sorted(oids),
            "as_winner": {"contract_count": len(won), "attributable_total": won_attr,
                          "shared_win_count": won_shared, "contracts": ser(won, "buyer")},
            "as_buyer": {"contract_count": len(bought), "attributable_total": buy_attr,
                         "shared_count": buy_shared, "contracts": ser(bought, "winner_name")},
        }, ensure_ascii=False, indent=2, default=str))
        return

    print(f'Public procurement contracts (HILMA) — "{args.name}"\n')

    def render(label, rows, attr, shared, other_label, other_field):
        if not rows:
            return
        print(f"  {label}: {len(rows)} sopimusta | attributable {fmt_money(attr)} "
              f"| +{shared} jaettua (arvoa ei kohdisteta)")
        print_table(["Year", "Value", "Sole", "Suora", other_label, "Title"], [
            [(r["date_published"] or "-")[:4],
             fmt_money(r["value"]) if (r["sole_winner"] and r["value"] and r["value"] <= _CONTRACT_VALUE_CAP) else "-",
             "yes" if r["sole_winner"] else "no", "yes" if r["is_suorahankinta"] else "no",
             textwrap.shorten((r["buyer"] if other_field == "buyer" else r["winner_name"]) or "-", 24, placeholder="..."),
             textwrap.shorten(r["title"] or "-", 38, placeholder="...")]
            for r in rows[:limit]])
        if len(rows) > limit:
            print(f"    ... ({len(rows) - limit} lisää; nosta --limit)")
        print()

    render("Voittajana (myynyt)", won, won_attr, won_shared, "Buyer", "buyer")
    render("Ostajana (hankkinut)", bought, buy_attr, buy_shared, "Winner", "winner_name")
    if not won and not bought:
        print("  (ei hankintasopimuksia)")


def cmd_lobbying(args, conn):
    """Lobbying register (lobbying_orgs/lobbying_topics) and political party ties
    (political_connections). Note: the registry skews to companies/industry bodies; most
    third-sector orgs have no entries."""
    third_sector = args.third_sector
    allow = THIRD_SECTOR_ALLOWLIST

    if args.party:
        rows = conn.execute(
            "SELECT org_name, connection_count, categories, sector, total_grants_eur "
            "FROM political_connections WHERE UPPER(party) = UPPER(?) ORDER BY connection_count DESC",
            [args.party]
        ).fetchall()
        if args.json:
            print(json.dumps([{"org": r["org_name"], "connections": r["connection_count"],
                               "categories": r["categories"], "sector": r["sector"],
                               "grants_eur": r["total_grants_eur"]} for r in rows],
                             ensure_ascii=False, indent=2, default=str))
            return
        print(f"Orgs connected to {args.party.upper()} (political_connections)\n")
        print_table(["Org", "Conns", "Sector", "Categories"], [
            [textwrap.shorten(r["org_name"] or "-", 40, placeholder="..."),
             str(r["connection_count"]), r["sector"] or "-",
             textwrap.shorten((r["categories"] or "").strip("[]").replace('"', ""), 30, placeholder="...")]
            for r in rows
        ])
        return

    if args.top is not None:
        n = args.top or 20
        where, params = ["1=1"], []
        if third_sector:
            ph = ",".join("?" for _ in allow)
            where.append(f"sector IN ({ph})")
            params += list(allow)
        rows = conn.execute(
            f"SELECT org_name, sector, topic_count, contact_count, total_grants_eur "
            f"FROM lobbying_orgs WHERE {' AND '.join(where)} ORDER BY topic_count DESC LIMIT ?",
            params + [n]
        ).fetchall()
        if args.json:
            print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2, default=str))
            return
        title = "Most active lobbyists (by registered topics)"
        if third_sector:
            title += " [third-sector]"
        print(title + "\n")
        print_table(["#", "Org", "Topics", "Contacts", "Grants"], [
            [str(i), textwrap.shorten(r["org_name"] or "-", 40, placeholder="..."),
             str(r["topic_count"]), str(r["contact_count"]), fmt_money(r["total_grants_eur"])]
            for i, r in enumerate(rows, 1)
        ])
        return

    if not args.name:
        die("Usage: lobbying <org-name>  |  lobbying --top [N]  |  lobbying --party <KOK|SDP|...>")
    # Resolve to y_tunnus + org_id set.
    oids = _contract_org_ids(conn, args.name)
    yts = set()
    if oids:
        ph = ",".join("?" for _ in oids)
        for r in conn.execute(
            f"SELECT DISTINCT y_tunnus FROM org_mapping WHERE org_id IN ({ph}) AND y_tunnus IS NOT NULL AND y_tunnus != ''",
            list(oids)):
            yts.add(r["y_tunnus"])
    lob = None
    if oids:
        ph = ",".join("?" for _ in oids)
        lob = conn.execute(
            f"SELECT org_name, main_industry, contact_count, topic_count, total_grants_eur "
            f"FROM lobbying_orgs WHERE org_id IN ({ph}) LIMIT 1", list(oids)).fetchone()
    topics, conns = [], []
    if yts:
        ph = ",".join("?" for _ in yts)
        topics = conn.execute(
            f"SELECT DISTINCT topic_description, activity_type, activity_date FROM lobbying_topics "
            f"WHERE y_tunnus IN ({ph}) ORDER BY activity_date DESC LIMIT ?", list(yts) + [getattr(args, "limit", None) or 10]
        ).fetchall()
        conns = conn.execute(
            f"SELECT party, connection_count, categories FROM political_connections "
            f"WHERE y_tunnus IN ({ph}) ORDER BY connection_count DESC", list(yts)).fetchall()
    if args.json:
        print(json.dumps({
            "query": args.name,
            "lobbying": (dict(lob) if lob else None),
            "recent_topics": [{"date": t["activity_date"], "type": t["activity_type"],
                               "topic": t["topic_description"]} for t in topics],
            "party_connections": [{"party": c["party"], "connections": c["connection_count"],
                                   "categories": c["categories"]} for c in conns],
        }, ensure_ascii=False, indent=2, default=str))
        return
    print(f'Lobbying & political ties — "{args.name}"\n')
    if lob:
        print(f"  Registry: {lob['topic_count']} topics, {lob['contact_count']} contacts.  "
              f"Main industry: {lob['main_industry'] or '-'}")
        if lob["total_grants_eur"]:
            print(f"  Grants (precomputed): {fmt_money(lob['total_grants_eur'])}")
    else:
        print("  Not in the lobbying register.")
    if topics:
        print("  Recent lobbying topics:")
        for t in topics:
            print(f"    {(t['activity_date'] or '-'):12s} {(t['activity_type'] or '-'):8s} "
                  f"{textwrap.shorten(t['topic_description'] or '-', 70, placeholder='...')}")
    if conns:
        print("  Party connections:")
        for c in conns:
            print(f"    {c['party']:6s} {c['connection_count']} "
                  f"({(c['categories'] or '').strip('[]').replace(chr(34), '')})")
    elif lob or topics:
        print("  Party connections: (none)")


RELEASE_URL = "https://github.com/ekipalen/ralssi/releases/download/v2.4/ralssi-data.zip"


def cmd_setup(args):
    data_dir = os.path.join(SCRIPT_DIR, "data")
    db_path = os.path.join(data_dir, "funding.db")
    if os.path.exists(db_path) and not args.force:
        print(f"Data already exists: {db_path}")
        print("Use --force to re-download.")
        return

    import zipfile
    import tempfile

    zip_path = os.path.join(tempfile.gettempdir(), "ralssi-data.zip")
    print(f"Downloading data from GitHub release...")

    try:
        req = urllib.request.Request(RELEASE_URL)
        with urllib.request.urlopen(req) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            with open(zip_path, "wb") as f:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = downloaded * 100 // total
                        print(f"\r  {downloaded // (1024*1024)} / {total // (1024*1024)} MB ({pct}%)", end="", flush=True)
            if total:
                print()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            die("Release not found. For private repos, download manually from:\n"
                f"  {RELEASE_URL}\n"
                f"and unzip into {SCRIPT_DIR}/")
        die(f"Download failed: {e}")

    print(f"Extracting to {SCRIPT_DIR}/...")
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(SCRIPT_DIR)
    os.remove(zip_path)

    if os.path.exists(db_path):
        print(f"Done! {db_path} ready.")
        print("Try: uv run ralssi.py sources")
    else:
        die("Extraction completed but funding.db not found. Check the zip structure.")


def main():
    parser = argparse.ArgumentParser(prog="ralssi", description="Finnish funding data explorer")
    parser.add_argument("--third-sector", action="store_true", default=True, dest="third_sector",
                        help="Filter to third-sector orgs only (default: on)")
    parser.add_argument("--no-third-sector", action="store_false", dest="third_sector",
                        help="Disable third-sector filter (show all orgs)")
    sub = parser.add_subparsers(dest="command")

    def _json(p):
        p.add_argument("--json", action="store_true", help="JSON output")
        return p

    p = _json(sub.add_parser("org", help="Cross-source org search"))
    p.add_argument("name", help="Organisation name (partial match)")
    p.add_argument("--merge", action="store_true", help="Merge all matching org groups into one view")
    p.add_argument("--detail", action="store_true", help="Show individual grants instead of just summary")
    p.add_argument("--source", choices=list(SOURCES.keys()), help="Limit detail to one source")
    p.add_argument("--limit", type=int, default=50, help="Max grants per source in detail view (default: 50)")
    p.add_argument("--contracts", action="store_true", help="Also show HILMA public-procurement wins (separate from grants)")

    p = _json(sub.add_parser("hunters", help="Find orgs in multiple sources"))
    p.add_argument("--min", type=int, default=2, help="Min sources (default: 2)")
    p.add_argument("--limit", type=int, default=20, help="Max results (default: 20)")
    p.add_argument("--sort", choices=["total", "sources"], default="total")
    p.add_argument("--sources", type=str, help="Comma-separated source filter (e.g., stea,um)")
    p.add_argument("-v", "--verbose", action="store_true", help="Show per-source money breakdown")
    _add_year_args(p)

    p = _json(sub.add_parser("top", help="Biggest recipients"))
    p.add_argument("source", nargs="?", help=f"Source ({'/'.join(SOURCES)})")
    p.add_argument("-n", type=int, default=20, help="Number of results (default: 20)")
    _add_year_args(p)

    p = sub.add_parser("verify", help="Verify org against sources")
    p.add_argument("name", help="Organisation name")

    _json(sub.add_parser("sources", help="Overview of data sources"))

    p = _json(sub.add_parser("clusters", help="List or inspect grant clusters"))
    p.add_argument("cluster_id", nargs="?", type=int, default=None, help="Cluster ID for details")
    p.add_argument("--id", type=int, default=None, help="Cluster ID (alternative to positional)")
    p.add_argument("--limit", type=int, default=None, help="Max results")

    p = _json(sub.add_parser("vsearch", help="Find similar items by vector similarity"))
    p.add_argument("item_id", nargs="?", help="Grant ID (numeric=STEA, S/A/J-prefix=EURA, else=UM)")
    p.add_argument("--text", help="Find seed grant by keywords (multi-word: all words must match)")
    p.add_argument("--limit", type=int, default=10, help="Number of results (default: 10)")
    p.add_argument("--source", choices=list(EMB_FILES), help="Search only this source")
    p.add_argument("--no-dedup", action="store_true", help="Disable org deduplication (default: keep best per org per source)")

    p = _json(sub.add_parser("search", help="Fulltext search across all sources"))
    p.add_argument("term", help="Search term")
    p.add_argument("--source", choices=list(SEARCH_FIELDS.keys()), help="Limit to one source")
    p.add_argument("--limit", type=int, default=30, help="Max results (default: 30)")
    _add_year_args(p)

    p = _json(sub.add_parser("profile", help="Year x source funding matrix for an org"))
    p.add_argument("name", help="Organisation name (partial match)")
    p.add_argument("--merge", action="store_true", help="Merge all matching org groups")

    p = _json(sub.add_parser("families", help="Federated org families (emojärjestöt): movement-wide totals"))
    p.add_argument("name", nargs="?", help="Family keyword or name to drill into (omit to list all)")
    p.add_argument("--limit", type=int, default=None, help="Max members shown in drill-down")

    p = _json(sub.add_parser("contracts", help="HILMA public-procurement wins for an org / top winners"))
    p.add_argument("name", nargs="?", help="Organisation name (omit and use --top for a ranking)")
    p.add_argument("--top", nargs="?", type=int, const=20, default=None, metavar="N",
                   help="Rank top contract winners (default 20)")
    p.add_argument("--buyer", help="Show contracts by a contracting authority (e.g. Maahanmuuttovirasto) and who won them")
    p.add_argument("--suorahankinta", action="store_true", help="With --top: only no-competition direct awards")
    p.add_argument("--limit", type=int, default=50, help="Max contracts shown in org/buyer mode (default: 50)")

    p = _json(sub.add_parser("lobbying", help="Lobbying register + political party ties for an org"))
    p.add_argument("name", nargs="?", help="Organisation name")
    p.add_argument("--top", nargs="?", type=int, const=20, default=None, metavar="N",
                   help="Rank most active lobbyists by registered topics (default 20)")
    p.add_argument("--party", help="List orgs connected to a party (KOK/SDP/VIHR/KESK/PS/RKP/VAS/KD)")
    p.add_argument("--limit", type=int, default=10, help="Max lobbying topics shown in org mode (default: 10)")

    p = sub.add_parser("setup", help="Download data files from GitHub release")
    p.add_argument("--force", action="store_true", help="Re-download even if data exists")

    p = _json(sub.add_parser("sql", help="Run arbitrary SQL query"))
    p.add_argument("query", help="SQL query")
    p.add_argument("--limit", type=int, default=None, help="Row limit")
    p.add_argument("--csv", dest="csv_out", action="store_true", help="CSV output")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "setup":
        cmd_setup(args)
        return

    conn = connect()
    _prune_missing_sources(conn)
    commands = {
        "org": cmd_org, "hunters": cmd_hunters, "top": cmd_top,
        "verify": cmd_verify, "sources": cmd_sources,
        "clusters": cmd_clusters, "vsearch": cmd_vsearch,
        "search": cmd_search, "profile": cmd_profile, "sql": cmd_sql,
        "families": cmd_families, "contracts": cmd_contracts, "lobbying": cmd_lobbying,
    }
    try:
        commands[args.command](args, conn)
    except BrokenPipeError:
        pass
    except KeyboardInterrupt:
        sys.exit(130)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
