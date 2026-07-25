# RWI – Plan för förenkling och ombyggnad

## Syftet (så vi inte tappar bort det igen)

En snygg, aktuell översikt över Runway Safe (RW):
1. Vilka flygplatser har EMAS idag (EMASMAX/greenEMAS) – status quo
2. Vilka incidenter (avåkningar) har skett – varje incident betyder nästan
   garanterat en kommande **ombeställning** (arresting-materialet förstörs
   vid en aktivering och måste bytas ut)
3. Vilka flygplatser är **på väg** att beställa EMAS – från Master Plans,
   ALP:ar, FAA:s AIP-bidragsdata och nyheter
4. Allt sammanvägt till en enkel signal: "detta kan påverka RW:s intäkter"

Detta är research för eget bruk (investeringsunderlag), inte ett
redaktionellt verktyg för ett team. Det är den viktigaste insikten som
ska styra hur enkelt allt annat blir.

---

## Vad som var fel med v1

Codex byggde en fyrstegs granskningspipeline (Observation → Verification →
Fact → Intelligence) med egna tabeller, formulär och sidor för varje steg,
plus en scraper med 9 egna felkoder för att ladda ner en fil. Det är
arkitektur för en nyhetsredaktion med flera analytiker som ska godkänna
varandras jobb – inte för en person som vill hålla koll på ett bolag.

**Regel för allt vi bygger nu: om du inte personligen kommer klicka dig
igenom ett godkännande-steg varje vecka, ska det inte finnas ett
godkännande-steg. En bool-flagga (`confirmed: true/false`) räcker.**

---

## Ny, enkel datamodell

Fem tabeller istället för tolv:

### `Airport`
id, iata_code, icao_code, name, city, state, country, lat, lon

### `Installation`
Vad som finns idag (EMASMAX/greenEMAS) – kommer från FAA:s Tableau-karta,
exakt den data vi redan extraherat manuellt i den här konversationen.

- id, airport_id, type (EMASMAX / greenEMAS), runway_end,
  install_year, source_id

### `Incident`
Avåkningar/aktiveringar – automatiskt en signal om kommande underhåll.

- id, airport_id, date, crew_and_passengers_saved, source_id,
  **implies_replacement: bool** (sätts automatiskt = true, ingen manuell
  granskning behövs – en aktivering betyder alltid att bädden ska bytas)

### `Signal`
Ersätter hela "Project + Observation + Verification + Fact +
Intelligence"-röran. Ett enda ställe för "det här kan bli en framtida
order".

- id, airport_id, title (fritext, t.ex. "Master Plan 2026 nämner RSA/EMAS
  förlängning"), source_id, category (new_construction /
  replacement_after_incident / maintenance / unknown),
  confidence (low / medium / high – sätts manuellt av dig, eller av en
  enkel regel, se nedan), target_year (kan vara null/gissning), notes

### `Source`
Länken/dokumentet något kommer ifrån – behövs för att du ska kunna lita på
datan, men det räcker med EN tabell, inte ett helt dokumenthanteringssystem.

- id, url, title, type (faa_tableau / master_plan / alp / aip_grant /
  news / other), retrieved_at

**Det är allt.** Ingen Alembic-historik behövs i det här skedet – kör
`create_all()` och exportera/importera JSON om du behöver flytta data.
Lägg till migrationer den dagen du faktiskt har produktionsdata att skydda.

---

## Enkla regler istället för en "intelligence-motor"

Du behöver ingen poängsättningsmotor. Två regler räcker för att börja:

1. **Incident → automatisk Signal.** Så fort en rad läggs till i
   `Incident`, skapa automatiskt en `Signal` med
   `category=replacement_after_incident`, `confidence=high`. Ingen
   mänsklig granskning krävs – det här är nästan alltid sant.
2. **Nyckelord i dokument → föreslagen Signal.** När du (eller ett skript)
   lägger in en PDF-länk (Master Plan/ALP/AIP-bidrag), sök efter "EMAS",
   "RSA", "runway safety area", "arresting system" i texten. Om träff:
   skapa en `Signal` med `confidence=low` som väntar på att du läser den
   och höjer till medium/high manuellt.

Så småningom kan ni bygga ut med fler regler, men börja här.

---

## Datakällor att bevaka (konkret)

1. **FAA:s EMAS-karta (Tableau)** – redan löst i den här konversationen.
   Återanvänd `sanitize_tableau_har.py`-logiken men förenkla bort
   enum-felkoderna. Detta ger dig `Installation` + `Incident`-tabellerna.
2. **FAA Airport Improvement Program (AIP) – bidragsdata.** FAA publicerar
   offentlig data över AIP-bidrag (federala anslag till flygplatser för
   just den typen av infrastrukturprojekt som RSA/EMAS-arbete). Ett
   bidrag taggat "runway safety area" eller "EMAS" är en stark
   framåtblickande signal – leta upp deras öppna dataportal.
3. **Airport Master Plans & ALP:ar (Airport Layout Plans).** Stora
   flygplatser publicerar dessa som PDF:er, ofta på den egna flygplatsens
   hemsida under "Planning" eller "Master Plan Update". Här nämns
   RSA-förbättringar och EMAS explicit när det är aktuellt.
   (OBS: i USA heter det AIP = Airport Improvement Program, inte samma sak
   som det internationella "Aeronautical Information Publication" som
   också förkortas AIP – lätt att blanda ihop, värt att hålla isär i
   modellen.)
4. **NTSB:s incidentdatabas** – kompletterar FAA:s karta med fler detaljer
   kring specifika avåkningar (flygplanstyp, orsak).
5. **Nyheter/pressmeddelanden** – RW själva, flygplatser, lokalpress kring
   byggstarter.

Du sa att du redan har länkar till en del PDF:er sparade – lägg in dem som
`Source`-rader manuellt till att börja med. Automatisk PDF-crawling av
enskilda flygplatsers hemsidor är ett bra steg 2, inte steg 1.

---

## Den tekniska sidan – hålla det enkelt

Behåll grundstacken (den är redan rätt för den här datamängden):

- **Backend/data:** Python, SQLite, SQLAlchemy – helt rimligt för
  hundratals rader.
- **Scraping:** befintlig FAA Tableau-logik, kraftigt förenklad
  (vanliga exceptions istället för enum-taxonomi).
- **Frontend:** byt bort standard-Bootstrap. Två alternativ:
  - **A (enklast, rekommenderas):** Generera en **statisk sida** från
    datan (ett script som exporterar till JSON/HTML) – ingen levande
    server behövs för att visa datan, bara för att uppdatera den. Snabb,
    billig att hosta (GitHub Pages/Netlify), och "snygg 2026-känsla" är
    lätt att uppnå med ett modernt CSS-ramverk (Tailwind) eller en
    handbyggd design.
  - **B:** Behåll FastAPI+Jinja men byt Bootstrap mot Tailwind och gör en
    genomtänkt design (kartvy + tabellvy + en enkel "senaste signaler"-
    flöde), snarare än admin-panel-look.

Jag rekommenderar **A** om sidan mest ska vara något du (och kanske andra)
läser, och **B** om du vill kunna lägga in nya `Signal`-rader direkt i
webbläsaren utan att röra databasen manuellt.

---

## Vad som ska bort ur nuvarande repo

- `app/models/observation.py`, `verification.py`, `fact.py`,
  `intelligence.py`, `finding_type.py`, `observation_type.py` →
  ersätts av `signal.py`
- `app/repositories/*` för samma → bort
- `app/services/fact_promotion.py`,
  `intelligence_derivation.py`, `intelligence_rule_evaluation.py`,
  `observation_candidates.py` → ersätts av de två enkla reglerna ovan
- `app/templates/observations/*`, `verifications/*`, `facts/*`,
  `intelligence/*` → ersätts av en enda `signals`-vy
- Enum-felkodstaxonomin i `faa_tableau.py` → vanliga exceptions
- De ~15 testfilerna som testar pipelinen → skriv nya, färre tester för
  den enkla modellen istället

## Vad som ska behållas (as-is eller nästan)

- `app/models/airport.py`, `emas_bed.py` (byt namn till `installation.py`
  för tydlighet), `incident.py`
- `app/acquisition/faa_tableau.py` + `sanitize_tableau_har.py` (förenklas,
  inte kastas)
- `app/database.py`, `app/config.py`

---

## Föreslagen ordning att göra det i (för Claude Code)

1. Skapa den nya, enkla modellen (`Airport`, `Installation`, `Incident`,
   `Signal`, `Source`) i en ny gren.
2. Skriv en migreringsscript som flyttar existerande data (om något redan
   är sparat) från gamla modellen till nya.
3. Ta bort observation/verification/fact/intelligence-lagren helt.
4. Förenkla `faa_tableau.py`-felhanteringen.
5. Bygg om frontend (rekommendation: statisk export + snygg design,
   alternativ B om du vill ha admin-inmatning).
6. Lägg till de två enkla reglerna (incident→signal,
   nyckelord-i-dokument→signal).
7. Lägg in dina redan sparade PDF-länkar som `Source`-rader manuellt för
   att se att flödet fungerar end-to-end.
8. Först därefter: fundera på automatisk PDF-crawling, mer avancerade
   regler, etc – som separata, avgränsade steg.

---

## Internationellt (senare, men bra att modellen klarar det redan nu)

Utöver USA finns redan ett par flygplatser på radarn: Zürich, en i
Madagaskar, en i UK och en i Nya Zeeland. Datamodellen ovan kräver inga
ändringar för detta – `Airport.country` finns redan, och `Source.type`
är generisk. Det som skiljer sig är källorna, som blir landsspecifika:

- **Zürich (Schweiz):** BAZL / flygplatsens utbyggnadsdokument
- **Madagaskar:** ACM eller ICAO:s landsprofiler (troligen tunt med
  offentlig info – räkna med mest manuell research)
- **UK:** CAA + flygplatsers "Airport Development Plan"
- **Nya Zeeland:** CAA NZ + flygplatsers "Asset Management Plan"

Praktiskt: varje land blir en ny `Source.type` (t.ex. `uk_caa`,
`nz_caa`) och en egen liten inmatnings-/skrapningsrutin när det är dags
– ingen omdesign behövs. Bygg klart USA-flödet först, lägg till länder
ett i taget.

## Nya konkreta källor (hittade och verifierade 2026-07-21)

### FAA AIP Grants (https://www.faa.gov/airports/aip/2026_aip_grants)
Fyra PDF:er per år ("announcements"), enkel tabell: delstat, stad,
flygplats, Loc ID, projektbeskrivning, belopp. **Redan verifierat en
träff:** Binghamton (BGM) fick 2026 ett bidrag för "Reconstruct
Engineered Material Arresting System Safety Area" – en solklar
`confidence=high`-signal utan tolkning. Källa för `Source.type =
aip_grant`. Bevaka: samma URL-mönster år för år
(`.../airports/aip/{ÅR}_aip_grants`), fyra nya PDF:er per år att
parsa/söka igenom med nyckelordsregeln.

### FAA IIJA Grants (https://www.faa.gov/iija/iija-airport-infrastructure-grant-funding-amounts/) — byggt 2026-07-25
IIJA (Infrastructure Investment and Jobs Act) är en egen, öronmärkt
finansieringspott, separat från den vanliga AIP-potten ovan. Samma
"Announcement"-PDF-tabellformat som AIP, så `app/acquisition/faa_iija_grants.py`
återanvänder `parse_grant_pdf` från AIP-parsern rakt av snarare än att
återimplementera en tabellparser. Sex PDF:er per budgetår på en
förutsägbar URL (`AIG-FY{ÅR}-A{1-6}.pdf`) – ingen HTML-listsida att skrapa,
till skillnad från AIP. Källa för `Source.source_type = iija_grant` (skilt
från `aip_grant`/`usaspending_grant` – samma bidrag kan i praktiken synas i
flera pottar/rapporteringsvägar samtidigt, se dedup-resonemanget under
USAspending nedan).

`scripts/import_faa_iija_grants.py` är den återkommande importskriptet
(samma mönster som `import_faa_aip_grants.py`, men aktiv, inte vilande).
`scripts/add_iija_fy2026_known_grants.py` är en engångskörning mot tre
redan verifierade FY2026-träffar (MHT, BOS, MMU) som alla matchade
befintliga USAspending-signaler nästan ord för ord – i stället för att
skapa dubblettsignaler skapas en fristående `iija_grant`-Source per fynd
(för proveniens/dedup) och en daterad notering läggs till på den befintliga
signalen; `Signal.source_id` lämnas oförändrat (en Signal har bara en källa,
och USAspending-källans PURPOSE-text är värd att behålla som primärkälla).
Samma engångsskript flaggar också explicit, på CLT:s befintliga Runway
Safe-EMAS-signal, att CLT separat har ett IIJA-bidrag för "Expand/Reconstruct
Apron" – asfaltsyta, helt orelaterat till EMAS-ordern – för att förhindra att
en framtida automatisk import blandar ihop de två.

### FAA Runway Ends Table (catalog.data.gov, del av NTAD) — RSA-regeln, avbruten 2026-07-22
Planen antog att denna databas innehåller Runway Safety Area (RSA)-mått
per bana. **Det gör den inte.** Undersökt grundligt innan något byggdes:

- ArcGIS REST-tjänsterna ägda av `USDOT_BTS` (`Runway_Ends_Table`,
  `Runways_View`, `NTAD_Aviation_Facilities`) genomsöktes fält-för-fält
  (samtliga ~70+34+91 fält) efter "RSA"/"safety" – inga träffar.
- Samma sak i den råa källan: FAA:s officiella NASR-abonnemang
  (`https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/`,
  `{cykel}/extra/{datum}_APT_CSV.zip`) innehåller `APT_RWY.csv` och
  `APT_RWY_END.csv` med exakt samma fält som ArcGIS-tjänsten (ArcGIS-lagret
  är uttryckligen härlett från NASR). `APT_CSV_DATA_STRUCTURE.csv`
  (fullständig kolumnlista för hela NASR-paketet) genomsöktes också –
  inga RSA/safety-fält någonstans.

RSA-compliance-status verkar bara finnas i FAA:s interna system eller
nämnas narrativt i Master Plans/ALP:ar/NTSB-rapporter, inte som ett
queryabart öppet fält. **Regel 4 (RSA under standardmått →
`potential_new_construction`-signal) är därför inte byggbar med öppen
data just nu och är avskriven**, snarare än ersatt med en svagare proxy
(t.ex. deklarerade distanser) som riskerar falskt förtroende i
confidence-nivåerna. Om en pålitlig RSA-källa dyker upp senare (FOIA,
ADIP-export, ny NTAD-tabell) kan regeln återupptas.

### Bonusfynd: APT_ARS.csv (Arresting System) i samma NASR-paket
Samma nedladdning innehåller en tabell som *är* direkt användbar:
`ARPT_ID, RWY_ID, RWY_END_ID, ARREST_DEVICE_CODE` – vilken bana/ände som
har vilken bromsutrustning, med `EMAS` som ett eget kodvärde (skilt från
äldre kabelsystem som BAK-12/MA-1A). Detta är en mer precis och
auktoritativ källa än att gissa bandsände via koordinatnärhet, och
används av `scripts/import_faa_runway_ends.py` för att berika
`Installation.runway_end`/`runway_id` (del 1 av ursprungsplanen för
regel 4 – berikningen, inte RSA-signalen).

### USAspending.gov (utforskat 2026-07-22, byggt 2026-07-22)

Testat live via `scripts/explore_usaspending.py` (skriver ingenting till
databasen, bara utforskning) innan något byggs på riktigt. Officiellt,
publikt API på `api.usaspending.gov`, **ingen API-nyckel krävs**.

**Vad som fungerar:** Sökning på exakt frasen "Engineered Material
Arresting System" mot **GRANTS-typerna** (`award_type_codes:
["02","03","04","05"]`) – inte CONTRACTS – ger extremt specifik,
individuellt användbar data per flygplats/bana/ände:

- Broome County (Binghamton/BGM) – flera bidrag 2021–2026 för
  Runway 16/34 EMAS, fas för fas (design, block-upphandling,
  konstruktion) – **matchar exakt** vår redan importerade AIP-grant-data
  för BGM.
- Manchester, City of (MHT) – $7 937 100, Runway 6/24 EMAS-rekonstruktion,
  2026-06-15 – **matchar vår befintliga MHT-signal.**
- Massachusetts Port Authority (Boston/BOS, flera bidrag 2024–2025),
  Barnstable Municipal (Hyannis/HYA) – båda redan i vår databas.
- Nya, ännu ej bevakade träffar: Roanoke Regional Airport Commission,
  Greenville Airport Commission, Allegheny County Airport Authority
  (Pittsburgh), City of Philadelphia, Town of Morristown, Michigan DOT –
  konkreta belopp, datum, bana och ände i klartext, ingen tolkning
  behövs.
- Data går tillbaka till 2007-10-01 (mycket längre historik än
  AIP-grant-PDF:erna som bara täcker innevarande år).

**Vad som INTE fungerar:**

- CONTRACTS-sökning (samma fras, `award_type_codes: ["A","B","C","D"]`)
  ger **noll träffar** – EMAS-upphandlingar går inte via federala
  kontrakt, bara via bidrag till flygplatsägaren.
- `"Runway Safe"` som mottagarnamn: **noll träffar** i
  `/api/v2/autocomplete/recipient/`. Leverantören själv syns aldrig i
  USAspending – bidraget går till flygplatsens ägare (county/city/
  airport commission/port authority), som sedan privat upphandlar EMAS
  av Runway Safe. Går alltså inte att spåra leverantör den här vägen.
- Bara nyckelordet `"EMAS"` (utan kontext): för brett/tvetydigt, matchar
  bl.a. "EMAIL" i beskrivningar (t.ex. "MFE EMAIL SECURITY"). Måste
  använda hela frasen eller "arresting system".
- `"arresting system"` utan att filtrera på GRANTS specifikt blandar in
  mycket militärt brus (Air National Guard-baser som bygger om
  kabelbaserade arresting-system – annan teknik, annan kund).

**Bedömning:** Datan är tillräckligt specifik (flygplats + bana + ände +
belopp + fas, i klartext) för att vara värd en riktig integration – i
praktiken ett alternativ eller komplement till att parsa AIP-grant-PDF:er,
med längre historik och strukturerad JSON istället för PDF-tabeller.

**Byggt 2026-07-22:** `app/acquisition/usaspending_grants.py` (hämtning,
paginerad, `EARLIEST_SUPPORTED_DATE = "2007-10-01"`) +
`scripts/import_usaspending_grants.py` (matchning mot `Airport` via
FAA/ICAO/IATA-kod i beskrivningens Loc ID-parentes eller via
"INTENDED BENEFICIARY"-meningens ort/delstat, `Signal(confidence="high")`
per träff). Dedup mellan USAspending och den äldre AIP-grant-PDF-parsern
löst via en ny unik kolumn `Source.external_id`
(`uq_sources_external_id`-index) – varje importväg kollar mot den innan
en ny `Source`-rad skapas, istället för en skör belopp/år-heuristik som
konstaterades ge falska positiva mot verklig data (se
`scripts/import_faa_aip_grants.py`, som gjordes vilande/fallback snarare
än borttagen – dess modul-docstring förklarar varför).

Körning mot skarp data gav 6 nya flygplatser (Roanoke, Greenville,
Allegheny County, Philadelphia, Morristown, Michigan DOT) plus nya
high-confidence-signaler för dem, utan en enda dubblett mot BGM/MHT/BOS/
HYA som redan fanns (i praktiken hade `import_faa_aip_grants.py` aldrig
körts mot produktionsdatabasen tidigare, så det fanns inga verkliga
dubbletter att reconcilea – bara en risk att skydda mot framåt).

### FAA Airport Construction Impact Report (utforskat och byggt 2026-07-22)

Kvartalsvis PDF-rapport från FAA:s ATO System Operations-avdelning,
listar pågående/planerade banarbeten per flygplats med exakta
start/slut-datum. Tredje källan (efter AIP-grant-PDF och USAspending)
som bekräftar samma verklighet, men den enda som ger **exakta
byggnadsdatum** snarare än bidragsbelopp/planeringsår.

**Vad som INTE stämde med uppgiftens antagande:** URL-mönstret
`Q{kvartal}_{år}_508_Airport_Construction_Impact_Report.pdf` är **inte
stabilt** – verifierat live mot indexsidan. Underkatalogen varierar
(`sys_cap_eval/`, `sys_cap_eval/media/`,
`slot_administration/data/doc/`), "508" saknas ibland, och minst en fil
har stavfelet "Constuction". En hopkodad URL-mall hade alltså missat
flera verkliga rapporter. Löst genom att `discover_latest_report()`
hämtar indexsidans HTML och letar upp de faktiska `href`-länkarna med
en regex på `Q(\d)_(\d{4})`, sorterar på (år, kvartal) och väljer högst
– ingen mall, ingen gissning.

**PDF-parsning:** `extract_text()` (rå textextraktion) ger jumblade,
sammanflätade rader för den här typen av flerkolumns Gantt-liknande
tabell (verifierat konkret på JFK-sektionen: datum som
"04/13/2026 to 2026 TBD" i fel ordning). `pdfplumber.extract_tables()`
återskapar däremot det riktiga rutnätet (Project ID, Project Name,
Description of Work, Estimated Dates, Status, Impact, Notes) pålitligt.
All parsning bygger på `extract_tables()`, med tabellen identifierad via
sin rubrikrad ("Description of Work"), inte via tabellindex.

**Flera kandidat-signaler per flygplats – BOS som konkret exempel:**
Innan kod skrevs visade en kontroll av produktionsdatabasen att BOS
redan hade **fyra** EMAS-omnämnande signaler (ursprunglig seed-signal +
tre separata USAspending-bidrag för samma runway 9/27-EMAS i olika
faser). Ett naivt "uppdatera första träffen"-antagande hade uppdaterat
fel signal icke-deterministiskt. Löst med en explicit poängheuristik i
`_candidate_score()`: +2 om `planning_year` matchar rapportens
startårtal, +1 om samma banummer nämns, +1 om samma "PHASE {n}"-text
finns i båda. Vid oavgjort resultat (ingen entydig vinnare) skapas en ny
signal istället för att gissa. Verifierad mot verklig BOS-data innan
den skrevs in i modulen: seed-signalen fick poäng 4, närmaste
USAspending-konkurrenten fick 3 – en tydlig, förklarbar vinnare.

**Bekräftat mot skarp data:** BOS "RWY 27 RSA (Phase 2)",
2026-08-31 → 2026-11-15, "second part of the RWY 9 EMAS installation"
matchade och uppdaterade den redan existerande BOS-signalen (id=3,
seed-signalen) – status satt till `"under construction"`,
`construction_start`/`completion_date` satta till de exakta datumen,
en bekräftelsenot bifogad till befintliga anteckningar (skriver inte
över). De tre andra BOS-signalerna lämnades helt orörda. SFO-sektionens
"RWY 1R/19L Rehabilitation and TWY W" (EMAS seam replacement,
2026-03-30 → 2026-10-03) hade redan en egen signal från tidigare
körningar och uppdaterades likadant istället för att skapa en dubblett.
Andra körningen mot samma rapport gav `already_imported: 2,
signals_updated: 0, signals_created: 0` – bekräftat idempotent via
samma `Source.external_id`-mönster
(`faa_construction_report:Q{kvartal}_{år}:{flygplatskod}:{project_id}`).

**Bedömning:** Byggd och klar. `app/acquisition/faa_construction_report.py`
+ `scripts/import_faa_construction_report.py`, körd mot skarp data.

## En sak till

Du har redan (via den här konversationen) en fungerande metod för att
hämta hela FAA-kartans installations- och incidentdata utan att klicka
manuellt (Tableau bootstrap-parsing). Den logiken är i praktiken en
nästan komplett ersättning för `faa_tableau.py` i sitt nuvarande skick –
ge gärna den koden till Claude Code som referens/startpunkt istället för
att låta den återuppfinna hjulet.

## Kvarstående, ej brådskande

### FastAPI-devserverns luckor (upptäckt 2026-07-25)
Devservern (`app/templates/airports/detail.html`) visar fortfarande
inte installationer på flygplatssidan alls — funktionen finns bara i
den statiska exporten (`app/static_export/`), som är den faktiska
produkten. Ingen brådska att fixa eftersom devservern bara är ett
internt utvecklingsverktyg, inte det som faktiskt driftsätts. Bra att
komma ihåg om devservern någon gång känns "trasig" jämfört med den
statiska sajten — det är väntat, inte en bugg.

### Framtida idé: n8n + AI-driven bevakning
Istället för att jag (användaren) manuellt googlar/läser PDF:er för
att hitta nya Master Plans/AIP/CIP-beslut, kan n8n (eller liknande
verktyg) bevaka kända källor och använda AI för att sammanfatta nya
dokument automatiskt. Viktigt designval om/när detta byggs: AI-
sammanfattningar ska gå till en "väntar på granskning"-status, INTE
skrivas direkt till `notes` som en bekräftad signal — jag ska
fortfarande bedöma relevans/tolkning själv, AI:n ska bara hitta och
sammanfatta, inte avgöra. Matcha/skapa flygplatser automatiskt
(samma mönster som USAspending-importen redan gör) om en ny,
okänd flygplats dyker upp. Inte påbörjat — kräver mer eftertanke om
vilka källor, hur ofta, och exakt vilket granskningsflöde som behövs
innan det byggs.
