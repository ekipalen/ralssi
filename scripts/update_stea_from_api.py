#!/usr/bin/env python3
"""Update STEA data from public API: y_tunnus + 2026 decisions.

Phase 1: Crawl API into staging table (stea_api_staging)
Phase 2: Update y_tunnus in grants + org_mapping (additive only)
Phase 3: Update 2026 myonnetty/ehdotettu values
Phase 4: Verification report

Safe: never changes jarjesto names, never deletes rows.
Supabase: not touched.
"""

import sqlite3
import json
import time
import sys
import urllib.request
import urllib.error

DB = "/home/eki/ralssi/data/funding.db"
STEA_API = "https://avustukset.stea.fi/api/organisation"
MAX_ORG_ID = 2500
DELAY = 0.1

def fetch_org(org_id):
    url = f"{STEA_API}/{org_id}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            if data.get("name") is None:
                return None
            return data
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError):
        return None


def phase1_crawl(conn):
    """Crawl STEA API into staging table."""
    print("=== Phase 1: API Crawl ===")

    conn.execute("DROP TABLE IF EXISTS stea_api_staging")
    conn.execute("""
        CREATE TABLE stea_api_staging (
            api_org_id INTEGER PRIMARY KEY,
            api_name TEXT NOT NULL,
            business_id TEXT,
            name_history TEXT,
            grants_json TEXT
        )
    """)

    orgs_found = 0
    for org_id in range(1, MAX_ORG_ID + 1):
        data = fetch_org(org_id)
        if data is None:
            continue

        orgs_found += 1
        name_history = json.dumps(data.get("nameHistory", []), ensure_ascii=False)
        grants_2026 = [
            {
                "name": g.get("organisationName") or data["name"],
                "year": g["year"],
                "requested": g["requested"],
                "proposed": g["proposed"],
                "granted": g["granted"],
                "purpose": g.get("purpose", ""),
            }
            for g in data.get("allAidTargets", [])
            if g["year"] == 2026
        ]

        conn.execute(
            "INSERT INTO stea_api_staging VALUES (?, ?, ?, ?, ?)",
            (
                data["organisationId"],
                data["name"],
                data.get("businessId"),
                name_history,
                json.dumps(grants_2026, ensure_ascii=False),
            ),
        )

        if org_id % 100 == 0:
            print(f"  ...org_id {org_id}: {orgs_found} orgs")
            conn.commit()

        time.sleep(DELAY)

    conn.commit()
    print(f"  Crawled {orgs_found} orgs into stea_api_staging")
    return orgs_found


def _build_name_map(conn):
    """Build API name -> DB jarjesto mapping, handling name changes via nameHistory."""
    rows = conn.execute(
        "SELECT api_name, business_id, name_history FROM stea_api_staging"
    ).fetchall()

    db_names = set(
        r[0] for r in conn.execute("SELECT DISTINCT jarjesto FROM grants").fetchall()
    )

    api_to_db = {}
    for api_name, bid, history_json in rows:
        if api_name in db_names:
            api_to_db[api_name] = api_name
        else:
            # Check name history (list of old name strings)
            history = json.loads(history_json) if history_json else []
            if not history:
                history = []
            for old_name in history:
                if not isinstance(old_name, str):
                    continue
                if old_name in db_names:
                    api_to_db[api_name] = old_name
                    break
            # Try case-insensitive
            if api_name not in api_to_db:
                for db_name in db_names:
                    if db_name.upper() == api_name.upper():
                        api_to_db[api_name] = db_name
                        break

    return api_to_db


def phase2_ytunnus(conn):
    """Update y_tunnus in grants and org_mapping from API businessId."""
    print("\n=== Phase 2: Y-tunnus enrichment ===")

    api_to_db = _build_name_map(conn)

    rows = conn.execute(
        "SELECT api_name, business_id FROM stea_api_staging WHERE business_id IS NOT NULL AND business_id != ''"
    ).fetchall()

    grants_updated = 0
    mapping_updated = 0
    skipped_no_match = 0

    for api_name, bid in rows:
        db_name = api_to_db.get(api_name)
        if not db_name:
            skipped_no_match += 1
            continue

        # Update grants table
        cur = conn.execute(
            "UPDATE grants SET y_tunnus = ? WHERE jarjesto = ? AND (y_tunnus IS NULL OR y_tunnus = '')",
            (bid, db_name),
        )
        grants_updated += cur.rowcount

        # Update org_mapping
        cur = conn.execute(
            "UPDATE org_mapping SET y_tunnus = ? WHERE source = 'stea' AND source_name = ? AND (y_tunnus IS NULL OR y_tunnus = '')",
            (bid, db_name),
        )
        mapping_updated += cur.rowcount

    conn.commit()
    print(f"  Grants rows updated with y_tunnus: {grants_updated}")
    print(f"  org_mapping rows updated: {mapping_updated}")
    print(f"  Skipped (no name match): {skipped_no_match}")


def phase3_decisions(conn):
    """Update 2026 myonnetty/ehdotettu from API data."""
    print("\n=== Phase 3: 2026 decisions ===")

    api_to_db = _build_name_map(conn)

    rows = conn.execute(
        "SELECT api_name, grants_json FROM stea_api_staging WHERE grants_json != '[]'"
    ).fetchall()

    updated = 0
    not_matched = 0
    already_ok = 0

    for api_name, grants_json in rows:
        db_name = api_to_db.get(api_name)
        if not db_name:
            grants = json.loads(grants_json)
            not_matched += len(grants)
            continue

        grants = json.loads(grants_json)
        for g in grants:
            # Primary match: jarjesto + vuosi + haettu
            cur = conn.execute(
                "SELECT rowid as rid, myonnetty, ehdotettu FROM grants WHERE jarjesto = ? AND vuosi = 2026 AND haettu = ?",
                (db_name, g["requested"]),
            )
            matches = cur.fetchall()

            if len(matches) == 0:
                not_matched += 1
                continue
            elif len(matches) > 1:
                # Disambiguate by purpose prefix
                purpose_prefix = (g.get("purpose") or "")[:80]
                cur2 = conn.execute(
                    "SELECT rowid as rid, myonnetty, ehdotettu FROM grants WHERE jarjesto = ? AND vuosi = 2026 AND haettu = ? AND substr(kayttotarkoitus,1,80) = ?",
                    (db_name, g["requested"], purpose_prefix),
                )
                matches2 = cur2.fetchall()
                if len(matches2) == 1:
                    matches = matches2
                else:
                    # All duplicates get same value — safe to update first
                    matches = [matches[0]]

            row = matches[0]
            if row["myonnetty"] == g["granted"] and row["ehdotettu"] == g["proposed"]:
                already_ok += 1
                continue

            conn.execute(
                "UPDATE grants SET myonnetty = ?, ehdotettu = ? WHERE rowid = ?",
                (g["granted"], g["proposed"], row["rid"]),
            )
            updated += 1

    conn.commit()
    print(f"  Updated: {updated}")
    print(f"  Already correct: {already_ok}")
    print(f"  Not matched: {not_matched}")


def phase4_verify(conn):
    """Verification report."""
    print("\n=== Phase 4: Verification ===")

    # Row counts
    total = conn.execute("SELECT COUNT(*) FROM grants").fetchone()[0]
    print(f"  Total grant rows: {total}")

    # Y-tunnus coverage
    with_yt = conn.execute(
        "SELECT COUNT(DISTINCT jarjesto) FROM grants WHERE y_tunnus IS NOT NULL AND y_tunnus != ''"
    ).fetchone()[0]
    total_orgs = conn.execute("SELECT COUNT(DISTINCT jarjesto) FROM grants").fetchone()[0]
    print(f"  Orgs with y_tunnus in grants: {with_yt}/{total_orgs} ({100*with_yt//total_orgs}%)")

    # org_mapping y_tunnus
    om_with = conn.execute(
        "SELECT COUNT(*) FROM org_mapping WHERE source='stea' AND y_tunnus IS NOT NULL AND y_tunnus != ''"
    ).fetchone()[0]
    om_total = conn.execute("SELECT COUNT(*) FROM org_mapping WHERE source='stea'").fetchone()[0]
    print(f"  org_mapping STEA with y_tunnus: {om_with}/{om_total}")

    # org_mapping integrity
    orphans = conn.execute(
        "SELECT COUNT(*) FROM org_mapping WHERE source='stea' AND source_name NOT IN (SELECT DISTINCT jarjesto FROM grants)"
    ).fetchone()[0]
    print(f"  org_mapping orphans: {orphans}")

    # 2026 decisions
    r = conn.execute(
        "SELECT COUNT(*) as cnt, SUM(myonnetty) as total, SUM(CASE WHEN myonnetty > 0 THEN 1 ELSE 0 END) as with_decision FROM grants WHERE vuosi=2026"
    ).fetchone()
    print(f"  2026 rows: {r[0]}, with decision: {r[2]}, total myönnetty: {r[1]:,.0f} €")

    # Spot check: Riemu Finland (org 862)
    riemu = conn.execute(
        "SELECT vuosi, haettu, myonnetty, y_tunnus FROM grants WHERE jarjesto='Riemu Finland ry' AND vuosi=2026 LIMIT 3"
    ).fetchall()
    print(f"\n  Spot check - Riemu Finland ry 2026:")
    for r in riemu:
        print(f"    vuosi={r[0]} haettu={r[1]} myonnetty={r[2]} y_tunnus={r[3]}")

    # Spot check: Loma ja Terveys (org 730)
    loma = conn.execute(
        "SELECT vuosi, haettu, myonnetty, y_tunnus FROM grants WHERE jarjesto='Loma ja Terveys ry' AND vuosi=2026 LIMIT 3"
    ).fetchall()
    print(f"\n  Spot check - Loma ja Terveys ry 2026:")
    for r in loma:
        print(f"    vuosi={r[0]} haettu={r[1]} myonnetty={r[2]} y_tunnus={r[3]}")

    # Overall totals per year
    print(f"\n  Yearly totals (myönnetty):")
    for r in conn.execute("SELECT vuosi, COUNT(*), SUM(myonnetty) FROM grants GROUP BY vuosi ORDER BY vuosi DESC"):
        print(f"    {r[0]}: {r[1]} rows, {r[2]:>14,.0f} €")


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    # Pre-check
    pre_count = conn.execute("SELECT COUNT(*) FROM grants").fetchone()[0]
    print(f"Pre-check: {pre_count} rows in grants\n")

    # Skip crawl if staging table already populated
    existing = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='stea_api_staging'"
    ).fetchone()[0]
    if existing:
        count = conn.execute("SELECT COUNT(*) FROM stea_api_staging").fetchone()[0]
        if count > 2000:
            print(f"Staging table exists with {count} rows, skipping crawl")
        else:
            phase1_crawl(conn)
    else:
        phase1_crawl(conn)

    phase2_ytunnus(conn)
    phase3_decisions(conn)
    phase4_verify(conn)

    # Post-check: row count must not change
    post_count = conn.execute("SELECT COUNT(*) FROM grants").fetchone()[0]
    assert pre_count == post_count, f"ROW COUNT CHANGED! {pre_count} -> {post_count}"
    print(f"\n✓ Row count unchanged: {post_count}")

    conn.execute("DROP TABLE IF EXISTS stea_api_staging")
    conn.commit()
    conn.close()
    print("Done. Staging table cleaned up.")


if __name__ == "__main__":
    main()
