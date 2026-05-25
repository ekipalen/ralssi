#!/usr/bin/env python3
"""Sync STEA updates (y_tunnus + 2026 decisions) from local SQLite to Supabase.

Only UPDATEs existing rows in stea_grants by id. No inserts, no deletes.
Safe to re-run (idempotent).

Rollback if needed:
  UPDATE stea_grants SET y_tunnus = '' WHERE y_tunnus != '';
  UPDATE stea_grants SET myonnetty = 0, ehdotettu = 0 WHERE vuosi = 2026;
"""

import sqlite3
import json
import urllib.request
import urllib.error
import sys

DB = "/home/eki/ralssi/data/funding.db"
SUPABASE_URL = "https://offrdvmodqojyrdldwmg.supabase.co/rest/v1"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im9mZnJkdm1vZHFvanlyZGxkd21nIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3OTM3Njk4MCwiZXhwIjoyMDk0OTUyOTgwfQ.6x7VC3VyLp9TrH9mwmvB1fcKkubE5zZ2qlK_37t1vAU"
BATCH_SIZE = 500


def supabase_patch(filter_params, body):
    """PATCH stea_grants with filter. Returns response status."""
    url = f"{SUPABASE_URL}/stea_grants?{filter_params}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="PATCH")
    req.add_header("apikey", SUPABASE_KEY)
    req.add_header("Authorization", f"Bearer {SUPABASE_KEY}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Prefer", "return=minimal")
    with urllib.request.urlopen(req) as resp:
        return resp.status


def supabase_get_count(filter_params):
    """GET count of matching rows."""
    url = f"{SUPABASE_URL}/stea_grants?select=id&{filter_params}"
    req = urllib.request.Request(url)
    req.add_header("apikey", SUPABASE_KEY)
    req.add_header("Authorization", f"Bearer {SUPABASE_KEY}")
    req.add_header("Prefer", "count=exact")
    req.add_header("Range", "0-0")
    with urllib.request.urlopen(req) as resp:
        return int(resp.headers.get("content-range", "*/0").split("/")[-1])


def phase1_ytunnus(conn):
    """Update y_tunnus in Supabase from SQLite, batch by id."""
    print("=== Phase 1: y_tunnus sync ===")

    rows = conn.execute(
        "SELECT id, y_tunnus FROM grants WHERE y_tunnus IS NOT NULL AND y_tunnus != ''"
    ).fetchall()

    updated = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        for row in batch:
            supabase_patch(f"id=eq.{row['id']}", {"y_tunnus": row["y_tunnus"]})
            updated += 1

        print(f"  ...{updated}/{len(rows)}")

    print(f"  Updated {updated} rows with y_tunnus")
    return updated


def phase1_ytunnus_fast(conn):
    """Update y_tunnus in Supabase — batch by jarjesto (fewer API calls)."""
    print("=== Phase 1: y_tunnus sync ===")

    rows = conn.execute(
        "SELECT DISTINCT jarjesto, y_tunnus FROM grants WHERE y_tunnus IS NOT NULL AND y_tunnus != ''"
    ).fetchall()

    updated = 0
    for row in rows:
        jarjesto = row["jarjesto"]
        yt = row["y_tunnus"]
        filter_p = f"jarjesto=eq.{urllib.request.quote(jarjesto)}&y_tunnus=neq.{urllib.request.quote(yt)}"
        try:
            supabase_patch(filter_p, {"y_tunnus": yt})
            updated += 1
        except urllib.error.HTTPError as e:
            print(f"  ERROR {jarjesto}: {e}")

        if updated % 100 == 0 and updated > 0:
            print(f"  ...{updated}/{len(rows)} orgs")

    print(f"  Processed {updated} orgs")
    return updated


def phase2_decisions(conn):
    """Update 2026 myonnetty + ehdotettu by id."""
    print("\n=== Phase 2: 2026 decisions sync ===")

    rows = conn.execute(
        "SELECT id, myonnetty, ehdotettu FROM grants WHERE vuosi = 2026 AND (myonnetty != 0 OR ehdotettu != 0)"
    ).fetchall()

    updated = 0
    for row in rows:
        supabase_patch(
            f"id=eq.{row['id']}",
            {"myonnetty": row["myonnetty"], "ehdotettu": row["ehdotettu"]},
        )
        updated += 1
        if updated % 100 == 0:
            print(f"  ...{updated}/{len(rows)}")

    print(f"  Updated {updated} rows with decisions")
    return updated


def phase3_verify():
    """Verify Supabase state matches SQLite."""
    print("\n=== Phase 3: Verification ===")

    yt_count = supabase_get_count("y_tunnus=neq.&y_tunnus=not.is.null")
    print(f"  Supabase rows with y_tunnus: {yt_count}")

    decisions_count = supabase_get_count("vuosi=eq.2026&myonnetty=gt.0")
    print(f"  Supabase 2026 with myonnetty>0: {decisions_count}")

    total = supabase_get_count("vuosi=eq.2026")
    print(f"  Supabase 2026 total: {total}")


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    print("Pre-check Supabase:")
    pre_yt = supabase_get_count("y_tunnus=neq.&y_tunnus=not.is.null")
    pre_dec = supabase_get_count("vuosi=eq.2026&myonnetty=gt.0")
    print(f"  y_tunnus filled: {pre_yt}")
    print(f"  2026 myonnetty>0: {pre_dec}\n")

    phase1_ytunnus_fast(conn)
    phase2_decisions(conn)
    phase3_verify()

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
