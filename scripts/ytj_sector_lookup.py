"""
YTJ/PRH API lookup for org_mapping rows where sector IS NULL and y_tunnus is valid.
Uses the PRH Opendata YTJ API v3.

Idempotent: skips rows already classified. Rate limited to 1 req/sec.
"""

import sqlite3
import time
import requests

DB_PATH = "/home/eki/ralssi/data/funding.db"
API_URL = "https://avoindata.prh.fi/opendata-ytj-api/v3/companies"

# Map company form descriptions (Finnish) to sector
FORM_TO_SECTOR = {
    "Osakeyhtiö": "company",
    "Julkinen osakeyhtiö": "company",
    "Kommandiittiyhtiö": "company",
    "Avoin yhtiö": "company",
    "Keskinäinen kiinteistöosakeyhtiö": "company",
    "Keskinäinen vakuutusyhtiö": "company",
    "Julkinen keskinäinen vakuutusyhtiö": "company",
    "Vakuutusosakeyhtiö": "company",
    "Julkinen vakuutusosakeyhtiö": "company",
    "Vakuutusyhdistys": "company",
    "Sivuliike": "company",
    "Eurooppayhtiö": "company",
    "Euroopp.taloudell.etuyht.sivutoimipaikka": "company",
    "Eurooppalainen taloudellinen etuyhtymä": "company",
    "Asunto-osakeyhtiö": "company",
    "Säätiö": "foundation",
    "Säästöpankki": "foundation",
    "Hypoteekkiyhdistys": "foundation",
    "Osuuskunta": "cooperative",
    "Osuuspankki": "cooperative",
    "Eurooppaosuuskunta": "cooperative",
    "Eurooppaosuuspankki": "cooperative",
    "Aatteellinen yhdistys": "association",
    "Asumisoikeusyhdistys": "association",
    "Taloudellinen yhdistys": "association",
    "Valtion liikelaitos": "government",
    "Asukashallintoalue": "government",
}

# Also map by companyForm type code
TYPE_TO_SECTOR = {
    "OY": "company", "OYJ": "company", "KY": "company", "AY": "company",
    "KOY": "company", "KVY": "company", "KVJ": "company", "VOY": "company",
    "VOJ": "company", "VY": "company", "SL": "company", "SE": "company",
    "ETS": "company", "ETY": "company", "AOY": "company",
    "SÄÄ": "foundation", "SP": "foundation", "HY": "foundation",
    "OK": "cooperative", "OP": "cooperative", "SCE": "cooperative", "SCP": "cooperative",
    "AYH": "association", "ASY": "association", "TYH": "association",
    "VALTLL": "government", "ASH": "government",
}


def get_sector_from_response(data: dict) -> str | None:
    """Extract sector from YTJ API response."""
    if not data.get("companies"):
        return None

    company = data["companies"][0]
    forms = company.get("companyForms", [])
    if not forms:
        return None

    form = forms[0]  # Most recent

    # Try by type code first
    form_type = form.get("type", "")
    # Type is a number string in v3, map from description
    descriptions = form.get("descriptions", [])
    fi_desc = None
    for desc in descriptions:
        if desc.get("languageCode") == "1":
            fi_desc = desc.get("description", "")
            break

    if fi_desc and fi_desc in FORM_TO_SECTOR:
        return FORM_TO_SECTOR[fi_desc]

    # Fallback: try partial matching
    if fi_desc:
        lower_desc = fi_desc.lower()
        if "osakeyhtiö" in lower_desc or "vakuutus" in lower_desc:
            return "company"
        if "säätiö" in lower_desc:
            return "foundation"
        if "osuus" in lower_desc:
            return "cooperative"
        if "yhdistys" in lower_desc:
            return "association"

    return None


def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Get distinct y_tunnus values where sector is NULL
    rows = cur.execute("""
        SELECT DISTINCT y_tunnus
        FROM org_mapping
        WHERE sector IS NULL
          AND y_tunnus IS NOT NULL
          AND y_tunnus != ''
          AND y_tunnus != '-'
    """).fetchall()

    total = len(rows)
    print(f"YTJ lookup: {total} distinct Y-tunnus values to check")

    found = 0
    not_found = 0
    errors = 0

    session = requests.Session()
    session.headers.update({"Accept": "application/json"})

    for i, (y_tunnus,) in enumerate(rows):
        if (i + 1) % 100 == 0:
            print(f"  Progress: {i+1}/{total} (found={found}, not_found={not_found}, errors={errors})")

        try:
            resp = session.get(API_URL, params={"businessId": y_tunnus}, timeout=10)

            if resp.status_code == 429:
                # Rate limited, wait and retry
                print(f"  Rate limited at {i+1}, waiting 10s...")
                time.sleep(10)
                resp = session.get(API_URL, params={"businessId": y_tunnus}, timeout=10)

            if resp.status_code == 200:
                data = resp.json()
                sector = get_sector_from_response(data)
                if sector:
                    cur.execute(
                        "UPDATE org_mapping SET sector = ? WHERE y_tunnus = ? AND sector IS NULL",
                        (sector, y_tunnus)
                    )
                    conn.commit()
                    found += 1
                else:
                    not_found += 1
            elif resp.status_code == 404:
                not_found += 1
            else:
                errors += 1
                if errors <= 5:
                    print(f"  HTTP {resp.status_code} for {y_tunnus}")

        except requests.exceptions.RequestException as e:
            errors += 1
            if errors <= 5:
                print(f"  Error for {y_tunnus}: {e}")

        time.sleep(1)  # Rate limit: 1 req/sec

    print(f"\nDone! Found: {found}, Not found: {not_found}, Errors: {errors}")

    # Final stats
    print("\n--- Updated sector distribution ---")
    results = cur.execute(
        "SELECT sector, COUNT(*) FROM org_mapping GROUP BY sector ORDER BY COUNT(*) DESC"
    ).fetchall()
    for sector, count in results:
        print(f"  {sector or 'NULL':15s} {count:>6}")

    conn.close()


if __name__ == "__main__":
    main()
