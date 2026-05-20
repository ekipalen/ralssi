"""
Deduplicate org_ids in org_mapping table.

Problem: 1,567 org names (case-insensitive) map to multiple org_ids.
Solution: For each group, pick a canonical org_id (prefer one with y_tunnus,
then lowest), update all rows, and propagate y_tunnus.
"""

import shutil
import sqlite3
from collections import defaultdict
from pathlib import Path

DB_PATH = Path("/home/eki/ralssi/data/funding.db")
BACKUP_PATH = DB_PATH.with_suffix(".db.bak")


def main():
    # --- Backup ---
    shutil.copy2(DB_PATH, BACKUP_PATH)
    print(f"Backup created: {BACKUP_PATH}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # --- Before stats ---
    total_rows = cur.execute("SELECT COUNT(*) FROM org_mapping").fetchone()[0]
    distinct_orgs_before = cur.execute(
        "SELECT COUNT(DISTINCT org_id) FROM org_mapping"
    ).fetchone()[0]
    dup_groups_before = cur.execute(
        "SELECT COUNT(*) FROM ("
        "  SELECT LOWER(source_name) FROM org_mapping"
        "  GROUP BY LOWER(source_name)"
        "  HAVING COUNT(DISTINCT org_id) > 1"
        ")"
    ).fetchone()[0]

    print(f"\n=== BEFORE ===")
    print(f"Total rows:            {total_rows}")
    print(f"Distinct org_ids:      {distinct_orgs_before}")
    print(f"Duplicate name groups: {dup_groups_before}")

    # --- Find duplicates ---
    # Group all rows by lower(source_name)
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

    # Filter to groups with multiple distinct org_ids
    dup_groups = {
        k: v for k, v in groups.items()
        if len({row["org_id"] for row in v}) > 1
    }

    print(f"\nFound {len(dup_groups)} duplicate groups to fix")

    # --- Pick canonical org_id and y_tunnus for each group ---
    org_id_changes = 0
    ytunnus_fills = 0
    change_log: list[str] = []

    conn.execute("BEGIN")

    for name_lower, members in sorted(dup_groups.items()):
        # Collect all y_tunnus values from the group
        ytunnus_values = {
            m["y_tunnus"] for m in members if m["y_tunnus"]
        }
        canonical_ytunnus = sorted(ytunnus_values)[0] if ytunnus_values else None

        # Pick canonical org_id: prefer rows with y_tunnus, then lowest
        with_yt = [m for m in members if m["y_tunnus"]]
        if with_yt:
            canonical_id = min(m["org_id"] for m in with_yt)
        else:
            canonical_id = min(m["org_id"] for m in members)

        # Update each member
        for m in members:
            updates = []
            params = []

            if m["org_id"] != canonical_id:
                updates.append("org_id = ?")
                params.append(canonical_id)
                change_log.append(
                    f"  org_id {m['org_id']:>6} -> {canonical_id:<6}"
                    f"  [{m['source']:>10}] {m['source_name']}"
                )
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

    # --- Verify primary key integrity ---
    pk_violations = cur.execute(
        "SELECT source, source_name, COUNT(*) c FROM org_mapping"
        " GROUP BY source, source_name HAVING c > 1"
    ).fetchall()

    if pk_violations:
        print(f"\nPRIMARY KEY VIOLATIONS FOUND ({len(pk_violations)})! Rolling back.")
        conn.rollback()
        return

    # --- Verify dedup result ---
    remaining_dups = cur.execute(
        "SELECT COUNT(*) FROM ("
        "  SELECT LOWER(source_name) FROM org_mapping"
        "  GROUP BY LOWER(source_name)"
        "  HAVING COUNT(DISTINCT org_id) > 1"
        ")"
    ).fetchone()[0]

    # --- Dry-run summary ---
    print(f"\n=== CHANGES ===")
    print(f"org_id updates:   {org_id_changes}")
    print(f"y_tunnus fills:   {ytunnus_fills}")
    print(f"Remaining dups:   {remaining_dups}")

    if change_log:
        print(f"\nChange log ({len(change_log)} org_id changes):")
        for line in change_log:
            print(line)

    # Commit
    conn.commit()
    print(f"\nCommitted.")

    # --- After stats ---
    distinct_orgs_after = cur.execute(
        "SELECT COUNT(DISTINCT org_id) FROM org_mapping"
    ).fetchone()[0]
    dup_groups_after = cur.execute(
        "SELECT COUNT(*) FROM ("
        "  SELECT LOWER(source_name) FROM org_mapping"
        "  GROUP BY LOWER(source_name)"
        "  HAVING COUNT(DISTINCT org_id) > 1"
        ")"
    ).fetchone()[0]

    print(f"\n=== AFTER ===")
    print(f"Total rows:            {total_rows}")
    print(f"Distinct org_ids:      {distinct_orgs_after} (was {distinct_orgs_before}, -{distinct_orgs_before - distinct_orgs_after})")
    print(f"Duplicate name groups: {dup_groups_after} (was {dup_groups_before})")

    # Example verification
    print(f"\n=== EXAMPLE: Suomen Punainen Risti ===")
    for r in cur.execute(
        "SELECT org_id, source, source_name, y_tunnus FROM org_mapping"
        " WHERE LOWER(source_name) = 'suomen punainen risti'"
    ).fetchall():
        print(f"  org_id={r['org_id']}, source={r['source']}, name={r['source_name']}, y_tunnus={r['y_tunnus']}")

    conn.close()


if __name__ == "__main__":
    main()
