#!/usr/bin/env python3
"""Update STEA 2026 grant decisions from the public API.

Crawls https://avustukset.stea.fi/api/organisation/{id} for each org,
matches grants by (jarjesto, vuosi, haettu) and updates myonnetty + ehdotettu
in the local SQLite database.

Safe: only UPDATEs existing rows (myonnetty/ehdotettu fields), never inserts or deletes.
Repeatable: can be re-run — idempotent updates.
"""

import sqlite3
import json
import time
import sys
import urllib.request
import urllib.error
from collections import defaultdict

DB = "/home/eki/ralssi/data/funding.db"
STEA_API = "https://avustukset.stea.fi/api/organisation"
TARGET_YEAR = 2026
MAX_ORG_ID = 2500
DELAY = 0.1  # seconds between API requests

def fetch_org(org_id):
    """Fetch org data from STEA API. Returns None if org doesn't exist."""
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

def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    # Current state
    cur = conn.execute(
        "SELECT COUNT(*) as cnt, SUM(myonnetty) as total FROM grants WHERE vuosi=?",
        (TARGET_YEAR,)
    )
    row = cur.fetchone()
    print(f"Current state: {row['cnt']} rows for {TARGET_YEAR}, total myönnetty={row['total']}")

    # Collect all 2026 grants from API
    api_grants = []
    orgs_found = 0
    orgs_with_2026 = 0

    print(f"Crawling STEA API org IDs 1-{MAX_ORG_ID}...")
    for org_id in range(1, MAX_ORG_ID + 1):
        data = fetch_org(org_id)
        if data is None:
            continue

        orgs_found += 1
        org_name = data["name"]
        grants_2026 = [g for g in data.get("allAidTargets", []) if g["year"] == TARGET_YEAR]

        if grants_2026:
            orgs_with_2026 += 1
            for g in grants_2026:
                api_grants.append({
                    "jarjesto": g.get("organisationName") or org_name,
                    "vuosi": TARGET_YEAR,
                    "haettu": g["requested"],
                    "ehdotettu": g["proposed"],
                    "myonnetty": g["granted"],
                    "purpose": (g.get("purpose") or "")[:100],
                })

        if org_id % 100 == 0:
            print(f"  ...org_id {org_id}: {orgs_found} orgs found, {len(api_grants)} grants collected")

        time.sleep(DELAY)

    print(f"\nAPI crawl complete: {orgs_found} orgs, {orgs_with_2026} with {TARGET_YEAR} data, {len(api_grants)} total grants")

    # Match and update
    updated = 0
    not_matched = 0
    already_correct = 0

    for ag in api_grants:
        # Match by jarjesto + vuosi + haettu (should be unique)
        cur = conn.execute(
            "SELECT rowid as rid, myonnetty, ehdotettu FROM grants WHERE jarjesto=? AND vuosi=? AND haettu=?",
            (ag["jarjesto"], ag["vuosi"], ag["haettu"])
        )
        rows = cur.fetchall()

        if len(rows) == 0:
            not_matched += 1
            continue
        elif len(rows) > 1:
            # Multiple matches — try purpose prefix to disambiguate
            cur2 = conn.execute(
                "SELECT rowid as rid, myonnetty, ehdotettu FROM grants WHERE jarjesto=? AND vuosi=? AND haettu=? AND substr(kayttotarkoitus,1,100)=?",
                (ag["jarjesto"], ag["vuosi"], ag["haettu"], ag["purpose"])
            )
            rows2 = cur2.fetchall()
            if len(rows2) == 1:
                rows = rows2
            elif ag["myonnetty"] == rows[0]["myonnetty"]:
                # All duplicates get same value — safe to update first
                rows = [rows[0]]
            else:
                not_matched += 1
                continue

        row = rows[0]
        if row["myonnetty"] == ag["myonnetty"] and row["ehdotettu"] == ag["ehdotettu"]:
            already_correct += 1
            continue

        conn.execute(
            "UPDATE grants SET myonnetty=?, ehdotettu=? WHERE rowid=?",
            (ag["myonnetty"], ag["ehdotettu"], row["rid"])
        )
        updated += 1

    conn.commit()

    # Final state
    cur = conn.execute(
        "SELECT COUNT(*) as cnt, SUM(myonnetty) as total FROM grants WHERE vuosi=?",
        (TARGET_YEAR,)
    )
    row = cur.fetchone()

    print(f"\nResults:")
    print(f"  Updated: {updated}")
    print(f"  Already correct: {already_correct}")
    print(f"  Not matched: {not_matched}")
    print(f"  New state: {row['cnt']} rows, total myönnetty={row['total']}")

    conn.close()

if __name__ == "__main__":
    main()
