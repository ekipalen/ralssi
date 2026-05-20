#!/usr/bin/env python3
"""
Add organisations from bf_awarded into org_mapping with source='bf'.

Logic:
1. Get all unique (organisation, y_tunnus) pairs from bf_awarded.
2. Group by y_tunnus — pick the most common organisation name per y_tunnus.
3. For each y_tunnus, check if it already exists in org_mapping:
   - Yes: reuse that org_id
   - No, but LOWER(name) exists: reuse that org_id (prevents duplicates)
   - No: assign a new sequential org_id
4. INSERT OR IGNORE to avoid PK violations.
5. Verify zero duplicate name groups remain after insert.
"""

import shutil
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "funding.db"
BACKUP_PATH = DB_PATH.with_suffix(".db.bak")


def main():
    # --- Backup ---
    shutil.copy2(DB_PATH, BACKUP_PATH)
    print(f"Backup created: {BACKUP_PATH}")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    cur = conn.cursor()

    # --- Before stats ---
    total_before = cur.execute("SELECT COUNT(*) FROM org_mapping").fetchone()[0]
    distinct_orgs_before = cur.execute(
        "SELECT COUNT(DISTINCT org_id) FROM org_mapping"
    ).fetchone()[0]
    sources_before = cur.execute(
        "SELECT source, COUNT(*) FROM org_mapping GROUP BY source ORDER BY source"
    ).fetchall()
    dup_groups_before = cur.execute(
        "SELECT COUNT(*) FROM ("
        "  SELECT LOWER(source_name) FROM org_mapping"
        "  GROUP BY LOWER(source_name)"
        "  HAVING COUNT(DISTINCT org_id) > 1"
        ")"
    ).fetchone()[0]

    print(f"\n=== BEFORE ===")
    print(f"Total rows:            {total_before}")
    print(f"Distinct org_ids:      {distinct_orgs_before}")
    print(f"Duplicate name groups: {dup_groups_before}")
    print(f"Sources:")
    for src, cnt in sources_before:
        print(f"  {src:>10}: {cnt}")

    # --- Step 1: Get all (organisation, y_tunnus) pairs with counts ---
    # Handle both cases: with y_tunnus and without (though BF data seems to always have y_tunnus)
    cur.execute("""
        SELECT organisation, y_tunnus, COUNT(*) as cnt
        FROM bf_awarded
        WHERE y_tunnus IS NOT NULL AND y_tunnus != ''
        GROUP BY organisation, y_tunnus
    """)
    rows_with_yt = cur.fetchall()

    cur.execute("""
        SELECT organisation, COUNT(*) as cnt
        FROM bf_awarded
        WHERE y_tunnus IS NULL OR y_tunnus = ''
        GROUP BY organisation
    """)
    rows_without_yt = cur.fetchall()

    # --- Step 2: Group by y_tunnus, pick most common name ---
    ytunnus_names: dict[str, Counter] = {}
    for org_name, y_tunnus, cnt in rows_with_yt:
        if y_tunnus not in ytunnus_names:
            ytunnus_names[y_tunnus] = Counter()
        ytunnus_names[y_tunnus][org_name] += cnt

    best_name: dict[str, str] = {}
    for yt, counter in ytunnus_names.items():
        best_name[yt] = counter.most_common(1)[0][0]

    # Orgs without y_tunnus — keyed by name directly
    no_yt_orgs: list[str] = [org_name for org_name, cnt in rows_without_yt]

    print(f"\nBF data:")
    print(f"  Unique y_tunnus values:     {len(best_name)}")
    print(f"  Orgs without y_tunnus:      {len(no_yt_orgs)}")

    # --- Step 3: Load existing org_mapping lookups ---
    cur.execute(
        "SELECT y_tunnus, org_id FROM org_mapping WHERE y_tunnus IS NOT NULL AND y_tunnus != ''"
    )
    existing_yt_to_orgid: dict[str, int] = {}
    for yt, oid in cur.fetchall():
        if yt not in existing_yt_to_orgid:
            existing_yt_to_orgid[yt] = oid

    cur.execute("SELECT LOWER(source_name), org_id FROM org_mapping")
    existing_name_to_orgid: dict[str, int] = {}
    for lname, oid in cur.fetchall():
        if lname not in existing_name_to_orgid:
            existing_name_to_orgid[lname] = oid

    cur.execute("SELECT source_name FROM org_mapping WHERE source = 'bf'")
    existing_bf_names = {r[0] for r in cur.fetchall()}

    cur.execute("SELECT COALESCE(MAX(org_id), 0) FROM org_mapping")
    next_org_id = cur.fetchone()[0] + 1

    matched_by_yt = 0
    matched_by_name = 0
    new_created = 0
    skipped = 0

    to_insert = []

    # --- Process orgs WITH y_tunnus ---
    for y_tunnus, name in sorted(best_name.items()):
        if name in existing_bf_names:
            skipped += 1
            continue

        if y_tunnus in existing_yt_to_orgid:
            org_id = existing_yt_to_orgid[y_tunnus]
            matched_by_yt += 1
        elif name.lower() in existing_name_to_orgid:
            org_id = existing_name_to_orgid[name.lower()]
            matched_by_name += 1
        else:
            org_id = next_org_id
            next_org_id += 1
            new_created += 1
            existing_yt_to_orgid[y_tunnus] = org_id

        existing_name_to_orgid[name.lower()] = org_id
        to_insert.append((org_id, "bf", name, y_tunnus, "high"))

    # --- Process orgs WITHOUT y_tunnus ---
    for name in sorted(no_yt_orgs):
        if name in existing_bf_names:
            skipped += 1
            continue

        if name.lower() in existing_name_to_orgid:
            org_id = existing_name_to_orgid[name.lower()]
            matched_by_name += 1
        else:
            org_id = next_org_id
            next_org_id += 1
            new_created += 1

        existing_name_to_orgid[name.lower()] = org_id
        to_insert.append((org_id, "bf", name, None, "high"))

    # --- Step 4: Insert ---
    cur.executemany("""
        INSERT OR IGNORE INTO org_mapping (org_id, source, source_name, y_tunnus, confidence)
        VALUES (?, ?, ?, ?, ?)
    """, to_insert)
    inserted = cur.rowcount
    conn.commit()

    print(f"\n=== CHANGES ===")
    print(f"Matched existing org_id (by y_tunnus): {matched_by_yt}")
    print(f"Matched existing org_id (by name):     {matched_by_name}")
    print(f"New org_ids created:                   {new_created}")
    print(f"Skipped (already in bf source):        {skipped}")
    print(f"Rows inserted:                         {inserted}")

    # --- Step 5: After stats + dedup check ---
    total_after = cur.execute("SELECT COUNT(*) FROM org_mapping").fetchone()[0]
    distinct_orgs_after = cur.execute(
        "SELECT COUNT(DISTINCT org_id) FROM org_mapping"
    ).fetchone()[0]
    sources_after = cur.execute(
        "SELECT source, COUNT(*) FROM org_mapping GROUP BY source ORDER BY source"
    ).fetchall()
    dup_groups_after = cur.execute(
        "SELECT COUNT(*) FROM ("
        "  SELECT LOWER(source_name) FROM org_mapping"
        "  GROUP BY LOWER(source_name)"
        "  HAVING COUNT(DISTINCT org_id) > 1"
        ")"
    ).fetchone()[0]

    print(f"\n=== AFTER ===")
    print(f"Total rows:            {total_after} (was {total_before}, +{total_after - total_before})")
    print(f"Distinct org_ids:      {distinct_orgs_after} (was {distinct_orgs_before}, +{distinct_orgs_after - distinct_orgs_before})")
    print(f"Duplicate name groups: {dup_groups_after}")
    print(f"Sources:")
    for src, cnt in sources_after:
        print(f"  {src:>10}: {cnt}")

    if dup_groups_after > 0:
        print(f"\nWARNING: {dup_groups_after} duplicate name groups remain — running dedup...")
        _dedup(conn)

        # Re-check after dedup
        dup_groups_final = cur.execute(
            "SELECT COUNT(*) FROM ("
            "  SELECT LOWER(source_name) FROM org_mapping"
            "  GROUP BY LOWER(source_name)"
            "  HAVING COUNT(DISTINCT org_id) > 1"
            ")"
        ).fetchone()[0]
        distinct_orgs_final = cur.execute(
            "SELECT COUNT(DISTINCT org_id) FROM org_mapping"
        ).fetchone()[0]
        print(f"\n=== AFTER DEDUP ===")
        print(f"Distinct org_ids:      {distinct_orgs_final}")
        print(f"Duplicate name groups: {dup_groups_final}")
        if dup_groups_final == 0:
            print("Dedup check PASSED: 0 duplicate groups.")
        else:
            print(f"ERROR: {dup_groups_final} duplicates still remain!")
    else:
        print(f"\nDedup check PASSED: 0 duplicate groups.")

    conn.close()


def _dedup(conn: sqlite3.Connection):
    """Merge org_ids where same name (case-insensitive) maps to multiple org_ids."""
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    rows = cur.execute(
        "SELECT org_id, source, source_name, y_tunnus FROM org_mapping"
        " ORDER BY source_name COLLATE NOCASE, org_id"
    ).fetchall()

    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        key = r["source_name"].lower()
        groups[key].append({
            "org_id": r["org_id"],
            "source": r["source"],
            "source_name": r["source_name"],
            "y_tunnus": r["y_tunnus"],
        })

    dup_groups = {
        k: v for k, v in groups.items()
        if len({row["org_id"] for row in v}) > 1
    }

    org_id_changes = 0
    ytunnus_fills = 0

    for name_lower, members in sorted(dup_groups.items()):
        ytunnus_values = {m["y_tunnus"] for m in members if m["y_tunnus"]}
        canonical_ytunnus = sorted(ytunnus_values)[0] if ytunnus_values else None

        with_yt = [m for m in members if m["y_tunnus"]]
        if with_yt:
            canonical_id = min(m["org_id"] for m in with_yt)
        else:
            canonical_id = min(m["org_id"] for m in members)

        for m in members:
            updates = []
            params = []

            if m["org_id"] != canonical_id:
                updates.append("org_id = ?")
                params.append(canonical_id)
                org_id_changes += 1

            if canonical_ytunnus and not m["y_tunnus"]:
                updates.append("y_tunnus = ?")
                params.append(canonical_ytunnus)
                ytunnus_fills += 1

            if updates:
                params.extend([m["source"], m["source_name"]])
                cur.execute(
                    f"UPDATE org_mapping SET {', '.join(updates)}"
                    f" WHERE source = ? AND source_name = ?",
                    params,
                )

    conn.commit()
    conn.row_factory = None
    print(f"  Dedup: {org_id_changes} org_id changes, {ytunnus_fills} y_tunnus fills")


if __name__ == "__main__":
    main()
