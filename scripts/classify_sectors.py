"""
Classify org_mapping rows by sector based on name heuristics.
Idempotent: can be re-run safely (drops and re-adds column if exists).
"""

import sqlite3
import re

DB_PATH = "/home/eki/ralssi/data/funding.db"


def classify_sector(name: str) -> str | None:
    """Classify organization sector based on name patterns."""
    # Normalize for matching (keep original case patterns too)
    lower = name.lower().strip()

    # === THIRD SECTOR MARKERS (always win) ===

    # Foundation markers
    foundation_patterns = [
        r'\bsr\b',           # sr as word
        r'säätiö',           # anywhere (compound words like Autismisäätiö)
        r'saatio',           # transliterated
        r'saeaetioe',        # another transliteration
        r'stiftelse',        # Swedish
        r'\bfoundation\b',   # English
    ]
    for pat in foundation_patterns:
        if re.search(pat, lower):
            return 'foundation'

    # Cooperative markers
    coop_patterns = [
        r'\bosk\b',          # osk as word
        r'\bosuuskunta\b',   # osuuskunta as word
    ]
    for pat in coop_patterns:
        if re.search(pat, lower):
            return 'cooperative'

    # Association markers
    assoc_patterns = [
        r'\bry\b',           # ry as word
        r'\brf\b',           # rf as word
        r'\br\.y\.\b',       # r.y. with dots
        r'\br\.y\.?$',       # r.y or r.y. at end
        r'r\.y\.',           # r.y. anywhere
        r'\br\.f\.\b',       # r.f. with dots
        r'\br\.f\.?$',       # r.f or r.f. at end
        r'r\.f\.',           # r.f. anywhere
        r'liitto',           # anywhere (compound: nuorisoliitto)
        r'yhdistys',         # anywhere
        r'\bassociation\b',  # English
        r'förbund',          # Swedish
        r'förening',         # Swedish
        r'seura\b',          # seura at word END (not seurakunta)
        r'punainen risti',   # Red Cross
        r'röda korset',      # Red Cross Swedish
    ]
    for pat in assoc_patterns:
        if re.search(pat, lower):
            return 'association'

    # === EXCLUDED PATTERNS (only if no third-sector marker matched) ===

    # University / education
    if re.search(r'yliopisto|university|universitet|akademi', lower):
        return 'university'
    if re.search(r'korkeakoulu|ammattikorkeakoulu|ammattiopisto', lower):
        return 'university'

    # Research institutes
    if re.search(r'teknologian tutkimuskeskus', lower):
        return 'research'
    if re.match(r'^vtt\b', lower):
        return 'research'
    if re.match(r'^csc\b', lower):
        return 'research'

    # Government - Business Finland
    if re.match(r'^business finland', lower):
        return 'government'

    # Church
    if re.search(r'seurakunta|församling|seurakuntayhtymä', lower):
        return 'church'

    # Government - municipalities and similar
    if re.search(r'kaupunki|\bstad\b|\bkommun\b|\bkunta\b', lower):
        return 'government'
    if re.search(r'ministeriö|ministry', lower):
        return 'government'
    if re.search(r'kuntayhtymä|samkommun', lower):
        return 'government'
    if re.search(r'liikelaitos', lower):
        return 'government'
    if re.search(r'hyvinvointialue|välfärdsområde', lower):
        return 'government'
    if re.search(r'\bely-keskus|\bte-toimisto|\bkela\b|\bfpa\b', lower):
        return 'government'

    # Company markers
    # Oy/Oyj/Ab/Ltd/Gmbh at end, or ^Oy at start
    if re.search(r'\b(oy|oyj|ab|ltd|gmbh)\s*$', lower):
        return 'company'
    if re.match(r'^oy\s', lower):
        return 'company'
    # Ky, Tmi at end
    if re.search(r'\b(ky|tmi)\s*$', lower):
        return 'company'
    # "avoin yhtiö" anywhere
    if re.search(r'avoin yhti', lower):
        return 'company'
    # "osakeyhtiö" as standalone word
    if re.search(r'osakeyhtiö', lower):
        return 'company'

    return None


def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Add column if not exists
    columns = [row[1] for row in cur.execute("PRAGMA table_info(org_mapping)").fetchall()]
    if 'sector' not in columns:
        cur.execute("ALTER TABLE org_mapping ADD COLUMN sector TEXT")
        conn.commit()
        print("Added 'sector' column to org_mapping.")
    else:
        # Reset all values for idempotent re-run
        cur.execute("UPDATE org_mapping SET sector = NULL")
        conn.commit()
        print("Reset existing 'sector' column to NULL.")

    # Fetch all rows
    rows = cur.execute("SELECT rowid, source_name FROM org_mapping").fetchall()
    print(f"Total rows to classify: {len(rows)}")

    # Classify in batches
    batch_size = 5000
    updates = []
    for rowid, name in rows:
        sector = classify_sector(name)
        if sector:
            updates.append((sector, rowid))

    # Apply updates in batches
    for i in range(0, len(updates), batch_size):
        batch = updates[i:i + batch_size]
        cur.executemany("UPDATE org_mapping SET sector = ? WHERE rowid = ?", batch)
        conn.commit()

    print(f"\nClassified {len(updates)} rows out of {len(rows)} total.")
    print(f"Remaining NULL: {len(rows) - len(updates)}")

    # Report counts per sector
    print("\n--- Sector distribution ---")
    results = cur.execute(
        "SELECT sector, COUNT(*) FROM org_mapping GROUP BY sector ORDER BY COUNT(*) DESC"
    ).fetchall()
    for sector, count in results:
        print(f"  {sector or 'NULL':15s} {count:>6}")

    conn.close()


if __name__ == "__main__":
    main()
