#!/usr/bin/env python3
"""Import FTS (EU Financial Transparency System) data into ralssi funding.db.

Reads: /home/eki/avustusdata-analysis/fts_all_years_finland_ngo.csv (4652 rows, 2007-2024)
Writes: fts_grants table in data/funding.db

This is Finnish NGO/NFPO data from EU direct funding programmes.
"""

import csv
import os
import re
import sqlite3

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)
DB_PATH = os.path.join(ROOT, "data", "funding.db")
CSV_PATH = "/home/eki/avustusdata-analysis/fts_all_years_finland_ngo.csv"


def vat_to_ytunnus(vat: str) -> str | None:
    """Convert Finnish VAT number to Y-tunnus format.

    FI12345678 -> 1234567-8 (7 digits + dash + check digit)
    """
    if not vat or vat == "-":
        return None
    vat = vat.strip()
    if vat.startswith("FI") and len(vat) == 10:
        digits = vat[2:]  # 8 digits
        if digits.isdigit():
            return f"{digits[:7]}-{digits[7]}"
    return None


def main():
    if not os.path.exists(CSV_PATH):
        print(f"CSV not found: {CSV_PATH}")
        return

    print(f"Reading {CSV_PATH} ...")

    rows_to_insert = []
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            year = int(row["year"]) if row.get("year") else None
            organisation = row.get("name", "").strip()
            programme = row.get("programme", "").strip()
            vat_number = row.get("vat_number", "").strip()
            y_tunnus = vat_to_ytunnus(vat_number)

            try:
                amount = float(row["amount_eur"]) if row.get("amount_eur") else 0.0
            except (ValueError, TypeError):
                amount = 0.0

            is_ngo = 1 if row.get("is_ngo") == "Yes" else 0
            is_nfpo = 1 if row.get("is_nfpo") == "Yes" else 0
            responsible_department = row.get("department", "").strip()
            expense_type = row.get("funding_type", "").strip()
            beneficiary_type = row.get("beneficiary_type", "").strip()

            # amount_secondary_eur not stored in main amount, keep as separate info if needed
            # We store primary amount_eur

            if vat_number == "-":
                vat_number = None

            rows_to_insert.append((
                year,
                programme,
                organisation,
                vat_number,
                y_tunnus,
                amount,
                is_ngo,
                is_nfpo,
                responsible_department,
                expense_type,
                beneficiary_type,
            ))

    print(f"Parsed {len(rows_to_insert)} rows")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("DROP TABLE IF EXISTS fts_grants")
    cur.execute("""
        CREATE TABLE fts_grants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            year INTEGER,
            programme TEXT,
            organisation TEXT,
            vat_number TEXT,
            y_tunnus TEXT,
            amount REAL,
            is_ngo INTEGER,
            is_nfpo INTEGER,
            responsible_department TEXT,
            expense_type TEXT,
            beneficiary_type TEXT
        )
    """)

    cur.executemany(
        "INSERT INTO fts_grants (year, programme, organisation, vat_number, y_tunnus, "
        "amount, is_ngo, is_nfpo, responsible_department, expense_type, beneficiary_type) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows_to_insert,
    )

    cur.execute("CREATE INDEX IF NOT EXISTS idx_fts_grants_yt ON fts_grants(y_tunnus)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_fts_grants_org ON fts_grants(organisation)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_fts_grants_year ON fts_grants(year)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_fts_grants_programme ON fts_grants(programme)")

    conn.commit()

    # Print summary
    print(f"\n=== Import complete ===")
    print(f"Total rows: {cur.execute('SELECT COUNT(*) FROM fts_grants').fetchone()[0]}")
    total_eur = cur.execute("SELECT SUM(amount) FROM fts_grants").fetchone()[0]
    print(f"Total EUR: {total_eur:,.0f}")
    print(f"Unique orgs: {cur.execute('SELECT COUNT(DISTINCT organisation) FROM fts_grants').fetchone()[0]}")
    print(f"With Y-tunnus: {cur.execute('SELECT COUNT(*) FROM fts_grants WHERE y_tunnus IS NOT NULL').fetchone()[0]}")

    print(f"\nYear range:")
    for row in cur.execute(
        "SELECT MIN(year), MAX(year) FROM fts_grants"
    ).fetchall():
        print(f"  {row[0]} - {row[1]}")

    print(f"\nPer year:")
    for row in cur.execute(
        "SELECT year, COUNT(*), SUM(amount) FROM fts_grants "
        "GROUP BY year ORDER BY year"
    ).fetchall():
        print(f"  {row[0]}: {row[1]:>5} rows  {row[2]:>14,.0f}€")

    print(f"\nNGO: {cur.execute('SELECT COUNT(*) FROM fts_grants WHERE is_ngo = 1').fetchone()[0]}")
    print(f"NFPO: {cur.execute('SELECT COUNT(*) FROM fts_grants WHERE is_nfpo = 1').fetchone()[0]}")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
