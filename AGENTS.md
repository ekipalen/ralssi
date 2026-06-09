# Rälssi — julkisen rahoituksen tutkimustyökalu

Tietokanta suomalaisesta julkisesta rahoituksesta. 8 datalähdettä, ~208 000 riviä, yhteensä ~48 mrd €. Tarkoitettu tutkivaan analyysiin: ketkä saavat rahaa, mistä lähteistä, ja kuinka paljon?

> **Suositus agenteille:** Käytä oletuksena `--third-sector`-lippua (oletus: päällä). Kolmanteen sektoriin rajattuna data on usein kiinnostavampaa ja datamäärä hallittavampaa. Poista suodatus `--no-third-sector`-lipulla vain kun käyttäjä nimenomaisesti pyytää yliopisto-, yritys- tai viranomaisdata.

## Kolmas sektori -suodatus (`--third-sector` / `--no-third-sector`)

Oletuksena PÄÄLLÄ kaikissa komennoissa (paitsi `sql`, `setup`, `sources`, `verify`). Suodattaa tuloksista pois organisaatiot joiden sector-sarake org_mapping-taulussa on: `company`, `government`, `university`, `research`, `international`.

Jos sector on NULL, käytetään nimipohjaista heuristiikkaa: poissuljettavia nimiä ovat mm. Oy, Ab, Oyj, Ltd, Ky, tmi, kaupunki, stad, yliopisto, universitet, university, ammattikorkeakoulu, korkeakoulusäätiö.

> **Ero verkkosivuun (avustusdata):** CLI käyttää **kieltolistaa** (poistaa yllä olevat sektorit, SÄILYTTÄÄ NULL/tuntemattomat nimiheuristiikalla). Verkkosivu käyttää **sallittulistaa** (näyttää VAIN sektorit `association`/`foundation`/`cooperative`/`church`). Siksi CLI:n kolmas-sektori-summa voi olla *suurempi* kuin sivuston samalle haulle (CLI pitää mukana sektoroimattomia orgeja). `contracts --top`- ja `lobbying --top` -rankingit käyttävät sivuston sallittulistaa (taulujen oma `sector`-sarake); `lobbying --party` näyttää kaikki kytketyt orgit sektorista riippumatta.

```bash
uv run ralssi.py top eura -n 10                    # Vain kolmas sektori (oletus)
uv run ralssi.py --no-third-sector top eura -n 10  # Kaikki organisaatiot
uv run ralssi.py hunters --min 3                   # Kolmas sektori, 3+ lähdettä
uv run ralssi.py --no-third-sector hunters         # Kaikki, sis. yliopistot/yritykset
```

Kolmannen sektorin organisaatiot = yhdistykset (ry), säätiöt (sr), osuuskunnat, kirkko. Poissuljetut = yritykset, yliopistot, tutkimuslaitokset, viranomaiset, kansainväliset organisaatiot.

## Setup

Ainoa vaatimus: [uv](https://docs.astral.sh/uv/). Se asentaa Pythonin automaattisesti.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Pikastart

```bash
uv run ralssi.py org "Organisaation nimi"   # Organisaatiohaku kaikista lähteistä
uv run ralssi.py profile "Nimi"             # Vuosittainen rahoitusmatriisi
uv run ralssi.py search "aihe"              # Tekstihaku kuvauksista ja nimistä
uv run ralssi.py hunters                    # Monilähde-rahoituksen saajat
uv run ralssi.py families                   # Emojärjestöt: koko liikkeen summat (keskus + paikallisyhdistykset)
uv run ralssi.py contracts "Nimi"           # HILMA-julkiset hankinnat (eri rahavirta kuin avustukset)
uv run ralssi.py sql "SELECT ..."           # Vapaa SQL mikä tahansa kysely
```

Ei ulkoisia riippuvuuksia — pelkkä Python 3.9+ stdlib. `uv run` hoitaa kaiken (asentaa myös numpyn automaattisesti vektorihaun vsearch-komentoa varten).

### Esimerkkipolku: tutkimus alusta loppuun
```bash
uv run ralssi.py hunters --min 3               # 1. Etsi monesta lähteestä rahoitusta saavat
uv run ralssi.py org "Kansanvalistusseura"     # 2. Valitse kiinnostava → lähdekohtaiset summat
uv run ralssi.py org "SASK" --merge            # 2b. Jos useita org_id:tä → yhdistä --merge:llä
uv run ralssi.py profile "Kansanvalistusseura" # 3. Vuositrendit: mistä rahaa, milloin
uv run ralssi.py search "mielenterveys"        # 4. Aiheeseen liittyvät hankkeet (ID näkyy tuloksissa)
uv run ralssi.py vsearch 15564                 # 5. Samankaltaiset hankkeet (ID edellisestä)
uv run ralssi.py vsearch --text "mielenterveys"  # 5b. Tai suoraan tekstillä (ei vaadi ID:tä)
uv run ralssi.py verify "Kansanvalistusseura"  # 6. Varmista alkuperäislähteistä
```

## Datalähteet

| Lähde | Taulu | Rivejä | Kuvaus |
|-------|-------|--------|--------|
| STEA | `grants` | 26 487 | Järjestöavustukset 2017– (sis. hylätyt, myonnetty=0) |
| RAY | `ray_grants` | 55 884 | Raha-automaattiyhdistys 2000–2016 (STEA:n edeltäjä) |
| EURA | `eura_all` | 19 878 | EU-rakennerahastohankkeet 2014-2029 |
| Business Finland | `bf_awarded` | 58 594 | Yritys- ja tutkimusrahoitus |
| UM/IATI | `um_grants` | 23 301 | Kehitysyhteistyö (ulkoministeriö) |
| Helsinki | `helsinki_grants` | 11 037 | Kaupungin avustukset |
| VA | `va_grants` | 8 537 | Valtionavustukset (haeavustuksia.fi): OKM, Akatemia, TEM, STM, THL, UM, VNK, OM, YM, OPH |
| FTS | `fts_grants` | 4 652 | EU Financial Transparency System (suorat EU-maksut) |

Organisaatiot linkitetty ristiin `org_mapping`-taululla (~59 000 riviä, ~6 650 orgia 2+ lähteessä, ~49 600 erillistä org_id:tä). Linkitys perustuu y-tunnukseen (luotettava) tai nimeen (riski väärille osumille).

> **RAY-kausi on alaraja:** ~24 % RAY-euroista (n. 1,2 mrd €, 4 490 nimeä) on nimillä joilla ei ole y-tunnusta eikä linkitystä organisaatioon. Siksi mikä tahansa org-/perhe-summa joka sisältää RAY:n voi olla **alakanttiin** (ei koskaan yläkanttiin) esi-2017-vuosien osalta. `top ray`, `families` ym. tulostavat tästä muistutuksen.

## Komennot

### Organisaatiohaku (tärkein)
```bash
uv run ralssi.py org "Kansanvalistusseura"
uv run ralssi.py org "SASK" --merge     # Yhdistä kaikki osuvat org_id:t yhteen näkymään
uv run ralssi.py org "Pakolaisapu" --detail --source stea  # Hanketason yksityiskohdat
uv run ralssi.py org "SPR" --detail --merge                # Kaikki hankkeet kaikilta lähteilta
```
Hakee kaikista lähteistä org_mapping-ristilinkityksen kautta. Näyttää per-lähde yhteenvedon. Jos haku osuu useaan org_id:hen, ne näytetään erikseen (esim. SASK vs SASKY). `--merge` yhdistää ne kun tiedetään että kyseessä on sama organisaatio. `--detail` näyttää yksittäiset hankkeet (oletuksena 50 per lähde, `--limit` säätää). `--source` rajaa tiettyyn lähteeseen.

**Huomioitavaa:**
- `org`-haku käyttää org_mapping-linkitystä. Jos organisaation y-tunnus eroaa lähteittäin (esim. BF:ssä eri y-tunnus), rahoitus voi jäädä piiloon. Käytä `verify`-komentoa tai suoraa SQL:ää ristintarkistukseen.
- `--merge` yhdistää KAIKKI osuvat org_id:t. Suurilla organisaatioilla (esim. SPR, jolla on keskusjärjestö + piirit) tämä voi yhdistää liikaa. Valikoivaan tarkasteluun käytä SQL:ää: `sql "SELECT * FROM org_mapping WHERE org_id IN (1234, 5678)"`

### Rahoitusprofiili (vuosi × lähde matriisi)
```bash
uv run ralssi.py profile "Pakolaisapu"
uv run ralssi.py profile "Fingo" --merge
```
Näyttää organisaation rahoituksen vuosittain ja lähteittäin taulukkona. Paljastaa trendit ja rahoituksen muutokset. `--merge` yhdistää org_id-ryhmät samaan taulukkoon.

### Tekstihaku kaikista lähteistä
```bash
uv run ralssi.py search "ilmastonmuutos"          # Haku kaikista lähteistä
uv run ralssi.py search "vammais" --source stea    # Rajaa yhteen lähteeseen
uv run ralssi.py search "digitali" --limit 50      # Enemmän tuloksia
uv run ralssi.py search "ilmasto" --since 2020     # Aikarajaus
uv run ralssi.py search "youth" --year 2023        # Yksittäinen vuosi
```
Etsii avustuskuvauksista, hankenimistä ja tiivistelmistä. Hakee STEA kayttotarkoitus, EURA nimi+tiivistelma, UM title+description, Helsinki hakemustyyppi+avustuslaji, VA purpose+call_name. Tulokset järjestetään summan mukaan. Huom: BF-datassa ei ole tekstikenttiä, vain organisaationimi ja summat — käytä SQL:ää BF-hakuihin.

**Kielimuistutus:** STEA/EURA-data on suomeksi, UM-data pääosin englanniksi. Kattavaan aihetutkimukseen hae molemmilla kielillä (esim. `search "ilmasto"` + `search "climate"`). Helsinki-datan tekstikentät (hakemustyyppi, avustuslaji) ovat geneerisiä kategorioita (esim. "Liikunta: Toiminta-avustus"), eivät hankekohtaisia kuvauksia — Helsinki-tuloksia löytyy vain yleisillä kategoriasanoilla. Käytä `top helsinki` tai SQL:ää Helsinki-datan tutkimiseen.

### Rälssien etsintä
```bash
uv run ralssi.py hunters              # Organisaatiot 2+ lähteessä
uv run ralssi.py hunters --min 3      # Vähintään 3 lähdettä
uv run ralssi.py hunters --sort sources --limit 50
uv run ralssi.py hunters --sources stea,um      # Vain tietyt lähteet (molemmat vaaditaan)
uv run ralssi.py hunters --since 2020           # Aikarajaus
uv run ralssi.py hunters -v                     # Verbose: per-lähde rahaerittelyt
```
Flags-sarakkeen selitykset:
- `dev+domestic` = saa sekä kehitysyhteistyörahaa (UM) että kotimaista rahoitusta (STEA/Helsinki)
- `ngo+company` = saa sekä järjestörahoitusta (STEA) että yritysrahoitusta (BF)
- `municipal+national` = saa sekä kunnallista (Helsinki) että valtakunnallista (STEA) rahoitusta

Huom: `hunters` aggregoi **org_id-tasolla** ja summaa kaikki organisaation nimivariantit per lähde — sama logiikka kuin `org`/`profile`-komennoissa ja verkkosivulla. (`--verbose` vaikuttaa enää vain näkymään, ei lukuihin.) Summat täsmäävät siksi `org "Nimi" --merge` -tulokseen.

### Suurimmat saajat
```bash
uv run ralssi.py top                  # Kaikki lähteet yhteensä (org_id-tasolla)
uv run ralssi.py top stea -n 10       # STEA top 10
uv run ralssi.py top um               # UM top 20
uv run ralssi.py top stea -n 10 --since 2020   # Aikarajaus
uv run ralssi.py top eura --year 2023           # Yksittäinen vuosi
```
`top` aggregoi org_id-tasolla: saman organisaation eri nimivariantit lasketaan yhteen (yksi rivi, kuten verkkosivun top-listoissa). Nimet joita ei ole org_mappingissa näkyvät omina riveinään. `top ray` ja `top` (kaikki) sisältävät RAY:n → alaraja-muistutus tulostuu.

### Emojärjestöt (federoidut perheet)
```bash
uv run ralssi.py families                       # Listaa 20 perhettä, koko liikkeen summan mukaan
uv run ralssi.py families omaishoitajaliitto    # Poraudu: jäsenkohtainen erittely + liikkeen summa
uv run ralssi.py families "punainen risti" --json
```
**Tärkein työkalu kysymykseen "paljonko KOKO liike/järjestöperhe on saanut".** Monen järjestön raha hajautuu keskusjärjestön + kymmenien paikallisyhdistysten kesken, jolloin kokonaiskuvaa ei näe yksittäisellä `org`-haulla (esim. omaishoitajat: keskus 14 M€, mutta liike 90 M€ hajautuneena 34 yhdistykseen). `families`-lista näyttää `concentration`-kentän: **keskitetty** = raha keskusjärjestössä, **hajautunut** = jakautunut paikallisyhdistyksiin. `top_pct` = suurimman jäsenen osuus liikkeen summasta. Perheet on **verifioitu järjestöjen virallisilta jäsenlistoilta** (`verified_on`-päivä). Drill-tulos summaa kaikki jäsen-y-tunnusten grantit (sama aggregaatio kuin org-profiilissa); RAY-alaraja koskee perheitä jotka ulottuvat RAY-kaudelle.

### Julkiset hankinnat (HILMA)
```bash
uv run ralssi.py contracts "Suomen Punainen Risti"   # Org:n voittamat hankintasopimukset
uv run ralssi.py contracts --top 20                  # Suurimmat hankintojen voittajat (kolmas sektori)
uv run ralssi.py contracts --top --suorahankinta     # Vain kilpailuttamattomat suorahankinnat
uv run ralssi.py org "SPR" --contracts               # Liitä hankinnat org-näkymään
```
HILMA-julkiset hankinnat ovat **eri rahavirta kuin avustukset** — älä laske niitä yhteen avustussummien kanssa. Euromäärä summataan VAIN yhden voittajan sopimuksista (`sole_winner=1`); monen voittajan sopimuksen arvoa ei voi kohdistaa yhdelle orgille, ne raportoidaan lukumääränä ("+N shared-win"). Kohdistus org_id:n kautta (sama kuin `org`).

### Lobbaus ja poliittiset kytkökset
```bash
uv run ralssi.py lobbying "Suomen Yrittäjät"    # Lobbarirekisteri + puoluekytkökset orgille
uv run ralssi.py lobbying --top 20              # Aktiivisimmat lobbarit (aiheiden määrä)
uv run ralssi.py lobbying --party KOK           # Puolueeseen kytketyt orgit
```
Lähteet: `lobbying_orgs` (rekisteri), `lobbying_topics` (lobbausaiheet), `political_connections` (puoluekytkökset: lahja/omistus/hallintotehtävä). Rekisteri painottuu yrityksiin/elinkeinojärjestöihin; useimmilla kolmannen sektorin orgeilla ei ole merkintöjä. Pienet taulut — myös `sql`-haut käyvät hyvin.


### Verifiointi alkuperäislähteistä
```bash
uv run ralssi.py verify "Organisaatio"
```
Näyttää nimivariantit, tarkistaa IATI XML-tiedostoista, antaa ohjeet STEA API -verifiointiin.

### Klusterit (STEA-avustusten teemaklusterit)
```bash
uv run ralssi.py clusters              # Listaa klusterit (59 kpl)
uv run ralssi.py clusters --id 133     # Klusterin avustukset
uv run ralssi.py clusters --id 176 --limit 20
```

### Samankaltaisuushaku (semanttinen, vaatii numpy)
```bash
uv run ralssi.py vsearch 1234                   # STEA grant ID (grants.id)
uv run ralssi.py vsearch S22173                  # EURA hankekoodi S/A/J-prefiksi (eura_all.hankekoodi)
uv run ralssi.py vsearch "FI-3-2020-1"          # UM activity_id (um_grants.activity_id)
uv run ralssi.py vsearch --text "mielenterveys"  # Tekstihaku → paras osuma → vsearch seed
uv run ralssi.py vsearch 1234 --source eura      # Rajaa tulokset yhteen lähteeseen
uv run ralssi.py vsearch 1234 --no-dedup         # Näytä kaikki tulokset ilman deduplikointia
```
Semanttinen haku GPT-embeddingien avulla. Löytää sisällöltään samankaltaisia avustuksia/hankkeita **yli lähteiden ja kielirajojen** — toimii myös kun käytetään eri sanoja tai kieltä (fi/en/sv).

**Oletuskäyttäytyminen (ilman `--source`):** Näyttää vain ristilähteiset tulokset — seed-hankkeen oma lähde jätetään pois. Esim. jos seed on STEA-avustus, tuloksissa näkyy vain EURA- ja UM-osumia. Käytä `--source X` jos haluat hakea saman lähteen sisällä.

**`--text` -lippu:** Ei vaadi hanke-ID:tä. Tekee ensin tekstihaun, valitsee parhaan osuman seediksi ja ajaa vsearchin sillä. Kätevä kun et tiedä hanke-ID:tä:
```bash
uv run ralssi.py vsearch --text "ilmastonmuutos"       # Paras STEA/EURA/UM-osuma → vsearch
uv run ralssi.py vsearch --text "climate" --source stea # Tekstihaku → seed → rajaa STEA-tuloksiin
```

**`--no-dedup`:** Oletuksena dedup on päällä: sama organisaatio näkyy tuloksissa vain kerran per lähde (korkein similarity-osuma säilyy). `--no-dedup` näyttää kaikki rivit.

**Embedding-kattavuus:** Tuloksen jälkeen näytetään kattavuustaulukko — kuinka suuri osa kunkin lähteen riveistä on embedding-avaruudessa.

| Lähde | Embeddings | Rivejä | Kattavuus | Huomio |
|-------|-----------|--------|-----------|--------|
| STEA | 26 472 | 26 487 | 99.9% | |
| RAY | 34 276 | 55 884 | 61.3% | Mukana ristihaussa. Suora `vsearch <id>` RAY:lle vaatii `--source ray` (numero-ID menisi muuten STEA:ksi) |
| UM | 23 301 | 23 301 | 100% | |
| EURA | 19 878 | 19 878 | 100% | Vanhemmat hankkeet indeksoitu nimellä (ei tiivistelmää) |
| VA | 8 537 | 8 537 | 100% | Valtionavustukset |
| FTS | 1 850 | 4 652 | 39.8% | EU Financial Transparency System |
| BF | 0 | 58 594 | 0% | Yritys/innovaatiorahoitus, ei sovellu semanttiseen hakuun |
| Helsinki | 0 | 11 037 | 0% | |

Jos vsearch antaa virheen tunnetulla ID:llä, kyseiselle riville ei ole embeddingiä.

**Hanke-ID:n löytäminen:** `search`-tuloksissa näkyy ID-sarake (STEA grant id, EURA hankekoodi, UM activity_id). `--text`-lippu tekee tämän automaattisesti. Vaihtoehtoisesti SQL:llä:
```bash
uv run ralssi.py sql "SELECT id, jarjesto, kayttotarkoitus FROM grants WHERE kayttotarkoitus LIKE '%ilmasto%' LIMIT 5"
uv run ralssi.py sql "SELECT hankekoodi, nimi FROM eura_all WHERE nimi LIKE '%climate%' LIMIT 5"
uv run ralssi.py sql "SELECT activity_id, title FROM um_grants WHERE title LIKE '%climate%' LIMIT 5"
```

**search vs vsearch:**
- `search` = tarkka sanahaku, löytää vain rivit joissa täsmälleen se sana esiintyy
- `vsearch` = merkityspohjainen haku yhdestä tunnetusta avustuksesta: löytää samankaltaisia myös synonyymein ja eri kielillä (esim. "nuorisotyö" → "ungdomsarbete", "youth work")
- Tyypillinen ketju: `search "aihe"` → löydät kiinnostavan hankkeen → `vsearch <hanke-id>` → löydät samankaltaiset joita sanahaku ei olisi löytänyt
- Tai suoremmin: `vsearch --text "aihe"` → hakee seedin ja samankaltaiset yhdellä komennolla

**Kielirajan ylitys vsearchissa:** Oletuksena (ilman `--source`) vsearch näyttää vain muiden lähteiden tuloksia, joten kielirajan ylitys tapahtuu automaattisesti. Käytä `--source` kohdentamiseen:
```bash
uv run ralssi.py vsearch S22173 --source um     # EURA (fi) → etsi vastaavia UM-hankkeita (en)
uv run ralssi.py vsearch "FI-3-2020-1" --source stea  # UM (en) → etsi vastaavia STEA-avustuksia (fi)
```

Numpy asentuu automaattisesti `uv run ralssi.py vsearch` -kutsulla (PEP 723 -metadata).

### Vapaa SQL
```bash
uv run ralssi.py sql "SELECT jarjesto, SUM(myonnetty) FROM grants GROUP BY jarjesto ORDER BY 2 DESC LIMIT 10"
uv run ralssi.py sql "SELECT * FROM org_mapping WHERE source_name LIKE '%Keidas%'" --json
```
`sql`-komento sallii vain SELECT- ja WITH-kyselyt (suojaa vahingolta). Tulokset rajoitetaan automaattisesti 1000 riviin ellei kyselyssä ole LIMIT-lauseketta (`--limit N` ohittaa oletuksen). `--csv` tulostaa CSV-muodossa. Kirjoitusoperaatioihin käytä `sqlite3` suoraan (ks. alla).

### Datan muokkaus (sqlite3)
Tietokantaa voi muokata suoraan `sqlite3`-työkalulla kun tutkimus paljastaa korjattavaa:
```bash
# Lisää puuttuva org_mapping-rivi (esim. uusi nimi samalle organisaatiolle)
sqlite3 data/funding.db "INSERT INTO org_mapping (org_id, source, source_name, y_tunnus, confidence) VALUES (4759, 'stea', 'Loisto setlementti ry', '0432210-3', 'high')"

# Korjaa confidence-arvo
sqlite3 data/funding.db "UPDATE org_mapping SET confidence = 'high' WHERE rowid = 12345"

# Poista väärä linkitys
sqlite3 data/funding.db "DELETE FROM org_mapping WHERE rowid = 12345"

# Tarkista muutos
uv run ralssi.py sql "SELECT * FROM org_mapping WHERE org_id = 4759"
```
Tee aina varmuuskopio ennen isoja muutoksia: `cp data/funding.db data/funding.db.bak`

### Tilastot ja lähteet
```bash
uv run ralssi.py sources              # Lähdetaulukko + kokonaisstatistiikka
```

### JSON-output (kaikissa komennoissa paitsi verify)
```bash
uv run ralssi.py org "Nimi" --json
uv run ralssi.py hunters --json
```

### Suora SQL sqlite3-työkalulla
Jos `sqlite3` on asennettuna, sitä voi käyttää suoraan ilman Pythonia:
```bash
sqlite3 data/funding.db "SELECT jarjesto, SUM(myonnetty) FROM grants GROUP BY 1 ORDER BY 2 DESC LIMIT 10"
sqlite3 -header -column data/funding.db ".schema grants"
sqlite3 -json data/funding.db "SELECT * FROM org_mapping LIMIT 5"
```

## Tietokantarakenne

### grants (STEA)
`id, jarjesto, y_tunnus, vuosi, kayttotarkoitus, avustuslaji, alue, avustuskokonaisuus, jarjestoluokka, haettu, ehdotettu, myonnetty`

### eura_all (EU-rahastot)
`hankekoodi, ohjelmakausi, rahasto, nimi, toteuttaja, y_tunnus, viranomainen, tila, aloituspvm, paattymispvm, myonnetty_eu_valtio, toteutunut_eu_valtio, tiivistelma, sijainti`

### um_grants (kehitysyhteistyö)
`id, activity_id, title, description, organisation, year, amount, currency, country, sector`

### bf_awarded (Business Finland)
`organisation, y_tunnus, year, grants_eur, loans_eur, eu_structural_eur, research_eur, total_eur`

### helsinki_grants (Helsinki)
`id, hakija, hallintokunta, hakemustyyppi, avustuslaji, vuosi, myonnetty, lahde`

### va_grants (Valtionavustukset)
`id, organisation, y_tunnus, grantor, year, applied_eur, granted_eur, eu_eur, purpose, call_name, region, case_number, decision_date`
- grantor: OKM, Suomen Akatemia, TEM, STM, THL, Ulkoministeriö, VNK, OM, YM, OPH (vain yhdistykset/säätiöt)
- Lähde: haeavustuksia.fi Power BI -export, raakatiedostot `data/okm/`

### fts_grants (EU Financial Transparency System)
`id, year, programme, organisation, vat_number, y_tunnus, amount, is_ngo, is_nfpo, responsible_department, expense_type, beneficiary_type`
- Lähde: ec.europa.eu/budget/fts — suorat EU-maksut suomalaisille organisaatioille
- 4 652 riviä, ~1,5 mrd €

### org_mapping (ristiin-linkitys)
`org_id, source, source_name, y_tunnus, confidence, is_category, sector`
- source: stea, ray, eura, um, bf, helsinki, va, fts
- confidence: `high` (luotettava, valtaosa), `name` / `name_match` / `suffix_match` (nimipohjainen, riski väärille osumille), `new`
- sector: company, government, university, research, international, association, foundation, cooperative, church tai NULL (käytetään kolmas-sektori-suodatukseen)
- is_category: 1 = temaattinen kategoria, ei oikea org (jätetään pois org/hunters-aggregaateista)

### org_families / org_family_members (emojärjestöt = federoidut perheet)
`org_families` (20 riviä): `id, keyword(slug), label, description, member_count, source_count, total_eur, top_pct, concentration, sample_members, source_url, verified_on` — **valtakunnalliset emojärjestöt** (keskusjärjestö + jäsenyhdistykset), esim. Suomen Punainen Risti, Omaishoitajaliitto, MIELI. EI temaattisia avainsanoja. `concentration` ∈ {`keskitetty`, `hajautunut`}, `top_pct` = suurimman jäsenen osuus liikkeen summasta. Jäsenyydet verifioitu virallisilta jäsenlistoilta (`verified_on`).
`org_family_members` (279 riviä): `family_id, keyword, y_tunnus` — linkittää jäsenten y-tunnukset perheeseen.

Paras käyttö: `families`-komento (ks. yllä). SQL: `sql "SELECT label, member_count, total_eur, concentration FROM org_families ORDER BY total_eur DESC"`. Koko liikkeen summan saa joinaamalla `org_family_members.y_tunnus` → `org_mapping` → lähde-taulut.
> Huom: `org_families_cache` (57 riviä) on **vanha** temaattinen avainsana-välimuisti (nuoriso/vammais/…), ei liity emojärjestöihin. Älä sekoita näitä.

### org_public_contracts (HILMA-julkiset hankinnat)
`id, org_id, y_tunnus, buyer, title, value, n_winners, sole_winner, procedure_type, is_suorahankinta, date_published, winner_name, sector` — 26 028 julkista hankintasopimusta jotka org on voittanut. **Eri rahavirta kuin avustukset** — älä summaa yhteen. `value` on luotettava vain kun `sole_winner=1`; monen voittajan sopimuksen arvoa ei voi kohdistaa yhdelle. Komento: `contracts`.

### lobbying_orgs / lobbying_topics / political_connections
`lobbying_orgs` (1 264): rekisteröidyt lobbarit (`org_name, y_tunnus, sector, contact_count, topic_count, main_industry, total_grants_eur`). `lobbying_topics` (18 958): lobbausaiheet (`y_tunnus, org_name, topic_description, activity_type, activity_date`). `political_connections` (122): puoluekytkökset (`org_id, org_name, y_tunnus, party, connection_count, categories, total_grants_eur`). Komento: `lobbying`.

### enrichments / eura_enrichments / um_enrichments / va_enrichments / ray_enrichments / fts_enrichments
GPT-rikastukset: `oneliner, tags, concreteness (1-5), target_group, method`
Ovat mallin tulkintoja, eivät faktatietoja. Concreteness on subjektiivinen.

## Verifiointi

Kun löydät kiinnostavan tuloksen, varmista alkuperäislähteestä:

### STEA
```bash
# Live API (vaatii numeerisen org_id:n, löytyy web-UI:n URL:sta)
curl -s "https://avustukset.stea.fi/api/organisation/774" | python3 -m json.tool
# Avustukset ovat allAidTargets-kentässä
# Web: https://avustukset.stea.fi/organisation/{org_id}
```

### UM/IATI
```bash
grep -r "Organisaation nimi" data/iati/
grep -A5 'ref="22000-311"' data/iati/Finland_total_2020.xml
```

### EURA, BF, Helsinki
Vertaa suoraan raakadatatiedostoihin: `data/eura_raw.xlsx`, `data/bf_awarded_raw.xlsx`, `data/helsinki/avustukset.xlsx`, `data/helsinki/nuoriso.csv`

## Tunnetut rajoitteet

1. **UM-nimivariantit (~57 ryhmää):** Sama organisaatio voi esiintyä eri nimellä ja IATI ref-numerolla eri vuosina. `org`-haku saattaa näyttää liian pieniä UM-summia. Tarkista aina myös englanninkielisellä nimellä: `grep -ri "english name" data/iati/`

2. **org_mapping name_match (378 kpl):** Nimeen perustuvat linkitykset voivat sisältää vääriä osumia. Y-tunnus-linkitykset ovat luotettavia. Tarkista: `uv run ralssi.py sql "SELECT * FROM org_mapping WHERE confidence='name_match' AND source_name LIKE '%nimi%'"`

3. **STEA sisältää hylätyt hakemukset:** myonnetty=0 rivit ovat tarkoituksellisesti mukana.

4. **RAY-kausi (2000–2016) on alaraja:** ~24 % RAY-euroista on nimillä joilla ei ole y-tunnusta → linkittämättä organisaatioon. Org-/perhe-/hunters-summat jotka sisältävät RAY:n voivat olla **alakanttiin** (ei koskaan yläkanttiin). Älä esitä RAY:n sisältäviä esi-2017-summia täydellisinä.

5. **Concreteness-pisteet:** GPT:n subjektiivinen arvio, käytä suuntaa-antavana.

## Tutkimuksen työnkulku

Tyypillinen tutkimuspolku etenee laveasta rajauksesta tarkkaan:

1. **Kartoita** — `hunters`, `top`, `hunters -v` → ketkä ovat isoimmat saajat ja missä on päällekkäisyyttä
2. **Syvenny organisaatioon** — `org "Nimi"` → lähdekohtaiset summat, `profile "Nimi"` → vuositrendit
3. **Tutki aihetta** — `search "teema"` → mitkä hankkeet liittyvät aiheeseen. Hae sekä suomeksi että englanniksi (UM-data on englanniksi)
4. **Laajenna semanttisesti** — `vsearch <id>` → löydä samankaltaisia hankkeita joita sanahaku ei löydä (ID:n saa search-tuloksista tai SQL:llä)
5. **Kaiva yksityiskohdat** — `sql "SELECT ..."` → vapaa kysely yksityiskohtiin
6. **Verifioi** — `verify "Nimi"` + alkuperäislähteet → varmista ennen johtopäätöksiä. Verify löytää myös org_mappingista puuttuvia linkityksiä.

Kun haku osuu useaan org_id:hen (esim. "SASK" → SASK + SASKY), komento varoittaa ja ehdottaa `--merge`-lippua. Käytä `--merge` vain kun olet varma että kyseessä on sama organisaatio.

## Tyypillisiä tutkimuskysymyksiä

- "Mitkä järjestöt saavat rahaa useimmista lähteistä?" → `hunters`
- "Paljonko organisaatio X on saanut yhteensä?" → `org "X"` tai `org "X" --merge`
- "Miten organisaation rahoitus on kehittynyt?" → `profile "X"`
- "Mitä hankkeita liittyy aiheeseen Y?" → `search "Y"`
- "Ketkä saavat eniten STEA-rahaa?" → `top stea`
- "Onko päällekkäistä rahoitusta?" → `hunters -v`
- "Löydä samankaltaisia hankkeita kuin tämä" → `vsearch <hanke-id>` (semanttinen, yli kielirajojen)
- "Tarkista tämä tulos alkuperäislähteestä" → `verify "X"` + manuaalinen tarkistus
- "Ketkä saavat sekä STEA:lta että kehitysyhteistyörahaa?" → `hunters --sources stea,um`
- "Ketkä saivat eniten STEA-rahaa vuonna 2023?" → `top stea --year 2023`
- "Mitä ilmastohankkeita on rahoitettu 2020 jälkeen?" → `search "ilmasto" --since 2020`
- "Näytä Pakolaisavun yksittäiset STEA-avustukset" → `org "Pakolaisapu" --detail --source stea`
- "Paljonko KOKO liike/järjestöperhe (esim. omaishoitajat, MLL) on saanut?" → `families <nimi>` (keskus + paikallisyhdistykset yhteensä)
- "Mitkä emojärjestöt jakavat rahan laajalle vs keskittävät sen?" → `families` (concentration: hajautunut vs keskitetty)
- "Mitä julkisia hankintoja org X on voittanut?" → `contracts "X"` tai `org "X" --contracts`
- "Ketkä kolmannen sektorin orgit voittavat eniten julkisia hankintoja?" → `contracts --top`
- "Lobbaako org X / mihin puolueisiin se kytkeytyy?" → `lobbying "X"`, `lobbying --party KOK`

## Raakadatatiedostot

| Lähde | Polku |
|-------|-------|
| STEA | `data/STEA-aineisto.xlsm` |
| RAY | `data/RAY-aineisto.xlsx` |
| EURA | `data/eura_raw.xlsx` |
| BF (myönnetyt) | `data/bf_awarded_raw.xlsx` |
| BF (maksetut) | `data/bf_paid_raw.xlsx` |
| Helsinki (kanslia) | `data/helsinki/avustukset.xlsx` |
| Helsinki (nuoriso) | `data/helsinki/nuoriso.csv` |
| UM/IATI | `data/iati/Finland_total_*.xml` (14 tiedostoa, 2012-2025) |
| VA | `data/okm/Myönteiset päätökset.xlsx`, `data/okm/Kielteiset päätökset.xlsx` |
| FTS | `data/fts/` (API/CSV-export EU Financial Transparency System) |

Lisätietoa datalähteistä ja verifioinnista: `SOURCES.md`
