# Uuden datalähteen lisääminen Rälssiin

Tämä ohje dokumentoi prosessin jolla VA-lähde (valtionavustukset) lisättiin toukokuussa 2026. Sama kaava toimii mille tahansa uudelle lähteelle.

## Yleiskuva

```
1. Analysoi raakadata   → mitä suodatetaan, duplikaattitarkistus
2. Import-skripti       → luo taulu, tuo rivit kantaan
3. Verify               → vertaa tuotua dataa alkuperäistiedostoon
4. Org mapping          → Y-tunnus-linkitys olemassaoleviin org_id:hin
5. Embeddings           → text-embedding-3-small, 384d vektorit
6. Enrichment           → GPT-4.1-nano: oneliner, tags, concreteness
7. ralssi.py päivitys   → SOURCES + SEARCH_FIELDS dict (listat johdetaan; vsearch erikseen)
8. Dokumentaatio        → README.md, AGENTS.md, SOURCES.md
9. Testit               → kaikki komennot, data integrity
10. Paketointi          → zip, GitHub release, git commit+push
```

## 1. Raakadatan analyysi

Ennen mitään koodausta: tutki data huolella.

- Montako riviä, mitä sarakkeita, miltä vuosilta
- Onko Y-tunnuksia (helpottaa org-linkitystä huomattavasti)
- Onko duplikaatteja olemassolevan datan kanssa (STEA, BF, EURA, UM, Helsinki, VA, FTS)
  - Testaa pienellä otoksella: sama org + sama summa + sama vuosi = duplikaatti
- Mitkä rivit ovat relevantteja (suodata turhat myöntäjät, tyhjät saajat jne.)
- Säilytä alkuperäistiedostot `data/<nimi>/` -hakemistossa verifointia varten

## 2. Import-skripti

Luo `scripts/import_<nimi>.py`. VA-esimerkki: `scripts/import_va.py`.

Skriptin tulee:
- Lukea raakadata (Excel/CSV/XML/JSON)
- Suodattaa relevantit rivit
- Luoda taulu `CREATE TABLE IF NOT EXISTS <nimi>_grants (...)`
- Lisätä indeksit (y_tunnus, organisation, grantor tms.)
- Tulostaa yhteenveto (rivimäärä, kokonaissumma, uniikki org-määrä)

**Taulun pakolliset sarakkeet** (jotta ralssi.py:n geneeriset funktiot toimivat):
- Uniikki `id` (INTEGER PRIMARY KEY tai TEXT)
- Organisaation nimi (vapaavalintainen sarakenimi)
- Rahasumma (EUR)
- Vuosi (INTEGER)

**Suositellut lisäsarakkeet:**
- `y_tunnus` — mahdollistaa cross-source linkityksen
- Kuvaus/tarkoitus — tarvitaan search- ja vsearch-komennoille
- Myöntäjä — hyödyllinen detail-näkymässä

## 3. Verifiointi

Vertaa tuotua dataa alkuperäistiedostoon:
- Rivimäärä täsmää (suodatuksen jälkeen)
- Kokonaissumma täsmää
- Muutama satunnainen rivi tarkistettu käsin

## 4. Org mapping

Luo `scripts/update_org_mapping.py` tai lisää logiikka import-skriptiin.

```sql
-- Peruslogiikka: Y-tunnus match olemassaoleviin
SELECT DISTINCT om.org_id, om.y_tunnus
FROM org_mapping om
WHERE om.y_tunnus IN (SELECT DISTINCT y_tunnus FROM <nimi>_grants)
```

- Y-tunnus-match on luotettavin linkitystapa
- Uudet orgs (ei Y-tunnus-osumaa) saavat uuden org_id:n
- Käytä `INSERT OR IGNORE` idempotenttiuteen
- confidence = 'y_tunnus' cross-source matcheille, 'new' uusille

## 5. Embeddings

Luo `scripts/embed_<nimi>.py`. Käyttää OpenAI API:a.

```python
# Avain: ~/.config/voice-bot/secrets.env -> OPENAI_REALTIME_KEY
# Malli: text-embedding-3-small
# Dimensiot: 384 (sama kuin muilla lähteillä)
# Batch: 500 kerrallaan
# Output: data/<nimi>_embeddings.npy + data/<nimi>_embedding_ids.json
```

Embedding-teksti per rivi: `org | grantor | purpose[:300] | call_name[:100]` (tai vastaavat kentät).

Koko: ~1.5 KB per rivi numpy float32, esim. 8500 riviä = ~13 MB .npy.

## 6. Enrichment (GPT-4.1-nano)

Luo `scripts/enrich_<nimi>.py`.

```python
# Malli: gpt-4.1-nano ($0.10/M input, $0.40/M output)
# Batch: 20 avustusta kerrallaan
# Taulu: <nimi>_enrichments (grant_id, oneliner, tags, concreteness)
# Kustannus: ~$0.03 per 1000 riviä
```

- `oneliner`: max 15 sanan suomenkielinen tiivistelmä
- `tags`: 2-4 pientä suomenkielistä tagia JSON-arrayna
- `concreteness`: 1-5 (1=abstrakti, 5=konkreettinen)
- Hidas: ~200 riviä / 5 min (API-latenssi). 8500 riviä ≈ 75 min.

## 7. ralssi.py — muutoskohteet

> **Päivitetty 5.6.2026 (single-source refactor):** lähdelistat johdetaan nyt
> `SOURCE_ORDER`/`SOURCES`-rekisteristä, eikä niitä enää kovakoodata moneen paikkaan.
> Useimmiten **uusi lähde = 2 dict-merkintää** (kohdat 7a–7b). Loput ovat tarpeen vain
> jos lähteellä on semanttinen haku (7c) tai poikkeava sarakemuoto (7d).

### 7a. SOURCES dict (~rivi 33) — PAKOLLINEN
```python
"<nimi>": {"table": "<nimi>_grants", "name": "<org_col>", "amount": "<amount_col>", "year": "<year_col>", "desc": "Kuvaus"},
```
Tästä johdetaan automaattisesti: `SOURCE_ORDER`, kaikki lähdelistat (`_summarize_org_group`,
`cmd_verify`, `cmd_profile`, vsearch-coverage), `top`/`org` argparse-choices, sekä
`cmd_sources`/`cmd_top` aggregaatit. **Ei erillisiä lähdelistoja päivitettävänä.**

### 7b. SEARCH_FIELDS dict (~rivi 1240) — PAKOLLINEN (fulltext-haku)
```python
"<nimi>": {"table": "<nimi>_grants", "text": ["<text_col1>", "<text_col2>"], "name": "<org_col>", "amount": "<amount_col>", "year": "<year_col>", "id": "<id_col>"},
```
`search`-komennon `--source`-choices johdetaan tästä.

### 7c. EMB_FILES dict (~rivi 48) — vain jos semanttinen haku
```python
"<nimi>": ("<nimi>_embeddings.npy", "<nimi>_embedding_ids.json"),
```
Moduulitason rekisteri. `vsearch --source`-choices johdetaan tästä, coverage näkyy
automaattisesti. Lisää MYÖS:
- **cmd_vsearch detail-fetch** (`elif src == "<nimi>":`) — org/year/amount/desc haku id:llä
- **cmd_vsearch seed-kuvaus** (`elif query_source_key == "<nimi>":`)
- **lookup_id int-muunnos** (`("stea", "ray", "va", "fts", ...)`) jos id on numeerinen

> Seed-haku osaa jo itse rajata embeddattuihin riveihin (osittainen kattavuus ok).

### 7d. Sarakemuotoiset erityishaarat — vain jos poikkeaa
Geneeriset funktiot (summary, year, top, org, profile, hunters) toimivat suoraan
`SOURCES`-metadatasta. Erityishaara tarvitaan vain jos:
- **y_tunnus**: ei toimenpiteitä — `_sources_with_ytunnus` tunnistaa sarakkeen automaattisesti.
- **_detail_grants() / _print_detail_table()**: lisää haara vain jos `org --detail`-näkymän
  sarakkeet poikkeavat olemassa olevista (voi myös liittää olemassa olevaan, esim. `in ("stea", "ray")`).
- **cmd_verify**: lähdekohtainen verify-info (Y-tunnus, URL, raakadata) jos relevanttia.

### 7e. RELEASE_URL — päivitä versio kun julkaiset uuden datasetin

## 8. Dokumentaatio

- **README.md**: rivimäärä, kokonaissumma, lähteiden lukumäärä, VA-rivi taulukkoon
- **AGENTS.md**: datalähteet-taulukko, skeema, hakukentät, embedding-kattavuus, org_mapping-tilastot
- **SOURCES.md**: uusi osio raakadatasta, sarakkeet, verifiointi-ohjeet, suodatuskriteerit
- **.gitignore**: `data/<nimi>/` jos raakadatatiedostot ovat isoja

## 9. Testit

Testaa kaikki 10 komentoa:
```bash
uv run ralssi.py sources                        # uusi lähde näkyy
uv run ralssi.py org "<tunnettu_org>" --merge    # cross-source näkymä
uv run ralssi.py profile "<org>" --merge         # vuosimatriisi
uv run ralssi.py search "<aihe>" --source <nimi>  # tekstihaku
uv run ralssi.py hunters --min 2 -v              # ristirahoitus
uv run ralssi.py top <nimi>                       # suurimmat saajat
uv run ralssi.py verify "<org>"                   # verifiointi
uv run ralssi.py vsearch --text "<aihe>" --source <nimi>  # vektorihaku
uv run ralssi.py sql "SELECT COUNT(*) FROM <nimi>_grants" # SQL
uv run ralssi.py clusters                         # ei pitäisi hajota
```

Data integrity:
```sql
SELECT COUNT(*) FROM <nimi>_grants WHERE y_tunnus IS NULL;  -- 0 jos vaadittu
SELECT MIN(year), MAX(year) FROM <nimi>_grants;             -- odotettu väli
SELECT COUNT(*) FROM <nimi>_enrichments;                     -- = grant-rivimäärä
```

## 10. Paketointi

```bash
sqlite3 data/funding.db "PRAGMA wal_checkpoint(TRUNCATE)"
zip -r /tmp/ralssi-data.zip data/ -x "data/funding.db-*"
gh release create v<N>.0 /tmp/ralssi-data.zip --title "v<N>.0 — <kuvaus>"
git add . && git commit && git push
```

## Kustannusarvio per lähde

| Vaihe | ~8500 riviä | Huom |
|---|---|---|
| Embeddings (OpenAI) | ~$0.01 | text-embedding-3-small, halpa |
| Enrichment (GPT-4.1-nano) | ~$0.23 | hidas mutta halpa |
| GitHub release upload | ilmainen | 155 MB zip |
| **Yhteensä** | **~$0.25** | |

## Aikataulu

| Vaihe | Kesto |
|---|---|
| Analyysi + suunnittelu | 30-60 min (interaktiivinen) |
| Import + verify + org mapping | 5-10 min |
| Embeddings | 2-5 min |
| Enrichment | 60-90 min (taustalla) |
| ralssi.py päivitys | 15-20 min |
| Dokumentaatio | 10 min |
| Testit | 5-10 min (sub-agentit) |
| Paketointi + release | 5 min |
| **Yhteensä** | **~2.5-3.5 h** (enrichment dominoi) |
