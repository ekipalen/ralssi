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

## EURA (EU-rakennerahastot)

- **Raakadata:** `data/eura_raw.xlsx`
- **Tietokantataulu:** `eura_all` (19 878 riviä), `eura_enrichments` (GPT-rikastus)
- **Verifiointi:** Ei suoraa API:a. Web-UI on SPA:
  - EURA 2014-2020: `https://www.eura2014.fi/rrtiepa/projekti.php?projektikoodi={hankekoodi}`
  - EURA 2021-2027: `https://www.eura2021.fi/hakutulokset/projektikortti?id={hankekoodi}`
  - Molemmat vaativat selainta (ei curl-yhteensopiva)
- **Sarakkeet:** hankekoodi, ohjelmakausi, rahasto, nimi, toteuttaja, y_tunnus, viranomainen, tila, aloituspvm, paattymispvm, myonnetty_eu_valtio, toteutunut_eu_valtio, tiivistelma, sijainti

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
- **Tietokantataulu:** `bf_awarded` (58 594 riviä)
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
- **Sarakkeet:** id, organisation, y_tunnus, grantor, decision_date, year, applied_eur, granted_eur, eu_funds_eur, purpose, call_name, regions
- **Huom:** Hylätyt päätökset (`data/okm/Kielteiset päätökset.xlsx`, 55 432 riviä) säilytetty referenssiksi mutta eivät tietokannassa.

## FTS (EU Financial Transparency System)

- **Raakadata:** `data/fts/` (API/CSV-export)
- **Tietokantataulu:** `fts_grants` (4 652 riviä)
- **Lähde:** https://ec.europa.eu/budget/fts — EU:n suorat maksut suomalaisille organisaatioille
- **Verifiointi:** Hae suoraan FTS-verkkosivulta organisaation nimellä tai VAT-numerolla
- **Sarakkeet:** id, year, programme, organisation, vat_number, y_tunnus, amount, is_ngo, is_nfpo, responsible_department, expense_type, beneficiary_type
- **Huom:** Sisältää vain Suomeen kohdistuvat maksut. Y-tunnus johdettu VAT-numerosta (FI-prefiksi poistettu).

## org_mapping (ristiin-linkitys)

- **Tietokantataulu:** `org_mapping` (~53 000 riviä)
- **Linkitysmenetelmät:**
  - `y_tunnus` (cross-source matchia) — luotettava
  - `name_match` (cross-source matchia) — riski väärälle osumalle
  - `new` — ei cross-source linkkiä
  - `high` — alkuperäinen lähde
- **~5 500 organisaatiota** esiintyy 2+ lähteessä
- **Korjattu 19.5.2026:** Vihreä Keidas ry/säätiö -väärä linkki, Helsingin yliopisto/ylioppilaskunta -sekaannus, Tampere poistettu
- **Tunnetut jäljellä olevat ongelmat:**
  - UM-nimivariantit: ~57 organisaatioparia joissa sama org esiintyy eri IATI ref-numerolla eri vuosina (esim. Kirkon Ulkomaanapu: ref 22000-3, 22000-456, 22000-528). Näitä EI yhdistetä automaattisesti koska riski väärille yhdistämisille. Tarkista manuaalisesti kun tulos on kiinnostava.
  - African Care ry kahdessa org_id:ssä (3958 + 4106)

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
