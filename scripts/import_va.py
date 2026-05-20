#!/usr/bin/env python3
"""Import valtionavustukset (haeavustuksia.fi) data into ralssi funding.db.

Reads: data/okm/Myönteiset päätökset.xlsx
Writes: va_grants table in data/funding.db

Filters:
  - Only agreed myöntäjät (OKM, OPH, Akatemia, TEM, UM, STM, THL, VNK, OM, YM)
  - OPH: only ry/rf/sr/säätiö recipients (no municipalities, universities, koulutuskuntayhtymä)
  - Requires Y-tunnus in saaja field
"""

import os
import re
import sqlite3
import sys

try:
    import openpyxl
except ImportError:
    print("openpyxl required: uv pip install openpyxl", file=sys.stderr)
    sys.exit(1)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)
DB_PATH = os.path.join(ROOT, "data", "funding.db")
XLSX_PATH = os.path.join(ROOT, "data", "okm", "Myönteiset päätökset.xlsx")

YT_RE = re.compile(r"\((\d{7}-\d)\)")

INCLUDE_MYONTAJAT = {
    "Suomen Akatemia",
    "Työ- ja elinkeinoministeriö",
    "Ulkoministeriö",
    "Opetus- ja kulttuuriministeriö",
    "Opetushallitus",
    "Sosiaali- ja terveysministeriö",
    "Terveyden ja hyvinvoinnin laitos",
    "Valtioneuvoston kanslia",
    "Oikeusministeriö",
    "Ympäristöministeriö",
}

OPH_SKIP_PATTERNS = [
    "yliopisto", "universitet", "korkeakoulu", "yrkeshögskola",
    "koulutuskuntayhtymä", "koulutusyhtymä", "ammattiopisto",
    "koulutuskeskus", "opisto",
    "kaupunki", "kunta ", " stad", " kommun",
]


def is_oph_ry_sr(name_lower):
    if any(p in name_lower for p in OPH_SKIP_PATTERNS):
        return False
    return any(x in name_lower for x in [" ry", " rf", " sr", "säätiö"])


def _parse_eu_varat(val):
    if not val:
        return 0
    s = str(val).strip()
    if not s or s.startswith("http"):
        return 0
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0


def parse_saaja(saaja_str):
    m = YT_RE.search(saaja_str)
    if not m:
        return None, None
    yt = m.group(1)
    name = saaja_str[:m.start()].strip()
    return name, yt


def main():
    if not os.path.exists(XLSX_PATH):
        print(f"Excel not found: {XLSX_PATH}", file=sys.stderr)
        sys.exit(1)

    print(f"Reading {XLSX_PATH} ...")
    wb = openpyxl.load_workbook(XLSX_PATH, read_only=True)
    ws = wb["Export"]

    rows_to_insert = []
    skipped = {"no_yt": 0, "wrong_myontaja": 0, "oph_filtered": 0, "empty": 0}

    for row in ws.iter_rows(min_row=2, values_only=True):
        vals = list(row)
        if len(vals) < 10 or all(v is None for v in vals):
            skipped["empty"] += 1
            continue

        pvm, saaja, myontaja, asianro, haettu, myonnetty, eu_varat, kayttotarkoitus, haun_nimi, alueet = vals[:10]
        myontaja_s = str(myontaja or "").strip()

        if myontaja_s not in INCLUDE_MYONTAJAT:
            skipped["wrong_myontaja"] += 1
            continue

        saaja_s = str(saaja or "")
        org_name, y_tunnus = parse_saaja(saaja_s)
        if not y_tunnus:
            skipped["no_yt"] += 1
            continue

        if myontaja_s == "Opetushallitus" and not is_oph_ry_sr(org_name.lower()):
            skipped["oph_filtered"] += 1
            continue

        year = None
        decision_date = None
        if pvm:
            try:
                year = pvm.year
                decision_date = pvm.strftime("%Y-%m-%d")
            except Exception:
                pass

        rows_to_insert.append((
            org_name,
            y_tunnus,
            myontaja_s,
            year,
            float(haettu or 0),
            float(myonnetty or 0),
            _parse_eu_varat(eu_varat),
            str(kayttotarkoitus or "").strip(),
            str(haun_nimi or "").strip(),
            str(alueet or "").strip(),
            str(asianro or "").strip(),
            decision_date,
        ))

    wb.close()
    print(f"Parsed {len(rows_to_insert)} rows to import")
    print(f"Skipped: {skipped}")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("DROP TABLE IF EXISTS va_grants")
    cur.execute("""
        CREATE TABLE va_grants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organisation TEXT NOT NULL,
            y_tunnus TEXT NOT NULL,
            grantor TEXT NOT NULL,
            year INTEGER,
            applied_eur REAL,
            granted_eur REAL,
            eu_eur REAL DEFAULT 0,
            purpose TEXT,
            call_name TEXT,
            region TEXT,
            case_number TEXT,
            decision_date TEXT
        )
    """)

    cur.executemany(
        "INSERT INTO va_grants (organisation, y_tunnus, grantor, year, applied_eur, "
        "granted_eur, eu_eur, purpose, call_name, region, case_number, decision_date) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows_to_insert,
    )

    cur.execute("CREATE INDEX IF NOT EXISTS idx_va_grants_yt ON va_grants(y_tunnus)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_va_grants_org ON va_grants(organisation)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_va_grants_grantor ON va_grants(grantor)")

    conn.commit()

    # Print summary
    print(f"\n=== Import complete ===")
    print(f"Total rows: {cur.execute('SELECT COUNT(*) FROM va_grants').fetchone()[0]}")
    print(f"Total EUR: {cur.execute('SELECT SUM(granted_eur) FROM va_grants').fetchone()[0]:,.0f}")
    print(f"Unique orgs: {cur.execute('SELECT COUNT(DISTINCT y_tunnus) FROM va_grants').fetchone()[0]}")
    print(f"\nPer grantor:")
    for row in cur.execute(
        "SELECT grantor, COUNT(*), SUM(granted_eur), COUNT(DISTINCT y_tunnus) "
        "FROM va_grants GROUP BY grantor ORDER BY SUM(granted_eur) DESC"
    ).fetchall():
        print(f"  {row[0]:<45} {row[1]:>5} rows  {row[2]:>12,.0f}€  {row[3]:>4} orgs")

    print(f"\nYear range:")
    for row in cur.execute(
        "SELECT year, COUNT(*), SUM(granted_eur) FROM va_grants "
        "WHERE year IS NOT NULL GROUP BY year ORDER BY year"
    ).fetchall():
        print(f"  {row[0]}: {row[1]:>5} rows  {row[2]:>12,.0f}€")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
