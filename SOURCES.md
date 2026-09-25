# Datalähteet ja verifiointi

## STEA (Sosiaali- ja terveysjärjestöjen avustuskeskus)

- **Raakadata:** `data/STEA-aineisto.xlsm` (alkuperäinen Excel-tiedosto)
- **Tietokantataulu:** `grants` (26 487 riviä), `enrichments` (GPT-rikastus)
- **Verifiointi-API:** `https://avustukset.stea.fi/api/organisation/{org_id}`
  - Hakee organisaation kaikki avustukset JSON-muodossa
  - Org ID löytyy skannaamalla (ei hakurajapintaa nimellä/y-tunnuksella)
  - Testattu: Kansanvalistusseura org_id=774, 9/9 riviä täsmää
- **Web-UI:** `https://avustukset.stea.fi/organisation/{org_id}`
- **Sarakkeet:** jarjesto, y_tunnus, vuosi, kayttotarkoitus, avustuslaji, alue, avustuskokonaisuus, jarjestoluokka, haettu, ehdotettu, myonnetty
- **Huom:** Sisältää myös hylätyt hakemukset (myonnetty=0). Tämä on tarkoituksellista.

## RAY (Raha-automaattiyhdistys)

- **Raakadata:** `data/RAY-aineisto.xlsx`
- **Tietokantataulu:** `ray_grants` (55 884 riviä, ~4,97 mrd €), `ray_enrichments` (GPT-rikastus)
- **Lähde:** STEA:n `downloadRay`-endpoint (RAY oli STEA:n edeltäjä; avustustoiminta siirtyi STEA:lle 2017)
- **Vuodet:** 2000-2016
- **Verifiointi:** Vertaa raaka-xlsx-tiedostoon
- **Sarakkeet:** jarjesto, y_tunnus, vuosi, kayttotarkoitus, avustuslaji, alue, jarjestoluokka, alaryhma, toimintoluokka, haettu, myonnetty, yt_source
- **KRIITTINEN — alaraja-varaus:** ~24 % RAY:n euroista on kirjattu nimillä joilla EI ole y-tunnusta, eivätkä ne ole linkittyneet mihinkään organisaatioon. Siksi mikä tahansa organisaatio- tai perhetason summa joka sisältää RAY-dataa on **alaraja** (aliarvioi, ei koskaan yliarvioi). `yt_source` kertoo y-tunnuksen alkuperän (local-name / prh-name-search / agent-candidate / NULL).

## EURA (EU-rakennerahastot)

- **Raakadata:** `data/eura_raw.xlsx`
- **Tietokantataulu:** `eura_all` (19 878 riviä), `eura_enrichments` (GPT-rikastus)
- **Verifiointi:** Ei suoraa API:a. Web-UI on SPA:
  - EURA 2014-2020: `https://www.eura2014.fi/rrtiepa/projekti.php?projektikoodi={hankekoodi}`
  - EURA 2021-2027: `https://www.eura2021.fi/hakutulokset/projektikortti?id={hankekoodi}`
  - Molemmat vaativat selainta (ei curl-yhteensopiva)
- **Sarakkeet:** hankekoodi, ohjelmakausi, rahasto, nimi, toteuttaja, y_tunnus, viranomainen, tila, aloituspvm, paattymispvm, myonnetty_eu_valtio, toteutunut_eu_valtio, tiivistelma, sijainti (NUTS-aluekoodi, esim. "FI193 Keski-Suomi"; täytetty ~41 % riveistä)

## UM / IATI (Ulkoministeriön kehitysyhteistyö)

- **Raakadata:** `data/iati/Finland_total_{vuosi}.xml` (14 tiedostoa, 2012-2025)
- **Tietokantataulu:** `um_grants` (23 301 riviä), `um_enrichments` (GPT-rikastus)
- **Lähde:** IATI d-portal / Suomen ulkoministeriön IATI-julkaisut
- **Verifiointi-API:** `https://d-portal.org/q?from=act&limit=50&reporting_org_ref=FI-3&participating_org={nimi}`
  - Huom: fulltext-haku, ei tarkka. Parempi verifioida suoraan XML-tiedostoista.
- **XML-rakenne:** `<iati-activity>` -> `<participating-org>` -> `<narrative>` (voi sisältää sekä suomen- että englanninkielisen nimen)
- **Tunnettu ongelma:** Sama organisaatio voi esiintyä usealla nimellä (fi/en), esim. "Kansanvalistusseura sr. (KVS)" ja "Finnish Lifelong Learning Foundation - KVS". Org_mapping ei kata kaikkia variantteja.
- **Sarakkeet:** activity_id, title, description, organisation, year, amount, currency, country, sector
- **Raakadatan verifiointi:** `grep -A5 "Organisaation nimi" data/iati/Finland_total_2020.xml`

## Business Finland

- **Raakadata:** `data/bf_awarded_raw.xlsx` + `data/bf_paid_raw.xlsx`
- **Tietokantataulu:** `bf_awarded` (58 935 riviä, 2010–2026)
- **Lähde:** Business Finland avoin data
- **Verifiointi:** Ei tunnettua API:a. Vertaa raaka-xlsx-tiedostoihin.
- **Sarakkeet:** organisation, y_tunnus, year, grants_eur, loans_eur, eu_structural_eur, research_eur, total_eur

## Helsinki (kaupungin avustukset)

- **Raakadata:** `data/helsinki/avustukset.xlsx` (kanslian avustukset) + `data/helsinki/nuoriso.csv` (nuorisotoimen avustukset)
- **Tietokantataulu:** `helsinki_grants` (11 037 riviä)
- **Lähde:**
  - Kanslian avustukset: Helsinki avoin data (päätöstiedot)
  - Nuorisotoimi: Helsinki avoin data CSV
- **Verifiointi:** Vertaa raaka-xlsx/csv-tiedostoihin suoraan
- **Sarakkeet:** hakija, hallintokunta, hakemustyyppi, avustuslaji, vuosi, myonnetty, lahde ('kanslia'/'nuoriso')
- **Huom:** Nimet deduplikoitu (254 ryhmää normalisoitu). Alkuperäiset nimivariantit eivät ole tallessa erikseen.

## Valtionavustukset (haeavustuksia.fi)

- **Raakadata:** `data/okm/Myönteiset päätökset.xlsx` (alkuperäinen Power BI -export, 144 993 riviä)
- **Tietokantataulu:** `va_grants` (8 537 riviä, 3,68 mrd €), `va_enrichments` (GPT-rikastus)
- **Lähde:** https://haeavustuksia.fi (ent. tutkiavustuksia.fi) — OKM:n Power BI -julkaisu
- **Verifiointi:** Vertaa raaka-xlsx-tiedostoon tai hae suoraan haeavustuksia.fi-palvelusta
- **Suodatus alkuperäisdatasta:**
  - Vain rivit joissa Y-tunnus hakijan nimessä (sulkeissa)
  - Myöntäjät: Suomen Akatemia, TEM, UM, OKM, OPH, STM, THL, VNK, OM, YM
  - OPH: vain ry/rf/sr/säätiö (ei kuntia/oppilaitoksia)
  - BF/STEA-myöntäjät pudotettu duplikaatteina (sama data jo kannassa)
- **Sarakkeet:** id, organisation, y_tunnus, grantor, decision_date, year, applied_eur, granted_eur, eu_eur, purpose, call_name, region, case_number
- **Huom:** Hylätyt päätökset (`data/okm/Kielteiset päätökset.xlsx`, 55 432 riviä) säilytetty referenssiksi mutta eivät tietokannassa.

## FTS (EU Financial Transparency System)

- **Raakadata:** `data/fts/` (API/CSV-export)
- **Tietokantataulu:** `fts_grants` (5 091 riviä, 2007–2025)
- **Lähde:** https://ec.europa.eu/budget/fts — EU:n suorat maksut suomalaisille organisaatioille
- **Verifiointi:** Hae suoraan FTS-verkkosivulta organisaation nimellä tai VAT-numerolla
- **Sarakkeet:** id, year, programme, organisation, vat_number, y_tunnus, amount, is_ngo, is_nfpo, responsible_department, expense_type, beneficiary_type
- **Huom:** Sisältää vain Suomeen kohdistuvat maksut. Y-tunnus johdettu VAT-numerosta (FI-prefiksi poistettu).

## org_mapping (ristiin-linkitys)

- **Tietokantataulu:** `org_mapping` (59 146 riviä, ~49 686 eri org_id:tä)
- **Sarakkeet:** org_id, source, source_name, y_tunnus, confidence, is_category, sector
- **Linkityksen luottamustasot (`confidence`):**
  - `high` — alkuperäinen lähde / luotettava (y-tunnus-osuma)
  - `name` — nimipohjainen linkki
  - `name_match` — cross-source nimimatchia, riski väärälle osumalle
  - `suffix_match` — suffiksipohjainen nimimatchia
  - `new` — ei cross-source linkkiä
- **`sector`:** organisaation sektoriluokitus, arvojoukko {company, government, university, research, international, association, foundation, cooperative, church, NULL}
- **`is_category`:** lippu kategoria-/kokoomariville (ei yksittäinen organisaatio)
- **~6 650 organisaatiota** esiintyy 2+ lähteessä
- **Korjattu 19.5.2026:** Vihreä Keidas ry/säätiö -väärä linkki, Helsingin yliopisto/ylioppilaskunta -sekaannus, Tampere poistettu
- **Tunnetut jäljellä olevat ongelmat:**
  - UM-nimivariantit: ~57 organisaatioparia joissa sama org esiintyy eri IATI ref-numerolla eri vuosina (esim. Kirkon Ulkomaanapu: ref 22000-3, 22000-456, 22000-528). Näitä EI yhdistetä automaattisesti koska riski väärille yhdistämisille. Tarkista manuaalisesti kun tulos on kiinnostava.
  - African Care ry kahdessa org_id:ssä (3958 + 4106)

## org_families (emojärjestöt) ja analyysitaulut

- **Tietokantataulu:** `org_families` (20 federoitua "emojärjestö"-perhettä)
  - Nimettyjä perheitä: keskusjärjestö + sen jäsenyhdistykset
  - **Sarakkeet:** id, keyword, label, description, member_count, source_count, total_eur, top_pct, concentration, sample_members, source_url, verified_on
- **Tietokantataulu:** `org_family_members` (perheiden jäsenten avaimet)
  - **Sarakkeet:** family_id, keyword, y_tunnus
- **Muut analyysitaulut:**
  - `org_public_contracts` — HILMA-hankinnat, voittaja- ja ostajapuoli (73 302 riviä, julkaistu 9/2026 asti)
  - `lobbying_orgs` / `lobbying_topics` — lobbausrekisteri (1 354 / 26 376 riviä)
  - `political_connections` — poliittiset kytkökset (122 riviä)

## Rikastukset (GPT)

- **Taulut:** `enrichments`, `eura_enrichments`, `um_enrichments`, `va_enrichments`
- **Malli:** GPT (tarkkaa versiota ei dokumentoitu)
- **Kentät:** oneliner, tags, concreteness (1-5), proportionality, target_group, method
- **Huom:** Rikastukset ovat mallin tulkintoja, eivät faktatietoja. Concreteness-pisteytys on subjektiivinen.

## Verifiointi-pikaohje

Kun tietokannasta saatu tulos halutaan tarkistaa alkuperäislähteestä:

```bash
# STEA: API-verifiointi (tarvitaan org_id)
curl -s "https://avustukset.stea.fi/api/organisation/774" | python3 -m json.tool

# EURA: vertaa raaka-xlsx
# data/eura_raw.xlsx

# UM/IATI: grep raaka-XML:stä
grep -A10 "Organisaation nimi" data/iati/Finland_total_2020.xml

# BF: vertaa raaka-xlsx
# data/bf_awarded_raw.xlsx + data/bf_paid_raw.xlsx

# Helsinki: vertaa suoraan raaka-xlsx/csv

# VA: vertaa raaka-xlsx tai hae haeavustuksia.fi
# data/okm/Myönteiset päätökset.xlsx
```

## Päivitysajo 25.9.2026 — mitä opittiin

Päivitetty: FTS (+2025), Business Finland (+2026), HILMA (6–9/2026), avoimuusrekisteri.
Tarkistettu, ei uutta: STEA, UM/IATI, Helsinki. **Ei saatu:** VA ja EURA 2021–2027.
Raportit ja hakuskriptit: `data/staging/<lähde>/REPORT.md` (gitignoressa — HILMA-skriptissä
on rajapinta-avain).

- **VA ja EURA 2021–2027 ovat Power BI -upotuksia.** Curl ei riitä, eikä headless-Chromium
  saanut vientiä toimimaan (EURA: vientinappi ei reagoi; VA: data tulee Power BI:n
  pakatussa DSR-muodossa). **Nopein reitti: lataa Excel käsin oikealla selaimella** ja
  vertaa `data/staging/va/baseline_from_raw.csv`:tä vasten. EURA:ssa oli 25.9. 8 733 hanketta,
  kannassa 8 091.
- **EURA 2014–2020 -palvelin (eura2014.fi) hylkää yhteydet Pi:ltä** (connection refused
  porttiin 443). Kausi on päättynyt, joten tämä ei ole kiireellinen.
- **Business Finland ei ole xlsx vaan Qlik-dashboard** (`tietopankki.businessfinland.fi`);
  haku Playwrightilla, ks. `data/staging/bf/`. `bf_awarded` vastaa *myönnettyä* rahoitusta,
  ei `bf_paid_raw.xlsx`:ää.
- **Avoimuusrekisterin koko-endpoint `/open-data-activity-notification` antaa HTTP 500**
  (liian iso). Käytä kausikohtaista `/term/{id}`-endpointia. Alkuperäinen tuonti otti vain
  yhden aiheen per ilmoitus — 2 585 vanhaa aihetta lisättiin jälkikäteen.
- **Supabase-synkka: `scripts/sync_tables_to_supabase.py`.** Kuivaharjoitus oletuksena,
  `--apply` korvaa taulut yhdessä transaktiossa ja ajaa `refresh_org_families_stats()`.
  Ota ensin varmuuskopio (`avustusdata/scripts/backup-supabase.sh`).
- **Supabasen euro-sarakkeet olivat `real`-tyyppiä** (~7 merkitsevää numeroa → 48 656 807 €
  tallentui 48 656 800 €:ksi, BF:ssä yhteensä 1,2 M€ virhettä). Korjattu 25.9.2026
  `double precision`:ksi 13 sarakkeessa; synkkaskripti korjaa jatkossa automaattisesti.
- **`bf_grants.id` = SQLiten rowid**, ja sivuston suosikit tallentavat avustukset muodossa
  `BF-<id>`. Synkka säilyttää id:t — älä lataa bf_grantsia uudelleen niin että id:t vaihtuvat.
- **`lobbying_orgs.total_grants_eur`:n alkuperäistä laskentakaavaa ei ole toistettavissa**
  (`fix-lobbying-grants.py` antaa tutkimusraskaille orgeille selvästi pienempiä summia). Siksi
  vain uusien orgien summat laskettiin; vanhoja ei kosketa ennen kuin kaava selvitetään.
