# Rälssi — Finnish Public Funding Explorer

A single-file CLI tool that brings together eight Finnish public funding databases
into one searchable view. 208K grant rows totaling 48 billion euros — from STEA
grants to EU structural funds to state grants and development cooperation — all
cross-referenced by organization.

Public funding data is scattered across separate systems with different formats,
making it hard to see the full picture. Rälssi combines these sources and can
surface patterns like organizations receiving funding from multiple channels,
helping make public spending more transparent and easier to explore.

Note: only publicly available data sources are included. A significant share of
Finnish public funding is distributed by agencies and municipalities that do not
publish their grant data openly, so this is far from a complete picture.

Designed to be used with **Claude Code**, **Codex**, or **OpenClaw** as the primary interface —
ask questions in natural language, the AI runs the commands.

## Setup

**1. Install uv** (installs Python automatically):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Windows: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

**2. Clone and download data:**

```bash
git clone https://github.com/ekipalen/ralssi.git
cd ralssi
uv run ralssi.py setup
```

The `setup` command downloads the database and data files (~150 MB) from the GitHub release.
Data files are not included in the repo due to size (~330 MB uncompressed).

**3. Verify:**

```bash
uv run ralssi.py sources
```

**Optional:**

```bash
sudo apt install sqlite3       # Direct DB queries (Debian/Ubuntu)
brew install sqlite3            # macOS
```

numpy (vector similarity search) is installed automatically by `uv run` via PEP 723 metadata.

## What's in the repo

| File | Purpose |
|------|---------|
| `ralssi.py` | Single-file CLI, zero mandatory deps (Python 3.9+ stdlib) |
| `AGENTS.md` | AI interface guide (auto-loaded by Claude Code and Codex) |
| `SOURCES.md` | Data provenance and verification documentation |
| `data/` | Downloaded via `setup` — SQLite DB, embeddings, raw source files |

## Data sources

| Source | Rows | Total | Description |
|--------|------|-------|-------------|
| STEA | 26,487 | 3.48B | Järjestöavustukset (incl. rejected applications) |
| RAY | 55,884 | 5.0B | Raha-automaattiyhdistys grants 2000–2016 (predecessor to STEA) |
| EURA | 19,878 | 4.3B | EU structural funds 2014–2029 |
| BF | 58,594 | 11.5B | Business Finland research and innovation funding |
| UM/IATI | 23,301 | 18.3B | Development cooperation |
| Helsinki | 11,037 | 366M | Municipal grants |
| VA | 8,537 | 3.7B | Valtionavustukset — OKM, Akatemia, TEM, STM, THL, UM, VNK, OM, YM, OPH |
| FTS | 4,652 | 1.5B | EU Financial Transparency System — direct EU payments to Finnish organisations |

Note: RAY (2000–2016) per-organisation totals are a lower bound, as ~24% of RAY euros are not yet linked to an organisation.

## Quick start

```bash
uv run ralssi.py sources                # Overview of all data sources
uv run ralssi.py org "Punainen Risti"   # Cross-source org search
uv run ralssi.py top stea              # Biggest STEA recipients
uv run ralssi.py hunters               # Orgs receiving from multiple sources
uv run ralssi.py hunters -v            # With per-source breakdowns
uv run ralssi.py verify "SPR"          # Verify against raw source files
uv run ralssi.py sql "SELECT source_name, source, org_id FROM org_mapping WHERE source_name LIKE '%Punainen Risti%'"
```

All commands except `verify` support `--json` for machine-readable output.

## Third-sector filter

By default, results are filtered to show only third-sector organizations (associations,
foundations, cooperatives, church). This excludes companies, universities, research
institutes, government bodies, and international organizations.

```bash
uv run ralssi.py top eura -n 10                    # Third-sector only (default)
uv run ralssi.py --no-third-sector top eura -n 10  # All organizations
```

The filter uses the `sector` column in `org_mapping`. For names not in the mapping,
a name-based heuristic is applied (e.g., names containing Oy, Ab, kaupunki, yliopisto).

## Commands

| Command | Description |
|---------|-------------|
| `org <name>` | Cross-source org search (`--merge`, `--detail`, `--source`, `--contracts` adds a public-contracts section) |
| `profile <name>` | Year x source funding matrix (`--merge` to combine groups) |
| `families [name]` | Federated org families (emojärjestöt): list, or drill into one family for its movement-wide total across all member associations |
| `contracts <name>` | HILMA public-procurement wins for an org (`--top` for top winners) |
| `lobbying <name>` | Lobbying-register activity + political party ties for an org (`--top`, `--party`) |
| `search <term>` | Fulltext search across all sources (`--source`, `--since/--until`) |
| `hunters` | Find orgs in multiple sources (`--sources`, `--since/--until`, `-v` for details) |
| `top [source]` | Biggest recipients (`--since/--until`, `--year`) |
| `verify <name>` | Verify org data against raw sources |
| `sources` | Overview of all data sources + aggregate statistics |
| `clusters [id]` | List or inspect STEA grant clusters (`--id` alternative) |
| `vsearch <id>` | Find similar grants by vector similarity (`--text`, `--source`, cross-source default) |
| `sql <query>` | Run arbitrary SELECT queries against the database |
| `setup` | Download data files from GitHub release (`--force` to re-download) |

## With Claude Code / Codex

Open the project directory and ask questions directly:

```
"Paljonko Suomen Punainen Risti on saanut yhteensä kaikista lähteistä?"
"Mitkä organisaatiot saavat rahoitusta useasta eri lähteestä?"
"Näytä suurimmat EU-rakennerahastojen saajat vuodesta 2020"
"Etsi ilmastonmuutokseen liittyvät hankkeet"
```

The AI reads `AGENTS.md` automatically and knows how to use the CLI, run SQL
queries, and cross-reference data across sources.

## Database schema

The database contains source-specific tables (`grants`, `ray_grants`, `eura_all`, `bf_awarded`,
`um_grants`, `helsinki_grants`, `va_grants`, `fts_grants`) plus an `org_mapping` table that links
the same organization across sources via `org_id`. The `org_families` table holds 20 federated
"emojärjestöt" — named org families (e.g. Suomen Punainen Risti, Omaishoitajaliitto), each a
central organisation plus its member associations, with `member_count`, `source_count`,
`total_eur`, `top_pct`, and `concentration` columns; `org_family_members` links member
y-tunnukset to a family. The legacy keyword-based grouping now lives in a separate
`org_families_cache`. Additional tables include `org_public_contracts` (HILMA
public-procurement wins, 26,028 rows), `lobbying_orgs` / `lobbying_topics` (lobbying-register
activity), and `political_connections`. Use `sql "SELECT name, sql FROM sqlite_master WHERE type='table'"` or inspect
`SOURCES.md` for full details.

## License

Data is from public Finnish government sources. See `SOURCES.md` for provenance.
