"""
Classify remaining NULL-sector org_mapping rows using extended heuristics.
Runs on top of classify_sectors.py and ytj_sector_lookup.py results (does NOT reset).
Only touches rows where sector IS NULL.

Usage: uv run python scripts/classify_nulls.py
"""

import sqlite3
import re

DB_PATH = "/home/eki/ralssi/data/funding.db"


# ─── KNOWN INTERNATIONAL ORGANIZATIONS ───────────────────────────────────────
# These are multilateral / intergovernmental / international NGOs.
# They are NOT Finnish third sector, NOT Finnish government.
# We tag them 'international' so the toggle can handle them separately.

INTERNATIONAL_KEYWORDS = [
    # UN system
    r'^united nations',
    r'^un\b',
    r'\bunited nations\b',
    r'^unicef\b',
    r'^unesco\b',
    r'^unifem\b',
    r'^undpa\b',
    r'^unpol\b',
    r'\bun women\b',
    r'\bun volunteers\b',
    r'\bun mine action\b',
    r'\bun peacebuilding\b',
    r'\bun secretariat\b',
    r'\bun office\b',
    r'\bun fund\b',
    r'\bun development\b',
    r'\bun childr',
    r'\bun educational',
    r'\bun economic',
    r'\bun framework',
    r'\bun human settlement',
    r'\bun industrial',
    r'\bun institute',
    r'\bun voluntary',
    r'\bun relief',
    r'\bun research',
    r'\bunaids\b',
    r'\bunhcr\b',
    r'\bundp\b',
    r'\bunep\b',
    r'\bunfpa\b',
    r'\bunrwa\b',
    r'\bunido\b',
    r'\bunitar\b',
    r'\bunocha\b',
    r'\bunodc\b',
    r'\bunops\b',
    r'\bunrisd\b',
    r'\bunv\b',
    r'\bunisdr\b',
    r'\bunfccc\b',
    r'\bunece\b',
    r'\bunctad\b',
    r'\bunccf\b',
    r'\bunmas\b',
    r'\bunpos\b',
    r'^joint united nations',
    # World Bank / IMF / IFC
    r'\bworld bank\b',
    r'\bibrd\b',
    r'\bida\b.*\btrust fund\b',
    r'\bida\b.*\binitiative\b',
    r'\bifc\b',
    r'^international finance corporation',
    r'^international bank for reconstruction',
    r'^international monetary fund',
    r'^international fund for agricultural development',
    r'\bifad\b',
    # Regional dev banks
    r'^african development (bank|fund)',
    r'^asian development (bank|fund)',
    r'^asian infrastructure investment bank',
    r'^inter-american development bank',
    r'^inter-american investment',
    r'^european bank for? reconstruction',
    r'^european investment bank',
    r'^european development fund',
    r'^nordic development fund',
    r'^caribbean development bank',
    # EU institutions
    r'^european commission',
    r'^european union',
    r'^eu institutions',
    r'^commission of the european',
    # Other multilateral
    r'^international labour org',
    r'^international atomic energy',
    r'\biaea\b',
    r'^international organisation for migration',
    r'^international organization for migration',
    r'\biom\b',
    r'^world health org',
    r'\bwho\b.*assessed',
    r'\bwho\b.*voluntary',
    r'^world food programme',
    r'\bwfp\b',
    r'^world trade org',
    r'\bwto\b',
    r'^world meteorological',
    r'\bwmo\b$',
    r'^world intellectual property',
    r'^food and agri.*org',
    r'\bfao\b',
    r'^international telecomm',
    r'^universal postal union',
    r'^organisation for economic co-operation',
    r'^oecd\b',
    r'^council of europe\b',
    r'^north atlantic tre?aty org',
    r'\bnato\b',
    r'^organization for security and co-?operation',
    r'\bosce\b',
    r'^nordic council of ministers',
    r'^nordic environment finance',
    r'^african union\b',
    r'^east african community\b',
    r'^southern african development community',
    r'\bsadc\b',
    r'^inter-governmental authority',
    r'\bigad\b',
    r'^intergovernmental authority',
    r'^intergovernmental panel on climate',
    r'\bipcc\b',
    r'^mekong river commission',
    r'^global fund for hiv',
    r'\bgfatm\b',
    r'^global environment facility',
    r'\bgef\b',
    r'^green climate fund',
    r'\bgcf\b',
    r'^global alliance for vaccines',
    r'\bgavi\b',
    r'^the gavi alliance',
    r'^global partnership for education',
    r'\bgpe\b',
    r'^adaptation fund\b',
    r'^central emergency response fund',
    r'\bcerf\b',
    r'^multilateral fund for the implementation',
    r'^montreal protocol\b',
    r'^extractive industries transparency',
    r'\beiti\b',
    r'^global crop diversity trust',
    r'^international renewable energy agency',
    r'\birena\b',
    r'^international vaccine institute',
    r'^coalition for epidemic preparedness',
    r'\bcepi\b',
    r'^least developed countries fund',
    r'\bldcf\b',
    r'^special climate change fund',
    r'\bsccf\b',
    r'^multilateral debt relief',
    r'^fcpf\b',
    r'^cgiar\b',
    r'^consultative group on international agricultural',
    r'^global green growth',
    r'\bgggi\b',
    r'^water supply.*sanitation.*collaborative',
    r'^international development law org',
    r'^south centre\b',
    r'^international trade centre',
    r'\bun-multi partner\b',
    # Generic multilateral/INGO categories
    r'^multilateral org',
    r'^international ngo\b',
    r'^ingos\b',
    r'^other multilateral',
    r'^other ingo\b',
    r'^other implementers\b',
    r'^institutions\b$',
    r'^goverments\b$',
    r'^governments\b$',
    r'^government\b$',
    r'^government experts\b$',
    r'^donor government\b',
    r'^donor government,',
    r'^recipient government\b',
    r'^third country government\b',
    r'^public sector\b$',
    r'^private sector\b',
    r'^public-private partnership\b',
    r'^local ngo\b$',
    r'^developing country.based ngo\b',
    r'^developing country ngo\b',
    r'^consultants?\b$',
    r'^german development cooperation',
]

# Exact-match international bodies
INTERNATIONAL_EXACT = {
    'OECD', 'OHCHR', 'UNESCO', 'UNICEF', 'UNIFEM', 'UNDPA', 'UNPOL',
    'WMO', 'WTO', 'IUCN', 'ISDR', 'ITTO', 'IPAS',
    'SPREP', 'ILO (non core)', 'FIDH', 'RCC',
    'EU and OSCE', 'Other', 'Other (VAI PPP?)',
    'Finnish NGOs', 'Finnish NGO',
    'INGOs', 'SANBio',
}


# ─── FINNISH GOVERNMENT PATTERNS ─────────────────────────────────────────────

GOVERNMENT_KEYWORDS = [
    r'^tilastokeskus\b',
    r'^ilmatieteen laitos\b',
    r'^ilmatieteen laitos\b',
    r'^geologian tutkimuskeskus\b',
    r'^geological survey of finland',
    r'^finnish environment institute',
    r'^suomen ympäristökeskus',
    r'^finnish meteorological institute',
    r'^natural resources? institute finland',
    r'^luonnonvarakeskus\b',
    r'^metsähallitus\b',
    r'^suomen metsäkeskus',
    r'^metsäntutkimuslaitos',
    r'^maanmittauslaitos\b',
    r'^museovirasto\b',
    r'^opetushallitus\b',
    r'^kansallisarkisto\b',
    r'^kansallisgalleria\b',
    r'^kansaneläkelaitos\b',
    r'^eläketurvakeskus\b',
    r'^liikennevirasto\b',
    r'^liikenteen turvallisuusvirasto',
    r'^väylävirasto\b',
    r'^maahanmuuttovirasto\b',
    r'^pelastusopisto\b',
    r'^poliisihallitus\b',
    r'^poliisin tekniikkakeskus\b',
    r'^rajavartiolaitos\b',
    r'^ruokavirasto\b',
    r'^tullihallitus\b',
    r'^verohallinto\b',
    r'^valtiokonttori\b',
    r'^valtion taloudellinen tutkimuskeskus',
    r'^säteilyturvakeskus\b',
    r'^elintarviketurvallisuusvirasto',
    r'^geodeettinen laitos\b',
    r'^mittatekniikan keskus\b',
    r'^kuluttajatutkimuskeskus\b',
    r'^rikosseuraamuslaitos\b',
    r'^työterveyslaitos\b',
    r'^terveyden ja hyvinvoinnin laitos',
    r'^national institute for health and welfare',
    r'^puolustusvoimat\b',
    r'^niuvanniemen sairaala\b',
    r'^suomen akatemia\b',
    r'^academy of finland\b',
    r'^arts promotion centre finland',
    r'^finnish national agency for education',
    r'^centre for international mobility',
    r'^kansainvälisen liikkuvuuden',
    r'^tax administration\b$',
    r'^tekes\b',
    r'^radiation and nuclear safety authority',
    r'^hus-yhtymä\b',
    r'^taide- ja kulttuurivirasto',
    r'^asumisen rahoitus- ja kehittämiskeskus',
    r'^biologisten uhkien osaamiskeskus',
    r'^cmcg? finland',
    r'^suomen itsenäisyyden juhlarahasto',
    r'^lääkealan turvallisuus- ja kehittämiskeskus',
    r'^ulkopoliittinen instituutti',
    r'^embassy of finland',
    r'^finnish embassy',
    r'^valteri-koulu',
    r'^elinkeino-.*kehittämis- ja hallintokeskus',
    r'^työllisyys-.*kehittämis- ja hallintokeskus',
    r'^tieteellisten seurain valtuuskunta',
    r'^eu civil protection mechanism',
    r'^euroopan avaruusjärjestö esa',
    r'^nuorisokeskus piispala',
    r'^hanasaari.*kulttuurikeskus',
    r'^nordisk kulturkontakt',
    r'^swedish.*folkting',
    r'^svenska finlands folkting',
    r'^samediggi',
    r'^saamelaisneuvosto\b',
    r'^finnish food safety authority',
    r'^finnish forest research institute',
    r'^finnish institute of public management',
    r'^maa- ja elintarviketalouden tutkimuskeskus',
    r'^other,\s*finnish environment institute',
    r'^other,\s*finnish meteorological institute',
    r'^other,\s*finnish police board',
    r'^other,\s*gtk\b',
    r'^other,\s*mtts?\b',
    r'^other,\s*vtt\b',
    r'^other,\s*nordic council of ministers',
    r'^suomen ympäristöopisto sykli',
    r'^ålands landskapsregering',
    r'^keva\b$',
    r'^finnfund\b',
    r'^saamelaisalueen koulutuskeskus',
    r'^integrated carbon observation system',
    r'^actris eric\b',
]


# ─── UNIVERSITY PATTERNS ───────────────────────���─────────────────────────────

UNIVERSITY_KEYWORDS = [
    r'^svenska handelshögskolan',
    r'^svenska social- och kommunalhögskolan',
    r'^sibelius-akatemia',
    r'^universidad de las regiones',
]


# ─── RESEARCH INSTITUTE PATTERNS ──────────────────────────────────────────────

RESEARCH_KEYWORDS = [
    r'^european forest institute',
    r'^pyhäjärvi-instituutti',
    r'^nordic africa institute',
    r'^the nordic africa institute',
    r'^uongozi institute',
    r'^institute for european environmental policy',
    r'^international institute for democracy',
    r'^international institute for environment',
    r'^international institute for sustainable development',
    r'^stockholm international water institute',
    r'^european centre for development policy',
    r'^european centre for minority issues',
    r'^center for global development',
    r'^european institute of peace',
    r'^small arms survey\b',
    r'^natural resource governance institute',
    r'^institute for human rights',
    r'^valtion taloudellinen tutkimuskeskus',
    r'^other,\s*regional environmental center',
    r'^global financial integrity',
    r'^institute of adult education',
    r'^research institute\b$',
]


# ─── CHURCH PATTERNS ──────────────────��─────────────────────────────���─────────

CHURCH_KEYWORDS = [
    r'kirkko\b',
    r'kyrka\b',
    r'^kirkon (keskusrahasto|ulkomaanapu)',
    r'church\b',
    r'^finn church aid',
    r'^finnchurchaid',
    r'^felm\b',
    r'luostari\b',
    r'^ortodoksinen kirkko',
    r'^suomen ortodoksinen kirkko',
    r'^katolinen kirkko',
    r'^oulun hiippakunnan tuomiokapituli',
    r'^missionskyrkan',
    r'^orthodox church aid',
    r'^baptist convention finland',
    r'^harvest church finland',
    r'^valamon luostari',
    r'^danakosha ling',
    r'^jesus christ assembly',
    r'^operaatio ruut',  # Christian mission organization
    r'^operation mobilisaiton',  # Missions org (OM)
    r'^portaanpään kristillinen opisto',
    r'^raudaskylän kristillinen opisto',
    r'^savonlinnan kristillinen opisto',
    r'^kiteen evankelinen kansanopisto',
    r'^the evangelical free church',
    r'^the free church federation',
    r'^uuden toivon seurakunnat',
    r'^bangladesh lutheran mission',
    r'^evangelical lutheran church',
]

# Islamic communities → church
ISLAMIC_PATTERNS = [
    r'islam(ilai|i).*yhdyskunta',
    r'islam\w*\s+(rahma\s+)?center',
    r'islamic\b',
    r'^as-salaam\b',
    r'^resalat\b',
]


# ─── ASSOCIATION (THIRD SECTOR) PATTERNS ───────────────────────────���──────────

ASSOCIATION_KEYWORDS = [
    # "Finnish NGO, ..." prefix → the org itself is a Finnish association
    r'^finnish ngo,\s+',
    # SPR = Suomen Punainen Risti (Red Cross)
    r'^spr\b',
    r'^suomen punaisen ristin',
    r'^finlands röda kors',
    r'^finnish red cross',
    # Mannerheim League
    r'^mannerheimin lastensuojeluliiton',
    r'^the mannerheim league',
    # Known Finnish NGOs by name
    r'^plan international finland',
    r'^save the children finland',
    r'^save the children.*donor country',
    r'^sos children.*villages finland',
    r'^world vision finland',
    r'^suomen world vision',
    r'^wwf finland',
    r'^interpedia\b',
    r'^disability partnership finland',
    r'^demo finland\b',
    r'^political parties of finland for democracy',
    r'^crisis management initiative',
    r'^fida international\b',
    r'^fairtrade finland\b',
    r'^finnwatch\b',
    r'^saferglobe\b',
    r'^attac\b',
    r'^suomen pakolaisapu',
    r'^the finnish refugee council',
    r'^refugee advice centre',
    r'^finnish refugee council',
    r'^suomen somalia-verkosto',
    r'^finnish somalia network',
    r'^inter press service finland',
    r'^international press service\b',
    r'^uff finland\b',
    r'^caritas finland\b',
    r'^unioni the league of finnish feminists',
    r'^national committee for un women in finland',
    r'^un women finland\b',
    r'^suomen unifem\b',
    r'^un global compact network finland',
    r'^finnish .* society\b',
    r'^finnish development ngos',
    r'^the finnish ngdo platform',
    r'^the finnish ecumenical council',
    r'^suomen ekumeeninen neuvosto',
    r'^pro ethical trade finland',
    r'^finnish water forum',
    r'^waterfinns\b',
    r'^liikunnan kehitysyhteisty',
    r'^the sport alliance of ymcas',
    r'^nuorten akatemia\b',
    r'^youth academy\b',
    r'^operation a day.s work finland',
    r'^finnish operation day.s work',
    r'^service center for development',
    r'^finnish agri-agency',
    r'^global music centre\b',
    r'^nada suomi',
    r'^suomen elvytysneuvosto',
    r'^soste finnish society',
    r'^puolueiden kansainvälinen demokratiayhteistyö',
    r'^progres institute for social democracy',
    r'^the finnish 4h-federation',
    r'^the guides and scouts of finland',
    r'^suomen partiolaiset\b',
    r'^population and family welfare federation',
    r'^world ywca\b',
    r'^women deliver\b',
    r'^worker.s educational assoc',
    r'^lions club\b',
    r'^kehitysvammaisten taitotupa',
    r'^ampersand.*ryhm',
    r'^rauhankasvatusinstituutti',
    r'^maailma\.net',
    r'^monikulttuurikeskus gloria',
    r'^yle helps\b',
    # Nuorten toimintaryhmä patterns (STEA youth groups)
    r'^nuorten toimintary',
    r'^451\s*-\s*nuorten toimintaryhmä',
    r'-nuorten toimintaryhmä',
    r'-ryhmä$',
    r'^[\w\s]+-ryhmä$',
    r'nuorten ryhmä',
    r'nuorisoryhmä',
    # STEA "activity groups" patterns (all-caps often)
    r'^[\w\s\-]+ -ryhmä$',
    r'toimintaryhmä',
    r'talotoimikunta',
    # Known Finnish civil society orgs
    r'^open cinema finland',
    r'^green cycling nordic finland',
    r'^suomen terveysjärjestö',
    r'^suomen vapaa-ajankalastajien',
    r'^global education network europe',
    r'^cisv\b',
    r'^heikintalo\b.*klubitalo',
    r'^sotkamon mielenterveyden tuki',
    r'^etelä-suomen vasemmistonuoret',
    r'^etelä-karjalan yrittäjät',
    r'^keskuskauppakamari',
    r'^seinäjoen urheilijat',
    r'^sotainvalidien veljesliiton',
    r'^arktiset maahanmuuttajat',
    r'^mtk-',
    r'^pohjois-pohjanmaan liikunta',
    r'^pohjois-savon liikunta',
    r'^lounais-suomen liikunta',
    r'^naisjarjestot yhteistyossa\b',
    r'^otaniemi international network',
    r'^riihimäen nuorisoteatteri',
    r'^nykyteatteri vera audentia',
    r'^suburban arts academy',
    r'^tampere film festival',
    r'^slush\b',
    # International NGOs (still tag as association for now, they're civil society)
    r'^oxfam\b',
    r'^care\b.*(international|deutschland)',
    r'^halo trust\b',
    r'^mines advisory group',
    r'^handicap international',
    r'^msi reproductive choices',
    r'^marie stopes international',
    r'^transparency international',
    r'^front line defenders',
    r'^pen international\b',
    r'^international alert\b',
    r'^international crisis group',
    r'^international committee of.*red cross',
    r'\bicrc\b',
    r'^international planned parenthood',
    r'\bippf\b',
    r'^international rehabilitation council',
    r'^international service for human rights',
    r'\bishr\b',
    r'^international work group for indigenous',
    r'\biwgia\b',
    r'^international peace institute',
    r'^international peacebuilding alliance',
    r'\binterpeace\b',
    r'^hivos\b',
    r'^civicus\b',
    r'^geneva call\b',
    r'^geneva centre for.*democratic control',
    r'^geneva international centre for humanitarian',
    r'\bgichd\b',
    r'^dialogue advisory group',
    r'^inter mediate\b',
    r'^forward thinking\b',
    r'^search for common ground',
    r'^justice rapid response',
    r'^danish demining group',
    r'^development aid from people to people',
    r'^rights and resources initiative',
    r'^publish what you pay',
    r'^global partners digital',
    r'^women.s world banking',
    r'^world organisation against torture',
    r'^minority rights group',
    r'^sos children.*villages\b',
    r'^sos femmes\b',
    r'^save the children\b',
    r'^panos institute\b',
    r'^international disability alliance',
    r'^international federation.*human rights',
    r'^international federation of agricultural',
    r'^international commission of jurists',
    r'^international criminal court.*trust fund',
    r'^lifeline embattled cso',
    r'^agriscord\b',
    r'^agri.?cord\b',
    r'^amani africa\b',
    r'^defenddefenders\b',
    r'^global alliance on disability',
    r'^cso partnership for development',
    r'^world federation of united nations assoc',
    r'^scholar rescue fund',
    r'^international aid transparency',
    r'^parliamentarians for global action',
    r'^peace operations training',
    r'^trade mark east africa',
    r'^natural voices\b',
    r'^tax justice network',
    r'^stichting onderzoek',
    r'^world federalist movement',
    # Finnish known associations without ry marker
    r'^borgerskapets i åbo',
    r'^gubbhemmet i åbo',
    r'^finlands svenska folkdansring',
    r'^flickscoutkåren',
    r'^scoutkåren\b',
    r'^sjöscoutkåren\b',
    r'^helsingfors.*scout',
    r'^helsingfors.*klubb',
    r'^helsingfors.*scouter',
    r'^helsingfors unga örnar',
    r'^helsingin\b.*nuoret\b',
    r'^helsingin\b.*vesaiset',
    r'^helsingin\b.*partio',
    r'^helsingin\b.*merikotkat',
    r'^helsingin\b.*metsän',
    r'^helsingin\b.*veikot',
    r'^helsingin\b.*kotkat',
    r'^helsingin\b.*tytöt',
    r'^helsingin\b.*pojat',
    r'^helsingin\b.*nmky',
    r'^helsingin\b.*nnky',
    r'^helsingin\b.*vpk\b',
    r'^helsingin\b.*siniset',
    r'^helsingin\b.*jellon',
    r'^helsingin\b.*kalev',
    r'^helsingin\b.*kulttuuri',
    r'^helsingin\b.*leijon',
    r'^helsingin\b.*pionee',
    r'^helsingin\b.*erä-',
    r'^helsingin\b.*vapaaseurakun',
    r'^helsingin\b.*piirijärjestö',
    r'^helsinkiläiset puoluepoliittiset',
    r'^jakomäen nuoret kotkat',
    r'^knk.*nuoret kotkat',
    r'^kontulan nuoret (kotkat|pioneerit)',
    r'^kontulan vesaiset',
    r'^herttoniemen nuoret kotkat',
    r'^myllypuron nuoret kotkat',
    r'^malminkartanon nuoret kotkat',
    r'^malmin nuoret kotkat',
    r'^pajamäen nuoret',
    r'^toukolan.*nuoret kotkat',
    r'^vuosaaren nuoret kotkat',
    r'^vuosaaren vesipääskyt',
    r'^oulunkylän ve(saiset|ikot)',
    r'^oulunkylän vpk',
    r'^vesaisten helsingin piiri',
    r'^etelä-kaarelan nuoret kotkat',
    r'^haaga-kaarelan vesaiset',
    r'^haagan (sirkut|vpk)',
    r'^eiran vihervillit',
    r'^malmin seudun partiolaiset',
    r'^nmky.*katajaiset',
    r'^nmky.*rastipartio',
    r'vpk nuoriso-osasto',
    r'^vesalan nuoret kotkat',
    r'^tammisalon (vpk|metsänkävijät)',
    r'^tapanilan (vpk|ilmailukerho)',
    r'^puistolan vpk',
    r'^pukinmäen vpk',
    r'^pakinkylän vpk',
    r'^lauttasaaren (vpk|luotsitytöt)',
    r'^laajasalon vpk',
    r'^töölön (nuotioveikot|siniset|tähystäjät)',
    r'^kulosaaren (meripartio|nmky)',
    r'^vartiovuoren (pojat|tytöt)',
    r'^sinivuoren tytöt',
    r'^viestitytöt',
    r'^kappelitytöt',
    r'^pursitytöt',
    r'^käpytytöt',
    r'^toimen tytöt',
    r'^navigatores\b',
    r'^pääkaupungin karjalaiset nuoret',
    r'^karjalan nuoret',
    r'^sdpl.*piirijärjestö',
    r'^luonto-liiton',
    r'^nuorisoyhteistyö seitti',
    r'^nuorisoteatteri\b',
    r'^pelastusarmeijan\b',
    r'^spartacus rs\b',
    r'^liike nyt r\.p\.',
    r'^perussuomalaiset r\.p\.',
    r'^suomen (keskusta|sosialidemokraattinen|kristillisdemokraatit).*r\.p\.',
    r'^kansallinen kokoomus r\.p\.',
    r'^kontutytöt\b',
    r'^krunalaiset\b',
    r'^kallionuoret\b',
    r'^kuunarikerho\b',
    r'^hiidenkiven klaani',
    r'^hipsut\b',
    r'^susiveikot\b',
    r'^metsolan pojat',
    r'^korven koukkaajat',
    r'^kuksat\b',
    r'^kulman kiertäjät',
    r'^huipunvaltaajat\b',
    r'^luontokerho parus',
    r'^ilmajoen sininauha',
    r'^supertiimi\b',
    r'^itä-helsingin kalakerhot',
    r'^helsingin kalamiespiiri',
    r'^silta-klubi\b',
    r'^hespartto\b',
    r'^behind the scenes\b',
    r'^circus helsinki\b',
    r'^club kuumotus\b',
    r'^progrupper\b',
    r'^saharan sissit\b',
    r'^erä-valakia\b',
    r'^kokoonpano\b',
    r'^jatkumo\b',
    r'^maailmanlopun pyörä',
    r'^rajat\b$',
    r'^vapaa liikkuvuus',
    r'^sosiaalikeskus siperia',
    r'^koodiviidakko\b',
    r'^neurofunk rallyboys',
    r'^rhythms of resistance',
    r'^sade festival\b',
    r'^mushroom forest\b',
    r'^music for friends\b',
    r'^one shot battles\b',
    r'^helsinki bmx\b',
    r'^skateboarding is not a crime',
    r'^little asia in helsinki',
    r'^viikin asukastalon',
    r'^suomen musliminuorten',
    r'^respectg?\s*[-–]',
    r'^respect\b.*nuorten',
    r'^left youth\b',
    r'^national union of finnish students',
    r'^suomen nuorten.*yhdist',
    r'^suomen nuorten ja opiskelijoiden',
    r'^the finnish federation of swedish speaking',
    r'^garantiforen',
    r'^suomalais.*ruotsalainen kulttuurirahasto',
    r'^sverigefinnarnas arkiv',
    r'^foreningen for framstegsvanlig',
    r'^european evaluation society',
    r'^helsinki pool boys',
    r'^finnish.namibian society',
    r'^finnish-arab friendship society',
    r'^inter-cultur\b',
    r'^all our children\b',
    r'^kokkolan afganistanilaiset',
    r'^kuopion seudun seniorit',
    r'^hikmah nuoret',
    r'^rakkauden maa\b',
    r'^wellfare oasis community',
    r'^turun nuorten muslimien',
    r'^somali sosiaalikehitys',
    r'^somali reconstruction',
    r'^sool sanaag',
    r'^suomen bosnjakkien',
    r'^suomen muslimiopiskelij',
    r'^suomen koptiortodoksit',
    r'^suomen vietnamilaisten',
    r'^suomalais vietnamilainen',
    r'^pohjois-suomen islamilainen',
    r'^vantaan islamilainen',
    r'^itä-vantaan islamilainen',
    r'^espoon islamilainen',
    r'^turun bosnjakien',
    r'^turun islamilainen',
    r'^tampereen islamin',
    r'^helsingin.*islam',
    r'^helsingin debre amin',
    # Red Cross international arms
    r'^kenya red cross society',
    r'^council of churches in namibia',
    # Development NGOs abroad
    r'^action aid vietnam',
    r'^action for relief.*development',
    r'^afortalecer',
    r'^africa.*centre for.*constructive resolution',
    r'^banadir women development',
    r'^caring hands\b',
    r'^children of (nakuru|zimbabwe)',
    r'^the children of zimbabwe',
    r'^coalition for environment and develop',
    r'^committee for ethnic minorities',
    r'^crash\b.*coalition for research',
    r'^development concern\b',
    r'^development for african education',
    r'^dignity international\b',
    r'^educational initiatives trust',
    r'^femmes africa solidarite',
    r'^forum for african women edu',
    r'^green living movement\b',
    r'^hogares providencia',
    r'^i-aid\b',
    r'^inf nepal\b',
    r'^instituto mozambicano',
    r'^inter press service\b',
    r'^kalali women dairy',
    r'^kalibu ministries',
    r'^khulisa social',
    r'^kilimanjaro women',
    r'^legal and human rights centre',
    r'^maf mongolia',
    r'^maedot\b',
    r'^mamta\b.*health institute',
    r'^movimiento comunal',
    r'^namibian national teachers',
    r'^nomadic development',
    r'^netherlands institute for multiparty',
    r'^ortaid\b',
    r'^patmos ararat',
    r'^programa de atencion',
    r'^psychologists for social responsibility',
    r'^physicians for social responsibility',
    r'^refugee law project',
    r'^sekolah rakyat petani',
    r'^society for the welfare of autistic',
    r'^syria justice and accountability',
    r'^the national institution of social care',
    r'^twaweza\b',
    r'^uraia trust\b',
    r'^women education project',
    r'^zimbabw.*aids.*orph',
    r'^aid for the disadvantaged',
    r'^afrikkalaisten vahaosaisten',
    r'^adpp\b',
    r'^associates to develop democratic',
    r'^helina rautavaara',
    r'^helinä rautavaara',
    r'^semcon-friends\b',
    r'^shalin\b',
    r'^synapse network center',
    r'^deaconess institute in helsinki',
    r'^helsinki deaconess institute',
    r'^the martha organization',
    r'^flom in mongolia',
    r'^g7\+\b',
    r'^global alliance for clean cookstoves',
    r'^global e-schools',
    r'^global equality fund',
    r'^international assistance mission',
    r'^intl\.?\s+centre for transitional',
    r'^international center for transitional',
    r'^international centre for transitional',
    r'^int centre trade and sustainable',
    r'^international centre for trade',
    r'^intl\.?\s+instit',
    r'^intl\.?\s+planned parenthood',
    r'^poverty environment partnership',
    r'^multilateral organisation performance',
    r'\bmopan\b',
    r'^arms trade treaty secretariat',
    r'^extractive industries',
    r'^african tax administration',
    r'^ifap\b',
    r'^kosovo (academy|security force)',
    r'^partnership fund for a resilient ukraine',
    r'^fellowship for doctoral students from ukraine',
    r'^south pole\b',
    r'^BEAM/DevPlat',
    r'^media and development fund',
    r'^office of the secretary-general',
    r'^savitapale nuorison tuki',
]

# Patterns for the "Nuorten toimintaryhmä ..." all-caps STEA groups
NUORTEN_RYHMA_PATTERN = re.compile(
    r'^nuorten toimintaryhmä\b', re.IGNORECASE
)


# ─── COMPANY PATTERNS ──────────────────────────────────────��──────────────────

COMPANY_KEYWORDS = [
    # T:mi / Tmi / F:ma patterns (sole traders)
    r'^t:mi\b',
    r'^tmi\b',
    r'^f:ma\b',
    r'^toiminimi\b',
    # Person names (first + last only, no org suffix) → likely sole trader
    # We'll handle these separately
    # Common company patterns in data
    r'\boy\b.*oy\b',  # double OY (e.g. "CURSOR OYKOTKAN-HAMINAN")
    r'oy[a-zäöå]',    # OY merged with next word (CURSOR OYKOTKAN)
    r'\bkyg?\b.*[a-z]',  # KY with additional text
    # Explicit company names
    r'^eka-asennus\b',
    r'^porokylän leipomo',
    r'^midland communications',
    r'^innomost\b',
    r'^kt company\b',
    r'^pisa design\b',
    r'^step2fit\b',
    r'^k3 strength',
    r'^autovarustamo\b',
    r'^hammaslab\b',
    r'^hammaslaboratorio\b',
    r'^hotelli\b',
    r'^ravintola\b',
    r'^taksi\b',
    r'^balettikoulu\b',
    r'^hiushuone\b',
    r'^apteekki\b',
    r'^juankosken apteekki',
    r'^lihanjalostamo\b',
    r'kehittämiskeskus oy',
    r'kehittämisyhtiö',
    r'kehitysyhtiö',
    r'näringslivscentral',
    r'^ab\b.*utvecklingsbolag',
    r'^keski-uudenmaan kehittämiskeskus oy',
    r'^cursor oy',
    r'^innovaatio oy',
    r'^lieksan kehitys oy',
    r'^pielisen karjalan kehittämiskeskus',
    r'^kiinteistö\s+(oy|astra|sallinkatu)',
    r'^kiinteistöyhtymä\b',
    r'^keskinäinen kiinteistö',
    r'^asunto-osakeyht',
    r'^erikoissijoitusrahasto\b',
    r'^kpmg\b',
    # Pattern: word + OY/KY merged
    r'oy\d',         # OY followed by number (Y-tunnus)
    r'oy[A-ZÄÖÅ]',  # OY merged with uppercase next word
    r'ky[A-ZÄÖÅ]',  # KY merged
    # Various small businesses
    r'^pulverfix\b',
    r'^puska metal\b',
    r'^servux\b',
    r'^design bod\b',
    r'^flamingo productions\b',
    r'^green house wear\b',
    r'^orion.s belt\b',
    r'^artem & yonas\b',
    r'^cederbergs verkstad',
    r'^oscar holmström',
    r'^konekorjaamo\b',
    r'^monitoimipalvelu\b',
    r'^kiint oy\b',
    r'^etelä-savon kauppakamari',
    r'^etelä-savon sosiaali-',
    r'^saksalais-suomalainen kauppakamari',
    r'^finsk-svenska handelskammaren',
    r'^oulun kauppakamari\b',
    r'^kuopion kauppakamari\b',
    r'^porin teollisuuskiinteistöt',
    # All known OY-based (double check)
    r'\boyjuuso\b',
    r'\boyjauhemaalaamo\b',
    r'\boyristo\b',
    r'\boyco\b',
    r'\boykotkan\b',
    r'\boyylivieska\b',
    r'\boylahti\b',
    r'\boyolli\b',
    r'\boyteollisuustie\b',
    r'\boyhouse\b',
    r'\boyhannu\b',
    r'\boyab\b',
    r'\boykalle\b',
    r'\boykl-digipaino\b',
    r'\boymikko\b',
    r'\boymarko\b',
    r'\boyjarno\b',
    r'\boyseven\b',
    r'\boynordica\b',
    r'\boyproagria\b',
    r'\boyexiops\b',
    r'\boyteemu\b',
    r'\boy\s*finland\b',
    r'\boy\s*inc\b',
    r'\boy\s*ltd\b',
    r'\boy\s*suomi\b',
    r'\boy\s*roi\b',
    r'\boy\s*ltdjori\b',
    r'\boy\s*kouvola\b',
    r'\boy\s*kotka\b',
]

# Named persons (likely sole traders / companies)
PERSON_NAME_PATTERNS = [
    r'^[A-ZÄÖÅ][a-zäöå]+ [A-ZÄÖÅ][a-zäöå]+$',  # "Firstname Lastname" exactly
    r'^[A-ZÄÖÅ][a-zäöå]+ [A-ZÄÖÅ][a-zäöå]+ [A-ZÄÖÅ][a-zäöå]+$',  # Three-word name
    r'^\w+ \w+ kuolinpesä$',  # estate
]

# Specific known companies from the list
COMPANY_EXACT = {
    'A-Films Japan', 'AJL-GUITARS', 'ALCEA', 'ArKo Holding', 'Avantin Lounas-ravintola',
    'BIOGAS ENERGY TMI.', 'BOTLABS', 'Cederbergs Verkstad', 'DELTAREC OYSAPPEENVUOREN HIIHTOKESKUS',
    'Design BOD', 'ELONEN OY LEIPOMO', 'ENPROS', 'ESKO ADVENTURES', 'EVERT OY SUOMI',
    'FGJ-GROUP', 'FINNCODE OYYLIVIESKA', 'FINNISH LAPLAND TOURIST BOARD',
    'FINNMEDTRAVELOY', 'FINNOSPACE', 'FINNVETTE', 'FLASH-TUOTANTO',
    'FOOD AND HEALTH TECH FINLAND OY INC.', 'GENERATE OY INC', 'GREEN ATTACHMENTS',
    'GYMSTICK INTERNATIONAL OYRISTO KASURINEN', 'HBR FINLAND', 'HENKILÖSTÖRAHASTOPALVELUT OY LAHTI',
    'HOTELLI KORPIKARTANO', 'HOUSE OF LAPLAND OYHOUSE OF LAPLAND',
    'HUTEK OYKALLE UUSITALO', 'INDOOR SKYDIVING CONSULTATION ISC OY LTD.',
    'INSINÖÖRITOIMISTO HUOVINEN', 'JAUHETEKNIIKKA OY KOTKA', 'JENNI RUTONEN DESIGN',
    'K- Närbutik Martens', 'KATRIINA\'S SPA', 'KL-KOPIO OYKL-DIGIPAINO (REK. APUTOIMINIMI)',
    'KONEISTUS VILCO OYC/O JARI PORVARI', 'KUOPION RAKENNUSTARVIKE OY / RT-KALUSTE',
    'KUUKIVI DESIGN OY.', 'LAPIN ELÄMYSTUOTANTO OYHOUSE OF LAPLAND', 'LAPIN MAINOSTUOTANTO',
    'LASITUSLIIKE M & J RAUTIO', 'LAURAN DESIGN', 'LINJATERÄS OYJAUHEMAALAAMO',
    'LOMAMOKKILA', 'LUJAPINTA OY FINLAND', 'LUXURY ACTIONLUXURY ACTION',
    'MAINOSTOIMISTO KIUAS OYSEVEN-1', 'MAKEEdesign', 'MARKETING AND COMMUNICATION OY MAC LTD.',
    'MATEXPRO', 'MEDIAATTORI OYPODIUM', 'MEDRIAN OYPIETARINEN RIITTA HELENA',
    'METALLIKONEPAJA K. NIEMELÄ', 'METALLIPALVELU J.HINTSALA', 'METALLISORVAAMO A. PAKARINEN',
    'MINORITO OY (LAPONIE LIFE OY)', 'MT-Konepalvelu', 'NorjaWire', 'Pacta Asia Limited',
    'PETCONS OY / TACKLA PRO', 'PHOTOKRAFIX', 'PK Sähkösuunnittelu Ja Asennus',
    'POHJOIS-SUOMEN HIRSITALOKESKUS OYMAMMUTTIHIRSI', 'POHJOLAN ELÄINTUHKAUS OY, PETU',
    'POLAR LIGHTS TOURS OY ROI', 'PR-BUILDING', 'PRO SÄRMÄYS', 'PRODISCUS',
    'PROFIN OY /(PROHAAPALA)', 'PROJECT BUSINESS OY FINLAND', 'PROLASER OY LTDJORI ANTILA',
    'PUHDISTUSPALVELUT CLEANING DESIGN OYCLEANING DESIGN', 'PUNKAHARJUN PUUTAITO OYJANNE TIRRONEN',
    'PUUSEPÄNLIIKE ARI KARVO', 'RANUA-REVONTULI OY  VIINI & MARJAT',
    'RECEIPTLESS SOFTWARE', 'REFORM KY (RFM)', 'ROVAKADUN SUUTARI JA AVAINPALVELU',
    'SAPOTECH', 'SCHAUPRO OY2193160-5', 'SIMEC SYSTEMS KYSIMO AITTO-OJA',
    'SISUWOOD OY2034683-4', 'SIXTENS MASKIN OCH TRANSPORT', 'STARTUP100*',
    'SUKELLUSPALVELU STELLA MARIA', 'SÄRKKÄIN PIHVIRAVINTOLA OYRAVINTOLA PIHVITUPA',
    'TAGOMO DIGITAL OY LTD.', 'TIMAPUU OY MARTTILA', 'TMI LAURI JARVINEN',
    'TOIMINTATERAPIA FLOW', 'TUKI- JA TERAPIAPALVELU GENEESI',
    'TWOY ENGINEERING OY(TIETO-WIZARD OY)', 'Tiger', 'Tiger Clean Finland',
    'VALKEA LAPLAND OYANTTI-PEKKA PALOKANGAS', 'VALKOSEN PELTISEPÄNLIIKE',
    'VARSINAISBITUMI OY HALLINTO', 'VEEN WATERS FINLAND OYMIKKO NIKKILÄ',
    'VEEPEE KONEISTUS', 'VELJEKSET HIETAMÄKI AY', 'VENTO STEEL OYTONI VENÄLÄINEN',
    'VIKSTRÖMS PLÅTSLAGERI AB OYVARJOPUOLI AB, APUTOIMINIMI',
    'VMK valmennus', 'VOLTER OYJARNO HAAPAKOSKI', 'WELHOFILMI',
    'WICIOT OYMARKO HÖYNÄLÄ', 'ZIP ADVENTURE PARK', 'Äijälän Rusti',
    'Övermarkin Kolarikorjaamo',
    'Helle Motorsport', 'Helsingin Studiopalvelut', 'Huopatehdas Johanna Alho',
    'Konsulttitoimisto Seppo Hoffrén Oy Consultancy', 'Kutvonen motion pictures',
    'Lapin Ykkössähkö', 'Liikuntakeskus Välke', 'Lomakartano Kivennapa',
    'Kaskiin Konekorjaamo', 'Kekkerikeittiö Ritakorpi', 'Kiinteistö Oy Kaapelitalo',
    'Kirstulan Kartano', 'Paimion Autohuolto, Helenius', 'Procolor Autopalvelut Mynämäen Automaalaamo',
    'Satula.com Finland', 'Sauna Hermanni', 'Seven Productions', 'Tee-Hoo Myynti',
    'Timanttipolku', 'Vuelta', 'Vuorelan Rauta', 'Viinamäen auto ja konekorjaamo',
    'Hannah la dolce vita', 'PRODES', 'HAHTUVAHULLUTEIJA KUHANEN',
    'HOHTOA FINLAND OYCO PUTIIKKI RANNALLA', 'HOT MIX OY FINLAND',
    'HS-VISUAL ART KYHANNU SIEVILÄ', 'HUCOH', 'DIMEX OY0477986-7',
    'DOSETEC EXACT OYOLLI-PEKKA RIHU', 'ELEMENTTI SAMPO OYC/O TILI- JA TARKASTUS ARI PIIRAINEN',
    'EVERWHATPRODUCTIONSANTTI KAARLELA', 'EXIOPS OYEXIOPS', 'KINONIKINONI',
    'KIVAQ OYAB', 'BRO BONO OYJUUSO SORJONEN', 'KUVASUHDE', 'MINNAMURRA MUSIC',
    'H-Team Mobile om. Teuvo Hänninen', 'ICCUNA OYTEEMU HAAPALAHTI',
    'LESTIJÄRVEN KUNTAKAUSTISEN SEUTUKUNTA', 'HAMMASLAB OY KOUVOLA',
    'HAMMASLABORATORIO DENTAL STUDIO', 'M HANDMADE', 'MACUTEC MATTI KUMPULAINEN',
    '4DBARN OYPROAGRIA OULU', 'NORTHERN ACTIVITIES', 'NORTSA V KYNORMUNDS VEINBERGS',
    'AB KRISTINESTADS NÄRINGSLIVSCENTRAL', 'KESKI-UUDENMAAN KEHITTÄMISKESKUS OY KEUKE',
    'KESKINÄINEN KIINTEISTÖ-OY AURINKOPAJA', 'Ammattienedistämislaitos AEL',
    'HELSINKI BAROQUE ORCHESTRA',
    'Jamkicks Oy / Demo iltapäiväkerho', 'REDERI AB FAKIR',
    'INARIN KUNTAELINKEINOT & KEHITYS NORDICA',
    'Private export company', 'JANNEN MAATALOUS OYMYSSYFARMI',
    'JUSSILA PEKKA T:MILEVIN ERÄTULET', 'JYRI & LAURA PAASONEN',
    'KARVINEN, PETRITMI PETRI KARVINEN', 'Enberg Marko ja Pasi',
}


# ─── COOPERATIVE PATTERNS ────────────────────────────────────────────────────

COOPERATIVE_KEYWORDS = [
    r'^camera cagliostro elokuvaosuuskunta',
    r'^taideosuuskunta\b',
    r'^kulttuuriosuuskunta\b',
    r'^herttoniemen ruokaosuuskunta',
    r'^valokuvataiteilijoiden.*osuuskunta',
    r'osuuskunta\b',
]


# ─── SECOND-PASS EXACT MATCHES (for remaining NULLs) ──────────────────────────

SECOND_PASS_EXACT = {
    # International organizations
    'Convention to Combat Desertification (UNCCD)': 'international',
    'Economic Commission for Africa': 'international',
    'European and Mediterranean Plant Protection Organisation (EPPO)': 'international',
    'IDA - Multilateral Debt Relief Iniative': 'international',
    'IUCN, World Conservation Union': 'international',
    'Inter-American Institute for Co-operation on Agriculture': 'international',
    'Inter-American Institute for Co-operation on Agriculture (IICA)': 'international',
    'International Center for Integrated Mountain Development (ICIMOD)': 'international',
    'International Centre for Research on El Nino': 'international',
    'International Maritime Organization - Technical Co-operation Fund': 'international',
    'International Organization for Cooperation in Evaluation': 'international',
    'International Organization for Standardization ISO': 'international',
    'International Organization of Supreme Audit Institutions': 'international',
    'International Organization of Supreme Audit Institutions (INTOSAI)': 'international',
    'International Strategy for Disaster Reduction': 'international',
    'International Union for Conservation of Nature': 'international',
    'International Union for the Conservation of Nature (IUCN)': 'international',
    'International Union for the Conservation of nature (IUCN)': 'international',
    'International Union of Forest Research O': 'international',
    'International Union of Forest Research Organizations': 'international',
    'International Union of Forest Research Organizations (IUFRO)': 'international',
    'Organisation of American States': 'international',
    'Secretariat of the Pacific Regional Environment Programme (SPREP)': 'international',
    'The International Maritime (IMO)': 'international',
    'The United Nationsthe Department of Economic and Social Affairs DESA': 'international',
    'The World Federalist Movement - Institute for Global Policy (the Agency)': 'international',
    'YK:n kehitystoimintoja koordinoiva toimisto': 'international',
    'Maailmanpankkiryhmä': 'international',
    'Independent Commission for Human Rights (ICHR)': 'international',
    'Adetef': 'international',  # French gov tech cooperation agency
    'Department of Forestry and Non-Renewable Natural Resources (DFNR)': 'international',
    'Other, Environment and Security Initiative (ENVSEC)': 'international',
    'Other, Global Gender and Climate Alliance (GGCA)': 'international',
    'Other, National Agency for the Prohibition of Traffic in Persons and Other Related Matters': 'international',
    'Other, institutions': 'international',
    'IPAS-Protecting Women\'s Health, Advancing Women\'s Reproductive Rights': 'international',
    'Protecting Women’s Health, Advancing Women’s Reproductive Rights': 'international',
    'Protecting Women?s Health, Advancing Women?s Reproductive Rights': 'international',

    # Government / education
    'Keski-Pohjanmaan Koulutusyhtymä': 'government',
    'Ylä-Savon ammattilisen koulutuksen kuntayht.': 'government',
    'Kouvolan aikuiskoulutuskeskus': 'government',
    'Aitoon emäntäkoulu Oy/Aitoon kotitalousoppilaitos': 'government',
    'Suomalais-Venäläinen koulu': 'government',
    'Opetusalan ammattijärjestö': 'association',  # OAJ trade union

    # Research
    'Instituto de Estudeos Sociais e Economicos (IESE)': 'research',
    'Instituto de Estudos Sociais e Economicos': 'research',
    'Instituto de Estudos Sociais e Económicos': 'research',
    'Media Institute of Southern Africa Tanzania Chapter (MISA-Tanzania)': 'research',

    # Church / religious
    'Finlands svenska baptistsamfund': 'church',
    'Suomen Kristiyhteisö - Kristensamfundet i Finland': 'church',
    'Suomen Nuorten Miesten Kristillisten Yhd': 'church',
    'The Finnish Evengelical Lutheran Mission': 'church',
    'Ilembula Lutheran Hospital/ Palliative Care Team': 'church',

    # Association (third sector)
    'Ab Det finlandssvenska kompetenscentret inom det sociala området': 'association',
    'Alands fredsinstitut': 'association',  # Ålands fredsinstitut peace research
    'Arap Moi Children\'s Home as Nakuru branch of the Child Welfare Society of Kenya': 'association',
    'Asociacion Grupo de Trabajo Redes (AGTR)': 'association',
    'Afg': 'association',  # NGO abbreviation in UM data
    'Afghanistan Independent Human Rights Commission': 'association',
    'AARNIKARHUT': 'association',  # Scout/youth group
    'AARNIVALKEAT': 'association',  # Youth group
    'ARKTISET AROMIT - ARKTISKA AROMERRY': 'association',
    'BALTIC ORGANISATIONS NETWORK FOR FUNDING SCIENCE ETEY': 'association',
    'BALTIC ORGANISATIONS NETWORK FOR FUNDING SCIENCE ETEY*BONUS EEIG': 'association',
    'CLEARING THE FREQUENCY': 'association',  # Youth activity group
    'FIVR': 'association',  # Finnish org
    'Finnish Ass. of the Deaf': 'association',
    'H.A.C. RYHMÄ': 'association',
    'HELSINGIN JUUTALAINEN NUORISOSEURARY': 'association',
    'HELSINGIN NUORI KANSALAISAKTIVISMI': 'association',
    'HELSINGIN PAIKALLISRYHMÄ': 'association',
    'HELSINGIN PUNAINEN NUORISOPIIRI': 'association',
    'HELSINGIN SOS.DEM. NUORISOPIIRI': 'association',
    'INFINITE COLOURS': 'association',  # Youth group
    'JOZIK-SUOMALAIS-VENÄLÄINEN NUORTEN KULTTUURIRYHMÄ': 'association',
    'KANSAINVÄL PERHEKRIISIEN TUKIYHD THE FAMILY CENTER': 'association',
    'KASKIPARTIO': 'association',  # Scout group
    'KULOSAAREN YHTEISKOULUN LUKION THIMUN-DELEGAATIO': 'association',
    'Landsforeningen U-landshjalp fran Folk t': 'association',
    'Lastenkerho': 'association',
    'MAAHANMUUTTAJASEMINAARI -TYÖRYHMÄ': 'association',
    'MALM SVENSKA UNGDOMSFORENING': 'association',
    'MANGARYHMÄ': 'association',
    'MARIAN VAHVISTUSRYHMÄ': 'association',
    'MUNKSNÄS FLICKSCOUTER': 'association',
    'Meidän juttu': 'association',
    'Merellinen Oulu, Oulu-laiva': 'association',
    'Metropolia polytechnic Student Union (METKA)': 'association',
    'NDJARA': 'association',  # Cultural group
    'NUKKETEATTERI PIKKUKULKURI': 'association',
    'NUORTEN DRAGON -TYÖRYHMÄ': 'association',
    'NUORTEN KYLÄJUHLATOIMIKUNTA': 'association',
    'Nguna Group of women Christina Mkumbo': 'association',
    'NiceBandy': 'association',
    'OULUNKYLÄN VIRKUT': 'association',
    'Other, AMSCO': 'international',
    'Other, Finnish Water Forum (FWP)': 'association',
    'Pohjois-Pohjanmaan omatoimisen työllistymisen tuki': 'association',
    'RASMUS VERKOSTON HELSINGIN JÄRJESTELYTYÖRYHMÄ': 'association',
    'RAUHANKOULUN FOORUMITEATTERIRYHMÄ': 'association',
    'ROIHUKINO-TYÖRYHMÄ': 'association',
    'SHOW-RYHMÄ STOLICHNAJA': 'association',
    'SUOMEN LATU RYSUOMEN LATU KIILOPÄÄ': 'association',
    'Siemenpuu-Kansalaisliikkeiden yhteistyos': 'association',
    'The African Centre for the Constructive Resolution of Disputes': 'association',
    'The Development Ass. of Aetsa Region': 'association',
    'The Finnish Society for Nature and Envir': 'association',
    'The National Council of The Gambia YMCAs': 'association',
    'TEATTERIRYHMÄ KOHTAUS': 'association',
    'TEATTERIRYHMÄ/MARIA KAURISMÄKI': 'association',
    'TOISKUN NUORET': 'association',
    'TRENDSPOTTING-KOLLEKTIIVI': 'association',
    'VIIPURIN METSÄNKÄVIJÄT': 'association',  # Scout group
    'VUORENSAKU': 'association',
    'VUOSAAREN KIINTEISTÖT OY:N VUOKRALAISTOIMIKUNTA': 'association',
    'Valkealan kristillisen kansanopiston kannatusyhdis': 'association',
    'WOMEN IN FILM & TELEVISION FINLANDRY': 'association',
    'Zimbabwen AIDS-orvot': 'association',
    'Ilmastoahdistus lyhytelokuvan työryhmä': 'association',
    'Kiitos Rouva Lagerstedt lyhytelokuvan työryhmä': 'association',
    'G7+': 'international',
    'BEAM/DevPlat': 'government',  # Business Finland program
    'Tenon kalatalousalue': 'association',  # Fishing management area (yhteisö)
    'ProAgria Pohjois-Karjala': 'association',  # Agricultural advisory service
    'Start of Your Ending': 'association',  # Band / youth group
    'ROMULUX': 'association',  # Youth activity
    'KÄSITYÖKLUBI': 'association',
    'KÄÄNTÖPAIKKA': 'association',

    # Company
    'A. Kahma': 'company',
    'BENJAMIN TAYLOR': 'company',
    'B.A.S. Femek': 'company',
    'ELÄINLÄÄKÄRI PEKKA HAKALA': 'company',
    'IFTIN BASEMENT IB': 'company',
    'IPATRIR': 'company',
    'ITÄ-SUOMEN HUOLTOPALVELU SAROLA': 'company',
    'JARKKO HAAVISTO1975': 'company',
    'Jasg': 'company',
    'Joukiisenranta': 'company',
    'Kuorma-autoilija Jari Immonen': 'company',
    'KYYVEDEN OSAKASKUNTA': 'company',  # Osakaskunta = shareholders' association (fishing)
    'Ljungqvist Alf Andre Yksityinen elinkeinoharjoittaja': 'company',
    'MATIAS & CO': 'company',
    'MATKAILUN ABC-TEAM': 'company',
    'Markkula Pekka Martti Johannes': 'company',
    'MottiMikko': 'company',
    'Niinistö Ahti Leopold kuolinpesä': 'company',
    'OTTO SILVENNOINEN': 'company',
    'PÄÄSKYNIEMEN LEMMIKKIELÄINHOITOLA': 'company',
    'Powwe\'r': 'company',
    'RAKENNUS EXTRA': 'company',
    'Reno-Produkt-Tuote, innehavare Leif Erik Senkas': 'company',
    'Sandberg Clas-Henrik': 'company',
    'Sumba Primary School Sumba West': 'association',  # School development project
    'Tamara Rasmussen Opisto om. Vivianne Budsko-Lommi': 'company',
    'The Midnight Sun Ashtanga Yoga School': 'company',
    'Timon mökkivuokraus': 'company',
    'ToimivaMinä': 'company',
    'Tulkkaus- ja käännöspalvelut Safin': 'company',
}


# ─── CLASSIFIER ──────────────────────────────────────────────────────────────

def classify_null(name: str) -> str | None:
    """Attempt to classify an org where basic heuristics failed."""
    original = name.strip()
    lower = original.lower()

    # ── Exact matches first ──
    if original in SECOND_PASS_EXACT:
        return SECOND_PASS_EXACT[original]
    if original in INTERNATIONAL_EXACT:
        return 'international'
    if original in COMPANY_EXACT:
        return 'company'

    # ── Cooperative (before association, since osuuskunta is specific) ──
    for pat in COOPERATIVE_KEYWORDS:
        if re.search(pat, lower):
            return 'cooperative'

    # ── International organizations ──
    for pat in INTERNATIONAL_KEYWORDS:
        if re.search(pat, lower):
            return 'international'

    # ── Government ──
    for pat in GOVERNMENT_KEYWORDS:
        if re.search(pat, lower):
            return 'government'

    # ── University ──
    for pat in UNIVERSITY_KEYWORDS:
        if re.search(pat, lower):
            return 'university'

    # ── Research ──
    for pat in RESEARCH_KEYWORDS:
        if re.search(pat, lower):
            return 'research'

    # ── Church ──
    for pat in CHURCH_KEYWORDS:
        if re.search(pat, lower):
            return 'church'
    for pat in ISLAMIC_PATTERNS:
        if re.search(pat, lower):
            return 'church'

    # ── Association (third sector) ──
    for pat in ASSOCIATION_KEYWORDS:
        if re.search(pat, lower):
            return 'association'

    # Nuorten toimintaryhmä (all variants)
    if NUORTEN_RYHMA_PATTERN.search(original):
        return 'association'

    # ── Company patterns ──
    for pat in COMPANY_KEYWORDS:
        if re.search(pat, lower):
            return 'company'

    # Person name patterns (sole traders)
    for pat in PERSON_NAME_PATTERNS:
        if re.match(pat, original):
            # Exclude some false positives (known orgs that look like names)
            if not any(w in lower for w in ['fund', 'trust', 'council', 'institute']):
                return 'company'

    # "Other, ..." prefixed - examine what's after
    if lower.startswith('other, '):
        remainder = lower[7:]
        # Companies
        if any(w in remainder for w in ['oy', 'valtra', 'stx', 'eltel', 'econet',
                                         'ge healthcare', 'instrumentarium',
                                         'export company', 'consultant']):
            return 'company'
        # Already handled government ones above
        # International
        if any(w in remainder for w in ['wco', 'eac', 'ecnc']):
            return 'international'
        # Leave truly ambiguous as NULL
        return None

    # "Developing country-based NGO, ..." pattern
    if 'developing country' in lower and 'ngo' in lower:
        return 'association'

    return None


def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Fetch all NULL-sector rows
    rows = cur.execute(
        "SELECT rowid, source_name FROM org_mapping WHERE sector IS NULL"
    ).fetchall()
    print(f"NULL-sector rows to classify: {len(rows)}")

    # Classify
    updates = []
    sector_counts: dict[str, int] = {}
    for rowid, name in rows:
        sector = classify_null(name)
        if sector:
            updates.append((sector, rowid))
            sector_counts[sector] = sector_counts.get(sector, 0) + 1

    # Apply updates
    if updates:
        cur.executemany("UPDATE org_mapping SET sector = ? WHERE rowid = ?", updates)
        conn.commit()

    classified = len(updates)
    remaining = len(rows) - classified
    print(f"\nClassified: {classified}")
    print(f"Remaining NULL: {remaining}")

    print("\n--- Newly classified breakdown ---")
    for sector, count in sorted(sector_counts.items(), key=lambda x: -x[1]):
        print(f"  {sector:15s} {count:>5}")

    # Show remaining NULLs
    still_null = cur.execute(
        "SELECT source_name FROM org_mapping WHERE sector IS NULL ORDER BY source_name"
    ).fetchall()
    print(f"\n--- Sample of remaining NULLs ({len(still_null)} total) ---")
    for row in still_null[:40]:
        print(f"  {row[0]}")

    # Overall distribution
    print("\n--- Full sector distribution after update ---")
    results = cur.execute(
        "SELECT sector, COUNT(*) FROM org_mapping GROUP BY sector ORDER BY COUNT(*) DESC"
    ).fetchall()
    for sector, count in results:
        print(f"  {sector or 'NULL':15s} {count:>6}")

    conn.close()


if __name__ == "__main__":
    main()
