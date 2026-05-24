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
    "eura":     {"table": "eura_all",       "name": "toteuttaja",  "amount": "myonnetty_eu_valtio","year": None,    "desc": "EU structural funds (EURA)"},
    "bf":       {"table": "bf_awarded",     "name": "organisation","amount": "total_eur",          "year": "year",  "desc": "Business Finland"},
    "um":       {"table": "um_grants",      "name": "organisation","amount": "amount",             "year": "year",  "desc": "UM/IATI dev cooperation"},
    "helsinki":	{"table": "helsinki_grants", "name": "hakija",      "amount": "myonnetty",          "year": "vuosi", "desc": "Helsinki municipal"},
    "va":       {"table": "va_grants",      "name": "organisation","amount": "granted_eur",        "year": "year",  "desc": "Valtionavustukset (haeavustuksia.fi)"},
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
        if source == "stea":
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
            details.append({
                "year": r["year"], "amount": r["total_eur"],
                "title": r["project_title"] if "project_title" in r.keys() else None,
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
    # Sort by amount descending
    details.sort(key=lambda d: -(d.get("amount") or 0))
    return details[:limit]


def _print_detail_table(source, details):
    """Print a source-appropriate detail table."""
    if not details:
        print("    (no grants)")
        return
    if source == "stea":
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
        print_table(["Year", "Amount", "Title"], [
            [str(d["year"] or "-"), fmt_money(d["amount"]),
             textwrap.shorten(d["title"] or "-", 60, placeholder="...")]
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


def _summarize_org_group(conn, source_names, json_mode, detail=False, detail_source=None, detail_limit=50):
    """Summarize one org group across sources. Returns (output_list, combined_total)."""
    combined_total = 0
    output = []
    for src in ["stea", "eura", "bf", "um", "helsinki", "va"]:
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

    if args.json:
        if len(all_results) == 1:
            r = all_results[0]
            print(json.dumps({"query": name, "org_id": r["org_id"], "match": r["match"],
                               "sources": r["sources"], "combined_total": r["combined_total"]},
                              ensure_ascii=False, indent=2))
        else:
            print(json.dumps({"query": name, "results": all_results}, ensure_ascii=False, indent=2))
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
        print(f"  Combined total: {fmt_money(r['combined_total'])}")
        if i < len(all_results) - 1:
            print()


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
    orgs = {}

    ytunnus_sources = {"stea", "eura", "bf", "va"}
    if source_filter:
        ytunnus_sources = ytunnus_sources & source_filter
    for src in ytunnus_sources:
        for row in _aggregate_by_ytunnus(conn, src, since=since, until=until):
            yt = row["y_tunnus"]
            total = row["total"] or 0
            if total <= 0:
                continue
            orgs.setdefault(yt, {"names": set(), "sources": set(), "total": 0, "source_totals": {}})
            orgs[yt]["names"].add(row["org_name"])
            orgs[yt]["sources"].add(src)
            orgs[yt]["total"] += total
            orgs[yt]["source_totals"][src] = orgs[yt]["source_totals"].get(src, 0) + total

    mapping_by_oid = {}
    mapping_sources = {"um", "helsinki"}
    if source_filter:
        mapping_sources = mapping_sources & source_filter
    if mapping_sources or verbose:
        for row in conn.execute("SELECT org_id, source, source_name, y_tunnus FROM org_mapping WHERE COALESCE(is_category, 0) = 0"):
            mapping_by_oid.setdefault(row["org_id"], []).append(row)

    if mapping_sources:
        for oid, entries in mapping_by_oid.items():
            src_set = set(e["source"] for e in entries)
            if not (src_set & mapping_sources):
                continue
            yt = next((e["y_tunnus"] for e in entries if e["y_tunnus"]), None)
            key = yt if yt and yt in orgs else (yt or f"_org_{oid}")
            entry = orgs.setdefault(key, {"names": set(), "sources": set(), "total": 0, "source_totals": {}})
            for e in entries:
                entry["names"].add(e["source_name"])

            for check_src, tbl_amount, tbl_name, tbl_table in [
                ("um", "amount", "organisation", "um_grants"),
                ("helsinki", "myonnetty", "hakija", "helsinki_grants"),
            ]:
                if check_src not in src_set:
                    continue
                if source_filter and check_src not in source_filter:
                    continue
                names = [e["source_name"] for e in entries if e["source"] == check_src]
                ph = ",".join("?" for _ in names)
                yr_clause, yr_params = _year_where(check_src, since, until)
                where = f"{tbl_name} IN ({ph})"
                if yr_clause:
                    where += f" AND {yr_clause}"
                t = conn.execute(f"SELECT SUM({tbl_amount}) FROM {tbl_table} WHERE {where}", names + yr_params).fetchone()[0]
                if t:
                    entry["sources"].add(check_src)
                    entry["total"] += t
                    entry["source_totals"][check_src] = entry["source_totals"].get(check_src, 0) + t

    # In verbose mode, also pull in org_mapping-only overlaps
    if verbose:
        for oid, entries in mapping_by_oid.items():
            src_set = set(e["source"] for e in entries)
            if len(src_set) < 2:
                continue
            yt = next((e["y_tunnus"] for e in entries if e["y_tunnus"]), None)
            key = yt if yt and yt in orgs else (yt or f"_org_{oid}")
            entry = orgs.setdefault(key, {"names": set(), "sources": set(), "total": 0, "source_totals": {}})
            for e in entries:
                entry["names"].add(e["source_name"])
            by_source = {}
            for e in entries:
                by_source.setdefault(e["source"], set()).add(e["source_name"])
            for src, names in by_source.items():
                if src in entry["source_totals"]:
                    continue
                rows = query_source(conn, src, names)
                if rows:
                    t = sum(r[SOURCES[src]["amount"]] or 0 for r in rows)
                    if t:
                        entry["sources"].add(src)
                        entry["total"] += t
                        entry["source_totals"][src] = t

    # Build sector lookup for third-sector filtering
    if third_sector:
        _sector_by_ytunnus = {}
        for row in conn.execute("SELECT DISTINCT y_tunnus, sector FROM org_mapping WHERE y_tunnus IS NOT NULL AND y_tunnus != ''"):
            _sector_by_ytunnus.setdefault(row["y_tunnus"], set()).add(row["sector"])

    results = []
    for key, d in orgs.items():
        if source_filter and not (d["sources"] >= source_filter):
            continue
        if len(d["sources"]) < min_sources:
            continue
        # Third-sector filter
        if third_sector:
            excluded = False
            if key.startswith("_org_"):
                oid = int(key[5:])
                excluded = _third_sector_org_excluded(conn, oid)
            elif key in _sector_by_ytunnus:
                sectors = _sector_by_ytunnus[key]
                excluded = all(s in EXCLUDED_SECTORS for s in sectors if s)
            else:
                # Fallback: name heuristic
                name = sorted(d["names"])[0] if d["names"] else key
                excluded = _is_non_third_sector_name(name)
            if excluded:
                continue
        flags = []
        s = d["sources"]
        if "stea" in s and "bf" in s:
            flags.append("ngo+company")
        if "um" in s and (s & {"stea", "helsinki"}):
            flags.append("dev+domestic")
        if "helsinki" in s and "stea" in s:
            flags.append("municipal+national")
        results.append({
            "name": sorted(d["names"])[0] if d["names"] else key,
            "y_tunnus": key if not key.startswith("_org_") else "-",
            "sources": sorted(d["sources"]), "source_count": len(d["sources"]),
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
        ts_clause, ts_params = _third_sector_sql_for_source(source, third_sector)
        where_parts = []
        params = []
        if yr_clause:
            where_parts.append(yr_clause)
            params.extend(yr_params)
        if ts_clause:
            where_parts.append(ts_clause)
            params.extend(ts_params)
        where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        # Fetch more than n to account for heuristic filtering on NULLs
        fetch_limit = n * 5 if third_sector else n
        rows = conn.execute(
            f"SELECT {s['name']} as name, SUM({s['amount']}) as total, COUNT(*) as cnt "
            f"FROM {s['table']} {where} GROUP BY {s['name']} ORDER BY total DESC LIMIT ?",
            params + [fetch_limit],
        ).fetchall()
        # Apply name heuristic for NULLs not in org_mapping
        if third_sector:
            rows = [r for r in rows if not _is_non_third_sector_name(r["name"])]
        rows = rows[:n]
        if args.json:
            print(json.dumps([{"name": r["name"], "total": r["total"], "grants": r["cnt"]} for r in rows],
                              ensure_ascii=False, indent=2))
            return
        title = f"Top {n} recipients — {source.upper()}"
        if yr_label:
            title += f" ({yr_label})"
        if third_sector:
            title += " [third-sector]"
        print(f"{title}\n")
        print_table(["#", "Name", "Total", "Grants"], [
            [str(i), textwrap.shorten(r["name"] or "-", 50, placeholder="..."),
             fmt_money(r["total"]), str(r["cnt"])]
            for i, r in enumerate(rows, 1)
        ])
    else:
        combined = {}
        for src, s in SOURCES.items():
            yr_clause, yr_params = _year_where(src, since, until)
            ts_clause, ts_params = _third_sector_sql_for_source(src, third_sector)
            where_parts = []
            params = []
            if yr_clause:
                where_parts.append(yr_clause)
                params.extend(yr_params)
            if ts_clause:
                where_parts.append(ts_clause)
                params.extend(ts_params)
            where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
            for row in conn.execute(
                f"SELECT {s['name']} as name, SUM({s['amount']}) as total FROM {s['table']} {where} GROUP BY {s['name']}",
                params,
            ):
                key = (row["name"] or "").strip().upper()
                if key:
                    if third_sector and _is_non_third_sector_name(row["name"]):
                        continue
                    combined.setdefault(key, {"name": row["name"], "total": 0})
                    combined[key]["total"] += row["total"] or 0
        top = sorted(combined.values(), key=lambda x: -x["total"])[:n]
        if args.json:
            print(json.dumps(top, ensure_ascii=False, indent=2))
            return
        title = f"Top {n} recipients — all sources (approximate, name-based)"
        if yr_label:
            title += f" ({yr_label})"
        if third_sector:
            title += " [third-sector]"
        print(f"{title}\n")
        print_table(["#", "Name", "Total"], [
            [str(i), textwrap.shorten(r["name"] or "-", 55, placeholder="..."), fmt_money(r["total"])]
            for i, r in enumerate(top, 1)
        ])



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
    for src in ["stea", "eura", "bf", "um", "helsinki", "va"]:
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
    emb_files = {
        "stea": ("embeddings.npy", "embedding_ids.json"),
        "eura": ("eura_embeddings.npy", "eura_embedding_ids.json"),
        "um":   ("um_embeddings.npy", "um_embedding_ids.json"),
        "va":   ("va_embeddings.npy", "va_embedding_ids.json"),
    }

    # Resolve item_id: either directly or via --text search
    if args.text:
        if args.item_id:
            die("Cannot use both item_id and --text at the same time")
        text_term = args.text
        words = text_term.split()
        best_match = None
        for src, sf in SEARCH_FIELDS.items():
            if src not in emb_files:
                continue
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
            row = conn.execute(
                f"SELECT {sf['id']} as item_id, {sf['name']} as org, {sf['amount']} as amount "
                f"FROM {sf['table']} WHERE {where} ORDER BY {sf['amount']} DESC LIMIT 1",
                params,
            ).fetchone()
            if row and (best_match is None or (row["amount"] or 0) > (best_match[2] or 0)):
                best_match = (src, row["item_id"], row["amount"], row["org"])
        if not best_match:
            die(f'No grants matching "{text_term}" found in indexed sources')
        text_src, item_id = best_match[0], str(best_match[1])
        if not args.json:
            print(f'--text "{text_term}" -> seed: [{text_src.upper()}] {item_id} ({best_match[3]})\n')
    elif args.item_id:
        item_id = args.item_id
    else:
        die("Either item_id or --text is required")

    # Auto-detect source from ID format (--source overrides)
    if args.source and args.source in emb_files:
        query_source_key = args.source
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
    lookup_id = int(item_id) if query_source_key in ("stea", "va") else item_id
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
    elif query_source_key == "eura":
        qrow = conn.execute("SELECT nimi, toteuttaja FROM eura_all WHERE hankekoodi = ?", [item_id]).fetchone()
        q_desc = f"{qrow['toteuttaja']}: {qrow['nimi']}" if qrow else item_id
    elif query_source_key == "va":
        qrow = conn.execute("SELECT organisation, purpose FROM va_grants WHERE id = ?", [lookup_id]).fetchone()
        q_desc = f"{qrow['organisation']}: {qrow['purpose']}" if qrow else item_id
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
    all_sources_ordered = ["stea", "eura", "um", "bf", "helsinki", "va"]
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
    "eura":     {"table": "eura_all",       "text": ["nimi", "tiivistelma"],            "name": "toteuttaja",  "amount": "myonnetty_eu_valtio","year": "aloituspvm", "id": "hankekoodi"},
    "um":       {"table": "um_grants",      "text": ["title", "description"],           "name": "organisation","amount": "amount",             "year": "year", "id": "activity_id"},
    "helsinki":	{"table": "helsinki_grants", "text": ["hakemustyyppi", "avustuslaji"],   "name": "hakija",      "amount": "myonnetty",          "year": "vuosi", "id": "id"},
    "va":       {"table": "va_grants",      "text": ["purpose", "call_name"],            "name": "organisation","amount": "granted_eur",        "year": "year",  "id": "id"},
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
        conditions = " OR ".join(f"{col} LIKE ? ESCAPE '\\'" for col in sf["text"])
        params = [f"%{_escape_like(term)}%" for _ in sf["text"]]
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
        if not (sources_found & {"stea", "eura"}):
            print(f'\nHint: STEA/EURA data is in Finnish. Try also: search "{term}" in Finnish')


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
        for src in ["stea", "eura", "bf", "um", "helsinki", "va"]:
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


RELEASE_URL = "https://github.com/ekipalen/ralssi/releases/download/v2.1/ralssi-data.zip"


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

    p = _json(sub.add_parser("hunters", help="Find orgs in multiple sources"))
    p.add_argument("--min", type=int, default=2, help="Min sources (default: 2)")
    p.add_argument("--limit", type=int, default=20, help="Max results (default: 20)")
    p.add_argument("--sort", choices=["total", "sources"], default="total")
    p.add_argument("--sources", type=str, help="Comma-separated source filter (e.g., stea,um)")
    p.add_argument("-v", "--verbose", action="store_true", help="Show per-source money breakdown")
    _add_year_args(p)

    p = _json(sub.add_parser("top", help="Biggest recipients"))
    p.add_argument("source", nargs="?", help="Source (stea/eura/bf/um/helsinki)")
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
    p.add_argument("--source", choices=["stea", "eura", "um", "va"], help="Search only this source")
    p.add_argument("--no-dedup", action="store_true", help="Disable org deduplication (default: keep best per org per source)")

    p = _json(sub.add_parser("search", help="Fulltext search across all sources"))
    p.add_argument("term", help="Search term")
    p.add_argument("--source", choices=list(SEARCH_FIELDS.keys()), help="Limit to one source")
    p.add_argument("--limit", type=int, default=30, help="Max results (default: 30)")
    _add_year_args(p)

    p = _json(sub.add_parser("profile", help="Year x source funding matrix for an org"))
    p.add_argument("name", help="Organisation name (partial match)")
    p.add_argument("--merge", action="store_true", help="Merge all matching org groups")

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
    commands = {
        "org": cmd_org, "hunters": cmd_hunters, "top": cmd_top,
        "verify": cmd_verify, "sources": cmd_sources,
        "clusters": cmd_clusters, "vsearch": cmd_vsearch,
        "search": cmd_search, "profile": cmd_profile, "sql": cmd_sql,
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
