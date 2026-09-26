#!/usr/bin/env python3
"""Synkkaa valitut taulut paikallisesta SQLitestä tuotannon Supabaseen.

Korvaa kunkin taulun sisällön kokonaan YHDESSÄ transaktiossa: sivusto näkee joko
vanhan tai uuden datan, ei koskaan puolikasta. Tauluissa ei ole triggereitä eikä
vierasavaimia (tarkistettu 25.9.2026), joten DELETE + INSERT on turvallinen.
Lopuksi ajetaan refresh_org_families_stats(), koska perheiden summat on
välimuistettu org_families_cache-tauluun.

Oletuksena KUIVAHARJOITUS: näyttää rivimäärät ja summat kummassakin päässä eikä
kirjoita mitään. Kirjoitus vain lipulla --apply.

  uv run --with psycopg2-binary --with numpy scripts/sync_tables_to_supabase.py \\
      --db data/funding.db fts_grants bf_awarded org_public_contracts ...
  ... --apply

Ota ennen --applyta varmuuskopio: avustusdata/scripts/backup-supabase.sh
"""
import argparse
import sqlite3
import sys

import psycopg2
import psycopg2.extras

sys.path.insert(0, "/home/eki/avustusdata")
from migrate_to_supabase import PG_DSN  # noqa: E402  (salasana pysyy yhdessä paikassa)

# SQLite-taulu -> (Postgres-taulu, summasarake tarkistukseen tai None)
TABLES = {
    "fts_grants": ("fts_grants", "amount"),
    "bf_awarded": ("bf_grants", "total_eur"),
    "org_public_contracts": ("org_public_contracts", "value"),
    "lobbying_orgs": ("lobbying_orgs", "total_grants_eur"),
    "lobbying_topics": ("lobbying_topics", None),
    "org_mapping": ("org_mapping", None),
    "va_grants": ("va_grants", "granted_eur"),
    "eura_all": ("eura_grants", "myonnetty_eu_valtio"),
    "grants": ("stea_grants", "myonnetty"),
    "helsinki_grants": ("helsinki_grants", "myonnetty"),
    "um_grants": ("um_grants", "amount"),
    "va_enrichments": ("va_enrichments", None),
    "eura_enrichments": ("eura_enrichments", None),
    "org_family_members": ("org_family_members", None),
    "org_families": ("org_families", None),
}
# Postgresin omat sarakkeet joita SQLitessä ei ole (serial id) — jätetään kannan generoitaviksi.
PG_ONLY_OK = {"id"}
BATCH = 1000


def pg_columns(cur, table):
    cur.execute("""select column_name from information_schema.columns
                   where table_schema='public' and table_name=%s order by ordinal_position""", (table,))
    return [r[0] for r in cur.fetchall()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tables", nargs="+", choices=sorted(TABLES))
    ap.add_argument("--db", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    lite = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    pg = psycopg2.connect(PG_DSN.replace(":6543/", ":5432/"))   # session pooler: pitkä transaktio
    pg.autocommit = False
    cur = pg.cursor()

    plan = []
    for lt in a.tables:
        pt, sumcol = TABLES[lt]
        lcols = [r[1] for r in lite.execute(f'pragma table_info("{lt}")')]
        pcols = pg_columns(cur, pt)
        extra = set(pcols) - set(lcols) - PG_ONLY_OK
        missing = set(lcols) - set(pcols)
        if missing:
            sys.exit(f"{lt}: sarakkeita puuttuu Postgresista: {sorted(missing)} — pysähdytään")
        cols = [c for c in lcols if c in pcols]
        # Jos Postgresissa on serial-id jota SQLitessä ei ole (bf_grants), id = SQLiten rowid.
        # Sivuston suosikit tallentavat avustukset muodossa "BF-<id>" selaimen localStorageen,
        # joten id:t eivät saa vaihtua synkassa. Tarkistettu 25.9.2026: PG id == rowid 58594/58594.
        use_rowid = "id" in pcols and "id" not in lcols
        ln = lite.execute(f'select count(*) from "{lt}"').fetchone()[0]
        cur.execute(f'select count(*) from public."{pt}"')
        pn = cur.fetchone()[0]
        ls = ps = None
        if sumcol:
            ls = lite.execute(f'select coalesce(sum("{sumcol}"),0) from "{lt}"').fetchone()[0]
            cur.execute(f'select coalesce(sum("{sumcol}"),0) from public."{pt}"')
            ps = float(cur.fetchone()[0])
        note = f"  (PG:n omat sarakkeet jäävät oletusarvoon: {sorted(extra)})" if extra else ""
        print(f"{lt:22} -> {pt:22} rivit {pn:>7} -> {ln:>7} ({ln - pn:+})"
              + (f"  summa {ps/1e6:,.1f} -> {ls/1e6:,.1f} M€" if sumcol else "") + note)
        plan.append((lt, pt, cols, ln, use_rowid))

    if not a.apply:
        print("\nKuivaharjoitus — mitään ei kirjoitettu. Aja uudelleen lipulla --apply.")
        return

    try:
        for lt, pt, cols, ln, use_rowid in plan:
            # Alkuperäinen migraatio loi euro-sarakkeet tyyppiä `real` (~7 merkitsevää
            # numeroa) → 48 656 807 € tallentui muotoon 48 656 800 €. Korjataan tyyppi
            # ennen latausta; DDL on Postgresissa transaktionaalinen, joten rollback
            # palauttaa myös tämän.
            cur.execute("""select column_name from information_schema.columns
                           where table_schema='public' and table_name=%s and data_type='real'""", (pt,))
            for (col,) in cur.fetchall():
                cur.execute(f'alter table public."{pt}" alter column "{col}" type double precision')
                print(f"  {pt}.{col}: real -> double precision")
            cur.execute(f'delete from public."{pt}"')
            # SQLite tallentaa totuusarvot 0/1-lukuina; Postgres vaatii boolean-tyypin.
            cur.execute("""select column_name from information_schema.columns
                           where table_schema='public' and table_name=%s and data_type='boolean'""", (pt,))
            bool_cols = {r[0] for r in cur.fetchall()}
            # SQLite sallii tyhjän merkkijonon numerosarakkeessa, Postgres ei (26.9.2026:
            # EURA-tuonti kirjoitti puuttuvan summan ''-arvona NULLin sijaan).
            cur.execute("""select column_name from information_schema.columns
                           where table_schema='public' and table_name=%s
                           and data_type in ('double precision','real','numeric','integer','bigint')""", (pt,))
            num_cols = {r[0] for r in cur.fetchall()}
            offset = 1 if use_rowid else 0
            bool_idx = [i + offset for i, c in enumerate(cols) if c in bool_cols]
            num_idx = [i + offset for i, c in enumerate(cols) if c in num_cols]

            def fix(row):
                if not bool_idx and not num_idx:
                    return row
                row = list(row)
                for i in bool_idx:
                    if row[i] is not None:
                        row[i] = bool(row[i])
                for i in num_idx:
                    if row[i] == "":
                        row[i] = None
                return row

            collist = ",".join(f'"{c}"' for c in cols)
            src_cols = ("rowid, " if use_rowid else "") + collist
            dst_cols = ('"id", ' if use_rowid else "") + collist
            sel = lite.execute(f'select {src_cols} from "{lt}" order by rowid')
            while True:
                rows = sel.fetchmany(BATCH)
                if not rows:
                    break
                psycopg2.extras.execute_values(
                    cur, f'insert into public."{pt}" ({dst_cols}) values %s',
                    [fix(r) for r in rows], page_size=BATCH)
            # Id:t tuodaan eksplisiittisesti, joten serial-laskuri pitää siirtää niiden perään —
            # muuten seuraava sivuston/skriptin lisäys törmäisi olemassa olevaan id:hen.
            if use_rowid or "id" in cols:
                cur.execute(f"""select pg_get_serial_sequence('public."{pt}"','id')""")
                seq = cur.fetchone()[0]
                if seq:
                    cur.execute(f'select setval(%s, (select coalesce(max(id),1) from public."{pt}"))', (seq,))
            cur.execute(f'select count(*) from public."{pt}"')
            got = cur.fetchone()[0]
            if got != ln:
                raise RuntimeError(f"{pt}: odotettiin {ln} riviä, tuli {got}")
            print(f"  {pt}: {got} riviä ok")
        cur.execute("select refresh_org_families_stats()")
        pg.commit()
        print("COMMIT — Supabase päivitetty, perheiden tilastot laskettu uudelleen.")
    except Exception as e:
        pg.rollback()
        sys.exit(f"ROLLBACK — mitään ei muutettu: {e}")


if __name__ == "__main__":
    main()
