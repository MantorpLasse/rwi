# Utredning: alla flygplatser med `faa_tableau` som källa

Ren research – **ingen fil är ändrad, ingen kod körd mot databasen, ingen
commit gjord.** Enda output är denna rapport, som instruerat.

## Omfattning och metod

`Source.source_type = "faa_tableau"` finns bara som **en** Source-rad i
databasen (id 12: "FAA EMAS Incidents and Installations map (verified CSV
export)"), som ursprungligen sattes som källa på en generisk
Installation-rad per flygplats (ingen `install_year`, bara "FAA map region:
Map - Main" i notes) när FAA:s kartdata importerades. **69 flygplatser**
har fortfarande en Installation-rad som pekar på denna källa.

Av dessa 69 har **61 redan fått en andra, daterad Installation-rad** från
tidigare sessioners arbete (FAA Fact Sheet 2011/2016-importen, Gadelius
greenEMAS-listan, PRWeb-pressmeddelandet om Chicago) – den generiska
`faa_tableau`-raden ligger kvar orörd som en lokaliseringskälla, men
frågan "vilket år?" är redan besvarad på en separat rad. Dessa 61
listas kompakt nedan (korsrefererade mot tidigare sessioners
research, inte omsökta på nytt denna gång – se Metodbegränsningar).

**8 flygplatser saknar fortfarande all datering** och fick genomgången
denna uppgift bad om: sökt på internet, källor kontrollerade. Fyra av
dem (PHL, PDK, BCT, VNC) visade sig redan ha gedigen research liggande i
tidigare utredningsdokument som aldrig skrevs in i `install_year` – inget
nytt sökbehov, bara ett dataglapp att flagga. Tre (**OXC, CGF, HXD**) hade
aldrig undersökts alls i någon tidigare session – helt ny research gjord
här.

---

## De 8 icke-daterade flygplatserna

### OXC — Waterbury-Oxford Airport, Connecticut (NY)

**Ny research.** EMAS installerades vintern 2017/2018 – arbetet pågick
"end of November 2017 to beginning of March 2018", utfört i sektioner
under nattskift för att minimera störningar. En sammanställningsartikel
(SimpleFlying, "EMAS: 5 Things To Know...") daterar installationen till
**2018**. Ingen kostnad, leverantör eller banände hittades i denna
sökning – CT Airport Authority's egen Airport Master Plan Update
(`ctairports.org/wp-content/uploads/2017/05/finalAMPU.pdf`) kunde inte
hämtas (HTTP 403, blockerar automatisk hämtning) och skulle vara nästa
steg för en starkare primärkälla. **Konfidens: Medel** (aggregator +
en tidslinjebeskrivning, ingen direkt FAA/nyhetskälla med kostnad).

**Förslag:** `install_year=2018`, `type=EMASMAX`, källa = ny Source
(`source_type=news`, SimpleFlying-artikeln) tills en starkare källa
hittas.

### CGF — Cuyahoga County Airport, Richmond Heights, Ohio

**Ny research – stark, detaljerad källa hittad.** Del av ett $39M
"Runway Safety Area Improvement Project" (start 2016, hela projektet
firat klart nov 2020 enligt Cuyahoga County:s eget pressmeddelande).
EMAS-delen specifikt (Faserna 3 och 4) **färdigställdes 2018**: två
bäddar, en vid varje banände av bana 6/24 – bädden vid bana 6:s
avgångsände är **322 fot**, bädden vid bana 24:s avgångsände är **435
fot**. Samma faser inkluderade en 511-fots förlängning av bana 6,
förlängning av taxibana "A", nya inflygningsljus och ett nytt AWOS.
Finansierat via FAA- och ODOT (Ohio DOT Office of Aviation)-bidrag plus
countyfinansiering. Källor: [Cuyahoga County pressmeddelande](https://cuyahogacounty.gov/executive/news/press-releases-archive/2019-press-releases/2020/11/10/cuyahoga-county-airport-celebrates-completion-of-$39-million-runway-safety-area-improvement-project),
sökmotorsammanfattning som citerar countyts egen "Runway 6/24 Safety
Area Improvement Program"-översikt
(richmondheightsohio.org/DocumentCenter/View/255). **Konfidens: Hög**
för årtal 2018 och bäddängder, **Medel** för exakt kostnadsuppdelning
(totalsumman $39M gäller hela det fyrfasiga projektet, inte bara
EMAS-delen).

**Förslag:** `install_year=2018`, `type=EMASMAX`, `runway_end` kan
sättas separat för `06` (322 ft) och `24` (435 ft) om en two-row-modell
önskas (jfr STP/MKC-mönstret för "båda ändar"), källa = ny Source
(`source_type=news`, Cuyahoga County pressmeddelandet).

### HXD — Hilton Head Airport, South Carolina

**Ny research.** Två EMAS-bäddar (200 fot vardera), en vid varje
banände, del av ett $8M säkerhetsprojekt: 90% FAA, 5% South Carolina
Aeronautics Commission, resten flygplatsens egna intäkter. Projektet tog
ca 18 månader och **avslutades i slutet av juni 2018**, enligt Airport
Improvement Magazine (branschartikel, samma typ av källa som löste
LEX-frågan i en tidigare session). Huvudentreprenör: Quality Enterprises
USA (specifik EMAS-leverantör inte namngiven i artikeln). **En annan,
svagare källa** (aggregator) anger istället "installerat 2019" – en
diskrepans värd att notera; Airport Improvement-artikelns specifika
"slutet av juni 2018" väger tyngre här (branschpublikation med konkret
tidslinje mot en generisk lista utan källhänvisning). Enligt nuvarande
FAA-data (samma karta som redan är kopplad i databasen) sitter
bäddarna vid banände 21 (211×105 fot) och 03 (207×105 fot). **Konfidens:
Hög** för 2018 och kostnad/finansiering, **Låg** för den motstridiga
2019-uppgiften (inte verifierad mot primärkälla).

**Förslag:** `install_year=2018`, `type=EMASMAX`, `confirmed_vendor`
lämnas tomt (leverantör ej namngiven), källa = ny Source
(`source_type=news`, Airport Improvement-artikeln), notera
2019-diskrepansen i notes.

### PHL — Philadelphia International

**Inget nytt sökbehov – ren dataglapps-flagga.** Redan mycket väl
researchat i `docs/utreding_status_flygplatser.md` (Hög konfidens):
PHL:s första EMAS någonsin, klart 12 juni 2025, öster om bana 8-26,
~2000 EMASMAX-block, byggt av Runway Safe, 8,5 MUSD federalt
finansierat (källor: PHL Airport, 6abc, AirlineGeeks). Det finns även en
matchande USAspending-signal (id 43, $8,5M, FY2024). **Men
`install_year` är aldrig satt på Installation-raden** – bara den
generiska `faa_tableau`-posten finns. Ren backfill-fråga, ingen ny
research behövs.

**Förslag:** `install_year=2025`, `confirmed_vendor="Runway Safe"`,
ny Source (en av de redan citerade artiklarna) på en ny, separat rad –
samma mönster som alla andra fact-sheet-importer.

### PDK — DeKalb-Peachtree

**Inget nytt sökbehov.** Redan känt (Hög konfidens): klart dec 2018, "),
1 746 block, 8 MUSD-projekt, Georgias första EMAS (källor: Airport
Improvement magazine, AJC, DeKalb County). En matchande
incident-signal finns (id 22). 2016 Fact Sheet nämnde bara "expected
2016" (en tidigare, mer optimistisk prognos som inte slog in exakt) –
redan hanterat med en notering i `docs/utredning_faa_factsheet_resten.md`,
utan hårt `install_year`.

**Förslag:** `install_year=2018`, `confirmed_vendor` lämnas tomt om
leverantör inte är explicit namngiven i de redan citerade källorna
(kontrollera Airport Improvement-artikeln igen om det behövs).

### BCT — Boca Raton

**Inget nytt sökbehov.** Redan känt (Hög konfidens): "efter 2012",
matchar sept 2025-incidenten. 2016 Fact Sheet gav "expected 2016" som
den mest specifika dateringen hittills, redan noterad (utan hårt
`install_year`) i `docs/utredning_faa_factsheet_resten.md`. Boca Raton
Airports egen sida (`bocaairport.com/portfolio-items/engineered-materials-arresting-system-emas/`)
dök upp i denna sessions sökningar men hanns inte läsas i detalj –
**nästa steg** för att bekräfta ett hårt årtal.

**Förslag:** besök `bocaairport.com`s egen EMAS-sida (hittad, ej läst)
innan `install_year` sätts definitivt.

### VNC — Venice, Florida

**Inget nytt sökbehov, men en tidigare felaktig premiss rättad.**
`docs/utreding_status_flygplatser.md` antog att VNC saknade
Installation/Signal helt – det stämmer inte (den generiska
`faa_tableau`-posten fanns redan då). Oberoende bekräftelse finns sedan
tidigare (Local10 News, feb 2026, "sex Florida-flygplatser med EMAS").
2016 Fact Sheet gav bara "expected 2016" (redan noterat, ingen hård
`install_year`). Inget nytt sökt denna gång utöver vad som redan
konstaterats i `docs/utredning_faa_factsheet_resten.md`.

**Förslag:** samma som BCT – leta efter en flygplatsspecifik källa
(lokala Venice/Sarasota County-nyheter) för ett hårt installationsår
innan `install_year` sätts.

### VPC — Cartersville (bonusgenomgång, redan tidigare nedgraderad)

Inte en del av de 8 "helt icke-daterade" – har redan en not
(nedgraderad till Låg konfidens för "båda ändar", se
`docs/utredning_svaga_poster.md`). Ingen ny sökning gjord denna gång;
nämns bara för fullständighet eftersom `faa_tableau` fortfarande är dess
enda Installation-källa.

---

## De 61 redan daterade flygplatserna (korsreferens, ej nysökt)

Kontrollerade mot befintliga Installation-rader – varje kod nedan har
redan en andra, daterad rad (källa = FAA Fact Sheet 2011/2016, Gadelius
eller PRWeb, se respektive tidigare `docs/utredning_*.md`). Listas här
bara för att bekräfta att `faa_tableau`-genomgången är komplett; ingen
ny research gjord.

MHT(2007) · BOS(2005) · SFO(2014) · BGM(2002) · STP(2008) · JFK(1996) ·
HYA(2003) · MKC(2009) · MDW(2006 EMASMAX + 2014 greenEMAS) · TEB(2006) ·
ADQ(2015) · CDV(2007) · OME(2015) · LIT(2000) · ACV(2010) · BUR(2002) ·
MRY(2015) · OAK(2015) · SAN(2006) · SBP(2008) · TEX(2010) · GON(2011) ·
BDR(2015) · ILG(2010) · FLL(2004) · EYW(2010) · SUA(2011) · PBI(2011) ·
PWK(2014) · ORD(2008 EMASMAX + separat greenEMAS-rad) · SDF(2015,
"Sandiford" – se identitetsanmärkning i `docs/utredning_faa_factsheet_resten.md`) ·
BTR(2002) · LFT(2011) · AUG(2011) · ORH(2008) · MSP(1999) · EWR(2008) ·
TTN(2012) · ROC(2001) · LGA(2005) · FRG(2011) · CLT(2008) · EWN(2012) ·
INT(2010) · BKL(2013) · CLE(2011) · ABE(2015) · RDG(2009) · AVP(2008) ·
PVD(2014) · GMU(2003) · JWN(2015) · MEM(2013) · ADS(2014) · LRD(2006) ·
MFE(2015) · RUT(2015) · DCA(2014) · ROA(2004) · CRW(2007).

**ELM** är ett specialfall: `install_year=2012` sitter direkt på den
*samma* raden som pekar på `faa_tableau` (source_id oförändrad, se
`docs/utredning_faa_factsheet_2011_2016.md`) – inte en separat rad som
övriga. Nämns här för fullständighet.

---

## Sammanfattande förslagslista

| Kod | Föreslaget install_year | Konfidens | Ny källa behövs? |
|---|---|---|---|
| OXC | 2018 | Medel | Ja – starkare primärkälla än SimpleFlying vore bra |
| CGF | 2018 | Hög | Nej – Cuyahoga Countys pressmeddelande räcker |
| HXD | 2018 | Hög (2019-uppgift Låg/motstridig) | Nej – Airport Improvement räcker |
| PHL | 2025 | Hög | Nej – redan citerat i tidigare utredning |
| PDK | 2018 | Hög | Nej – redan citerat i tidigare utredning |
| BCT | – | Medel | Ja – bocaairport.com:s egen EMAS-sida (hittad, ej läst) |
| VNC | – | Medel | Ja – lokal Venice/Sarasota-nyhetskälla |
| VPC | (2021, oförändrad) | Låg för "båda ändar" | Redan flaggat i tidigare utredning |

Ingen av dessa ändringar är genomförda i denna session – bara
förslag, som instruerat.

## Metodbegränsningar

- De 61 redan daterade flygplatserna är **korsreferenser mot tidigare
  sessioners arbete**, inte nyomsökta denna gång. Om du vill ha en
  fullständig, oberoende omverifiering av alla 61 (inte bara de 8
  luckorna) är det ett betydligt större jobb som inte rymdes inom denna
  uppgifts "kolla alla" om det tolkas som "sök om alla från grunden".
- CT Airport Authority's Master Plan Update-PDF (OXC) gick inte att
  hämta (HTTP 403) – skulle kunna ge ett starkare, mer detaljerat
  underlag.
- Cuyahoga Countys egen FAQ-sida om projektet kunde hämtas men innehöll
  inget textmässigt om EMAS specifikt (troligen JS-renderat innehåll
  som inte syns i den statiska HTML:en) – informationen om CGF kommer
  istället från sökmotorns sammanfattning av flera sidor plus det
  ursprungliga pressmeddelandet.
- HXD:s 2018-vs-2019-diskrepans är inte slutgiltigt löst – ingen
  primärkälla för "2019" hittades att jämföra direkt mot.
- Sökningen är ett ögonblicksfynd (2026-07-26/27).
