#!/usr/bin/env python3
"""
Add organisations from va_grants into org_mapping with source='va'.

Logic:
1. Get all unique (organisation, y_tunnus) pairs from va_grants.
2. Group by y_tunnus — pick the most common organisation name per y_tunnus.
3. For each y_tunnus, check if it already exists in org_mapping:
   - Yes: reuse that org_id
   - No: assign a new sequential org_id (max existing + 1, +2, ...)
4. Skip if (source='va', source_name=X) already exists.
"""

import sqlite3
from collections import Counter
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "funding.db"


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    cur = conn.cursor()

    # Step 1: Get all (organisation, y_tunnus) pairs with counts
    cur.execute("""
        SELECT organisation, y_tunnus, COUNT(*) as cnt
        FROM va_grants
        WHERE y_tunnus IS NOT NULL AND y_tunnus != ''
        GROUP BY organisation, y_tunnus
    """)
    rows = cur.fetchall()

    # Step 2: Group by y_tunnus, pick most common name
    ytunnus_names: dict[str, Counter] = {}
    for org_name, y_tunnus, cnt in rows:
        if y_tunnus not in ytunnus_names:
            ytunnus_names[y_tunnus] = Counter()
        ytunnus_names[y_tunnus][org_name] += cnt

    # Best name per y_tunnus
    best_name: dict[str, str] = {}
    for yt, counter in ytunnus_names.items():
        best_name[yt] = counter.most_common(1)[0][0]

    print(f"Unique y_tunnus values in va_grants: {len(best_name)}")

    # Step 3: Load existing org_mapping y_tunnus -> org_id
    cur.execute("SELECT y_tunnus, org_id FROM org_mapping WHERE y_tunnus IS NOT NULL AND y_tunnus != ''")
    existing_yt_to_orgid: dict[str, int] = {}
    for yt, oid in cur.fetchall():
        if yt not in existing_yt_to_orgid:
            existing_yt_to_orgid[yt] = oid

    # Load existing name -> org_id (case-insensitive) to prevent duplicates
    cur.execute("SELECT LOWER(source_name), org_id FROM org_mapping")
    existing_name_to_orgid: dict[str, int] = {}
    for lname, oid in cur.fetchall():
        if lname not in existing_name_to_orgid:
            existing_name_to_orgid[lname] = oid

    # Load existing (source='va') entries to skip duplicates
    cur.execute("SELECT source_name FROM org_mapping WHERE source = 'va'")
    existing_va_names = {r[0] for r in cur.fetchall()}

    # Get max org_id
    cur.execute("SELECT COALESCE(MAX(org_id), 0) FROM org_mapping")
    next_org_id = cur.fetchone()[0] + 1

    matched_existing = 0
    matched_by_name = 0
    new_created = 0
    skipped = 0
    inserted = 0

    to_insert = []

    for y_tunnus, name in sorted(best_name.items()):
        # Skip if already exists
        if name in existing_va_names:
            skipped += 1
            continue

        if y_tunnus in existing_yt_to_orgid:
            org_id = existing_yt_to_orgid[y_tunnus]
            matched_existing += 1
        elif name.lower() in existing_name_to_orgid:
            # Reuse org_id from existing entry with same name (case-insensitive)
            org_id = existing_name_to_orgid[name.lower()]
            matched_by_name += 1
        else:
            org_id = next_org_id
            next_org_id += 1
            new_created += 1
            existing_yt_to_orgid[y_tunnus] = org_id

        existing_name_to_orgid[name.lower()] = org_id
        to_insert.append((org_id, "va", name, y_tunnus, "high"))

    # Step 4: Insert
    cur.executemany("""
        INSERT OR IGNORE INTO org_mapping (org_id, source, source_name, y_tunnus, confidence)
        VALUES (?, ?, ?, ?, ?)
    """, to_insert)
    inserted = cur.rowcount
    conn.commit()

    print(f"\n--- Summary ---")
    print(f"Matched existing org_id (by y_tunnus): {matched_existing}")
    print(f"Matched existing org_id (by name):     {matched_by_name}")
    print(f"New org_ids created:                   {new_created}")
    print(f"Skipped (already in va source):        {skipped}")
    print(f"Total va entries inserted:             {inserted}")

    # Verify
    cur.execute("SELECT COUNT(*) FROM org_mapping WHERE source = 'va'")
    total_va = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM org_mapping")
    total_all = cur.fetchone()[0]
    print(f"\norg_mapping total rows now: {total_all} (va: {total_va})")

    conn.close()


if __name__ == "__main__":
    main()
