#!/usr/bin/env python3
"""
Add organisations from fts_grants into org_mapping with source='fts'.

Logic:
1. Get all unique (organisation, y_tunnus) pairs from fts_grants.
2. Group by y_tunnus — pick the most common organisation name per y_tunnus.
3. For each y_tunnus, check if it already exists in org_mapping:
   - Yes: reuse that org_id
   - No: assign a new sequential org_id (max existing + 1, +2, ...)
4. Skip if (source='fts', source_name=X) already exists.

Also handles orgs without y_tunnus: matches by name (case-insensitive).
"""

import sqlite3
from collections import Counter
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "funding.db"


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    cur = conn.cursor()

    # Step 1: Get all (organisation, y_tunnus) pairs with counts — only rows with y_tunnus
    cur.execute("""
        SELECT organisation, y_tunnus, COUNT(*) as cnt
        FROM fts_grants
        WHERE y_tunnus IS NOT NULL AND y_tunnus != ''
        GROUP BY organisation, y_tunnus
    """)
    rows_with_yt = cur.fetchall()

    # Also get orgs WITHOUT y_tunnus
    cur.execute("""
        SELECT organisation, COUNT(*) as cnt
        FROM fts_grants
        WHERE y_tunnus IS NULL OR y_tunnus = ''
        GROUP BY organisation
    """)
    rows_without_yt = cur.fetchall()

    # Step 2: Group by y_tunnus, pick most common name
    ytunnus_names: dict[str, Counter] = {}
    for org_name, y_tunnus, cnt in rows_with_yt:
        if y_tunnus not in ytunnus_names:
            ytunnus_names[y_tunnus] = Counter()
        ytunnus_names[y_tunnus][org_name] += cnt

    # Best name per y_tunnus
    best_name: dict[str, str] = {}
    for yt, counter in ytunnus_names.items():
        best_name[yt] = counter.most_common(1)[0][0]

    print(f"Unique y_tunnus values in fts_grants: {len(best_name)}")
    print(f"Organisations without y_tunnus: {len(rows_without_yt)}")

    # Step 3: Load existing org_mapping y_tunnus -> org_id
    cur.execute("SELECT y_tunnus, org_id FROM org_mapping WHERE y_tunnus IS NOT NULL AND y_tunnus != ''")
    existing_yt_to_orgid: dict[str, int] = {}
    for yt, oid in cur.fetchall():
        if yt not in existing_yt_to_orgid:
            existing_yt_to_orgid[yt] = oid

    # Load existing name -> org_id (case-insensitive)
    cur.execute("SELECT LOWER(source_name), org_id FROM org_mapping")
    existing_name_to_orgid: dict[str, int] = {}
    for lname, oid in cur.fetchall():
        if lname not in existing_name_to_orgid:
            existing_name_to_orgid[lname] = oid

    # Load existing (source='fts') entries to skip duplicates
    cur.execute("SELECT source_name FROM org_mapping WHERE source = 'fts'")
    existing_fts_names = {r[0] for r in cur.fetchall()}

    # Get max org_id
    cur.execute("SELECT COALESCE(MAX(org_id), 0) FROM org_mapping")
    next_org_id = cur.fetchone()[0] + 1

    matched_existing = 0
    matched_by_name = 0
    new_created = 0
    skipped = 0

    to_insert = []

    # Process orgs with y_tunnus
    for y_tunnus, name in sorted(best_name.items()):
        if name in existing_fts_names:
            skipped += 1
            continue

        if y_tunnus in existing_yt_to_orgid:
            org_id = existing_yt_to_orgid[y_tunnus]
            matched_existing += 1
        elif name.lower() in existing_name_to_orgid:
            org_id = existing_name_to_orgid[name.lower()]
            matched_by_name += 1
        else:
            org_id = next_org_id
            next_org_id += 1
            new_created += 1
            existing_yt_to_orgid[y_tunnus] = org_id

        existing_name_to_orgid[name.lower()] = org_id
        to_insert.append((org_id, "fts", name, y_tunnus, "high"))

    # Process orgs without y_tunnus — match by name only
    no_yt_matched = 0
    no_yt_new = 0
    for org_name, cnt in rows_without_yt:
        if org_name in existing_fts_names:
            skipped += 1
            continue

        if org_name.lower() in existing_name_to_orgid:
            org_id = existing_name_to_orgid[org_name.lower()]
            no_yt_matched += 1
        else:
            org_id = next_org_id
            next_org_id += 1
            no_yt_new += 1

        existing_name_to_orgid[org_name.lower()] = org_id
        to_insert.append((org_id, "fts", org_name, None, "low"))

    # Step 4: Insert
    cur.executemany("""
        INSERT OR IGNORE INTO org_mapping (org_id, source, source_name, y_tunnus, confidence)
        VALUES (?, ?, ?, ?, ?)
    """, to_insert)
    inserted = cur.rowcount
    conn.commit()

    print(f"\n--- Summary ---")
    print(f"With Y-tunnus:")
    print(f"  Matched existing org_id (by y_tunnus): {matched_existing}")
    print(f"  Matched existing org_id (by name):     {matched_by_name}")
    print(f"  New org_ids created:                   {new_created}")
    print(f"Without Y-tunnus:")
    print(f"  Matched existing org_id (by name):     {no_yt_matched}")
    print(f"  New org_ids created:                   {no_yt_new}")
    print(f"Skipped (already in fts source):         {skipped}")
    print(f"Total fts entries inserted:              {inserted}")

    # Verify
    cur.execute("SELECT COUNT(*) FROM org_mapping WHERE source = 'fts'")
    total_fts = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM org_mapping")
    total_all = cur.fetchone()[0]
    print(f"\norg_mapping total rows now: {total_all} (fts: {total_fts})")

    conn.close()


if __name__ == "__main__":
    main()
