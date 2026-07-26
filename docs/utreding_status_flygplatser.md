# Utredning: EMAS-status för alla flygplatser i signal-vyn

Ren research – ingen kod, mall eller databas har ändrats. Detta är en
oberoende webbkontroll (engelska + lokalspråk vid behov) av samtliga 39
flygplatser som förekommer i signal-vyn (`signals`-tabellen), för att
verifiera vad som faktiskt är sant om EMAS-status i verkligheten just nu,
oavsett vad vår databas råkar innehålla.

**Metod:** 4 parallella research-pass (webbsökning), ett per ~10 flygplatser.
Varje flygplats kontrollerades mot: FAA-källor, flygplatsens egen webbplats,
nyhetsmedia, tillverkare (Runway Safe/ESCO/Zodiac m.fl.), Wikipedia/SKYbrary/
NTSB där relevant. Konfidensnivå (Hög/Medel/Låg) anger hur väl oberoende
källor stämmer överens – inte hur säker databasen är.

**Viktigt om jämförelsen mot vår databas:** kolumnen "Jfr databas" är en
ren observation, inte en ändring – inget i databasen är rört. "✓" betyder
att webbfyndet stämmer med vad signalerna redan antyder; "⚠" flaggar något
värt att dubbelkolla manuellt.

---

## Snabböversikt

| Kod | Flygplats | Land | Har EMAS nu? | Bana/år | Konfidens | Jfr databas |
|---|---|---|---|---|---|---|
| SDU | Santos Dumont | Brasilien | **Nej** – planerat, mål H2 2026 | – | Medel | ✓ Signal säger redan "planerad" |
| WLG | Wellington | Nya Zeeland | **Ja** – klart mars 2026 | Båda ändar 16/34 | Hög | ✓ Matchar vår graduering till Installation |
| CLT | Charlotte Douglas | USA | **Ja** – ersätts 2024-25 | 36R | Hög | ✓ Signal = ersättningsorder |
| MSP | Minneapolis-St Paul | USA | **Ja** | 12R | Medel | ✓ |
| PHL | Philadelphia | USA | **Ja** – nytt, klart juni 2025 | 8-26 (öst) | Hög | ✓ USAspending-bidrag FY2024 matchar |
| JFK | JFK | USA | **Ja** – sedan 1996 (första i världen) | 4R/22L | Hög | ✓ 3 incident-signaler + ersättningsprojekt |
| LGA | LaGuardia | USA | **Ja** | 13, 22 (alla banor) | Hög | ✓ Okt 2016 Pence-incident matchar exakt |
| BOS | Boston Logan | USA | **Ja** – 3 system, ett under byggnation | 22R, 33L/15R, 27 (nytt) | Hög | ✓ |
| MDW | Chicago Midway | USA | **Ja** – sedan 2007 + greenEMAS 2014-16 | 13C/31C + fler | Hög | ✓ (signalen är separat, framåtblickande) |
| ORD | Chicago O'Hare | USA | **Ja** – ~2016-2017 | 04R, 22L | Medel-Hög | ✓ |
| SFO | San Francisco | USA | **Ja** – sedan 2014 | 1L/1R/19L/19R (4 bäddar) | Hög | ✓ |
| TEB | Teterboro | USA | **Ja** – sedan 2006 | 06, 19, 24 (3 bäddar) | Hög | ✓ Apr 2026-incident matchar nyhetsartikel exakt |
| MHT | Manchester-Boston | USA | **Ja** – ersätts just nu (mitten 2026) | 6-24 | Hög | ✓ Exakt match med vår ersättningssignal |
| HYA | Cape Cod Gateway | USA | **Ja** – nyligen ersatt | 06-24 | Hög | ⚠ Se anmärkning nedan – ev. inaktuell status |
| BGM | Greater Binghamton | USA | **Ja** | 16, 34 | Hög | ✓ |
| STP | St Paul Downtown | USA | **Ja** | 14, 32 (båda ändar) | Hög | ✓ Exakt match ("replacement at both ends") |
| MKC | Charles B. Wheeler | USA | **Ja** | 01, 19 (båda ändar) | Hög | ✓ |
| FTY | Fulton County Executive | USA | **Nej** – planerat (EA aug 2026) | 08/26 | Medel | ✓ Exakt match ("safety improvements", status=design) |
| ASE | Aspen/Pitkin County | USA | **Nej** – ej planerat enligt FAA | – | Hög | ⚠ Se anmärkning nedan – möjlig konflikt |
| CRQ | McClellan-Palomar | USA | **Nej** – planerat sedan 2021, ej byggt | Båda ändar (planerat) | Medel | ✓ |
| PWK | Chicago Executive | USA | **Ja** – sedan 2012-2015 | 16, 34 (båda ändar) | Hög | ✓ Sept 2025-incident matchar exakt |
| FLL | Fort Lauderdale-Hollywood | USA | **Ja** – sedan 2004, skadad/ersatt 2023-25 | Norra banan, båda ändar | Hög | ✓ April 2023-incident = översvämningsskadan |
| PBI | Palm Beach Intl (DB: "DJT") | USA | **Ja** – sedan ≤2011 | 14 | Medel-Hög | ✓ |
| BUR | Hollywood Burbank (Bob Hope) | USA | **Ja** – sedan 2002 | 8 (västra änden) | Hög | ✓ 2018-signalen matchar Southwest 278 exakt |
| BCT | Boca Raton | USA | **Ja** – efter 2012 | 05, 23 (båda ändar) | Hög | ✓ Sept 2025-incident matchar exakt |
| EYW | Key West Intl | USA | **Ja** – sedan ≤2011 | Båda ändar 9/27 | Hög | ✓ Nov 2011-incident matchar exakt |
| SUA | Witham Field | USA | **Ja** (klart mellan 2018-2026) | 12, 30 (båda ändar) | Medel | ✓ |
| TEX | Telluride Regional | USA | **Ja** – sedan 2009-2010 | Båda ändar 9/27 | Hög | ✓ |
| LEX | Blue Grass | USA | **Ja** (installationsår okänt) | Båda ändar 4/22 | Medel | ✓ |
| ROA | Roanoke-Blacksburg | USA | **Ja** – ersatt 2024; 6/24 planerat men ej byggt | 16/34 (byggt), 6/24 (ej byggt) | Hög/Medel | ✓ Sept 2025-incident matchar exakt |
| BKL | Burke Lakefront | USA | **Ja** – sedan ~2013 | 6L-24R (västra änden) | Medel | ✓ Feb 2018-incident matchar exakt |
| CRW | Charleston Yeager | USA | **Ja** – sedan 2007 (fanns redan vid 2010-olyckan) | 5 | Hög | ✓ Jan 2010-signalen matchar exakt |
| PDK | DeKalb-Peachtree | USA | **Ja** – sedan dec 2018 | 21L | Hög | ✓ |
| GMU | Greenville Downtown | USA | **Ja** (bana 1, 2003); planerat (bana 19) | 1 (byggt), 19 (planerat) | Hög/Medel | ✓ 2006-signalen matchar Falcon 900-incidenten |
| RDG | Reading Regional | USA | **Ja** – sedan 2009 | 13 | Medel-Hög | ✓ |
| ORH | Worcester Regional | USA | **Ja** – ersatt 2024/2025 | Båda ändar 11/29 | Hög | ✓ 5 USAspending-signaler matchar fas-projektet |
| MMU | Morristown Municipal | USA | **Nej** – planerat, ej klart förrän ~2027 | 5 (planerat) | Medel-Hög | ✓ Bidragssignal matchar pågående fasprojekt |
| VPC | Cartersville | USA | **Ja** – sedan 2021 | 1/19 (minst en ände) | Medel-Hög | ✓ |
| — | Allegheny County (AGC) | USA | **Oklart** – troligen ej klart än | Båda ändar 10-28 (planerat) | Medel | ✓ Bidragssignal matchar pågående upphandling |

---

## Detaljerade fynd per flygplats

### Internationellt

**SDU — Santos Dumont, Rio de Janeiro, Brasilien**
Inte byggt än. Tillkännagavs sept 2024 som del av ett R$400M
moderniseringspaket (R$150M till EMAS), modellerat på Congonhas (CGH) som
fick EMAS 2022. Mål: färdigt andra halvåret 2026. Ingen källa bekräftar
färdigställande. Källor: [Agência Brasil](https://agenciabrasil.ebc.com.br/geral/noticia/2024-09/aeroporto-santos-dumont-tera-sistema-de-seguranca-para-pistas-curtas), [Aeroin.net](https://aeroin.net/governo-anuncia-implantacao-de-area-de-escape-na-pista-do-aeroporto-santos-dumont/), [Poder360](https://www.poder360.com.br/poder-infra/santos-dumont-tera-investimento-de-r-400-milhoes-ate-2027/). Konfidens: Medel.

**WLG — Wellington International, Nya Zeeland**
Klart och bekräftat **24 mars 2026**, båda ändar av bana 16/34 (~55×90m
vardera, 3000+ block), inom budget (35 MNZD). Landningssträcka +143m,
startsträcka +37m. Källor: [Wellington Airport](https://www.wellingtonairport.co.nz/news/airport-updates/wellington-airport-completes-major-runway-upgrade/), [RNZ](https://www.rnz.co.nz/news/national/590488/wellington-airport-completes-major-runway-safety-upgrade), [Scoop](https://www.scoop.co.nz/stories/BU2603/S00410/wellington-airport-completes-major-runway-upgrade.htm). Konfidens: Hög.

### USA — större flygplatser

**CLT — Charlotte Douglas Intl**
Befintlig EMAS på bana 36R (18C/36C); Charlotte-kommunens upphandling
(ITB 25-13, öppnad sept 2024) gäller **ersättning** av det befintliga
blocksystemet. Källor: [City of Charlotte ITB 25-13](https://www.charlottenc.gov/files/content/city/v/4/growth-and-development/doing-business/contract-opportunities/emas-replacement/avia-itb-25-13-emas-replacement.pdf). Konfidens: Hög.

**MSP — Minneapolis-St Paul Intl**
EMAS på avgångsänden av bana 12R (160×216 ft). Installationsår ej
bekräftat i denna sökning. Konfidens: Medel.

**PHL — Philadelphia Intl**
PHL:s **första EMAS någonsin**, klart 12 juni 2025, öster om bana 8-26,
~2000 EMASMAX-block, byggt av Runway Safe, 8,5 MUSD federalt finansierat.
Källor: [PHL Airport](https://www.phl.org/newsroom/EMAS), [6abc](https://6abc.com/post/philadelphia-international-airport-marks-completion-runway-safety-project-installation-engineered-material-arresting-system/17652799/), [AirlineGeeks](https://airlinegeeks.com/2025/08/27/philadelphia-airport-adds-airplane-safety-net/). Konfidens: Hög.

**JFK — John F. Kennedy Intl**
Historiskt sett **världens första EMAS-installation (1996)**. Idag vid
DER 4R (393×226 ft) och DER 22L (405×226 ft) – troligen ombyggt/omnumrerat
sedan 1996. Källor: [FAA EMAS-program](https://www.faa.gov/airports/engineering/incursions_excursions/emas), [Wikipedia](https://en.wikipedia.org/wiki/Engineered_materials_arrestor_system). Konfidens: Hög (existens), Medel (exakt bana/år-koppling till 1996).

**LGA — LaGuardia**
EMAS vid avgångsändarna av bana 13 och 22 (numera troligen alla
banändar efter 2015 års utbyggnad i Flushing Bay/Rikers Island Channel).
Berömd räddning **okt 2016** (Mike Pence-kampanjplanet, stannade ~300 ft
från motorväg) – matchar exakt vår signal daterad 2016-10-01. Källor:
[Port Authority](https://portfolio.panynj.gov/2016/11/08/port-authority-aviation-a-save-at-laguardia-airport/), [NYCAviation](https://www.nycaviation.com/2015/05/safe-la-guardias-short-runways/38659). Konfidens: Hög.

**BOS — Boston Logan Intl**
Tre system: bana 22R (2005), bana 33L/15R (2006, ombyggt till EMASMAX
2013 på pir i hamnen), och ett **helt nytt** under byggnation på bana 27
(stängningar sept 2025, arbete fortsätter efter juli 2026 – sannolikt
inte helt klart ännu). Källor: [Massport](https://www.massport.com/media/newsroom/massport-begin-runway-safety-work-boston-logan), [Airport Improvement](https://airportimprovement.com/article/logan-intl-builds-concrete-pier-over-boston-harbor-support-runway-safety-area-extension/). Konfidens: Hög (befintliga system), Medel (exakt slutförandestatus bana 27).

**MDW — Chicago Midway Intl**
EMAS sedan **2007** (direkt svar på den dödliga Southwest 1248-olyckan
dec 2005, som skedde *innan* EMAS fanns här) på bana 13C/31C, plus
**greenEMAS** (återvunnet glas) på bana 22L från nov 2014 med fler
installationer 2015-2016 – Midway och O'Hare var världens första
flygplatser med enbart greenEMAS. Källor: [NTSB AAR-07/06](https://www.ntsb.gov/investigations/AccidentReports/Reports/AAR0706.pdf), [PRWeb 2015](https://www.prweb.com/releases/chicago_airports_to_install_first_ever_sustainable_emas_solution_at_midway_and_o_hare/prweb12986556.htm). Konfidens: Hög.

**ORD — Chicago O'Hare Intl**
EMAS vid bana 04R och 22L, troligen installerat ~2016-2017 som del av
samma Chicago-övergripande greenEMAS-satsning som Midway. Källor: samma
PRWeb-release, [SKYbrary](https://skybrary.aero/articles/engineered-materials-arresting-system-emas). Konfidens: Medel-Hög.

### USA — regionala/mindre flygplatser

**SFO — San Francisco Intl**
Fyra bäddar (bana 1L, 1R, 19L, 19R), byggda 2014. Källor: [Runway Safe](https://runwaysafe.com/references/sfo-san-francisco-international-airport/), [CBS News SF](https://www.cbsnews.com/sanfrancisco/news/2-sfo-runways-to-close-4-months-for-safety-improvements-beginning-may/). Konfidens: Hög.

**TEB — Teterboro**
Tre bäddar (bana 06, 19, 24), den första klar okt 2006 efter PANYNJ:s
åtagande 2005 (NTSB-utredning fann att alla fyra RSA var undermåliga).
En **mycket färsk** artikel (April 2026) bekräftar att systemet stoppade
ett flygplan från att köra av banan – matchar exakt vår signal daterad
2026-04-01. Källor: [FAA TEB-info](https://www.faa.gov/sites/faa.gov/files/TEB.pdf), [News 12 NJ (24 apr 2026)](https://newjersey.news12.com/2026/04/24/faa-safety-system-stops-aircraft-from-overrunning-runway-at-teterboro-airport/20sivSB8aGXzySCviiWGFM). Konfidens: Hög.

**MHT — Manchester-Boston Regional**
Befintlig EMAS på bana 6-24 (avgångsänden), **håller på att ersättas just
nu** – Manchester Dept. of Aviation-upphandling, arbete planerat till
sommaren 2026. Matchar vår signal "Runway 6 departure-end EMAS
replacement" exakt. Källor: [flymanchester.com projektmanual (juni 2026)](https://www.flymanchester.com/wp-content/uploads/2026/06/19199.11-MHT-Runway-6-Departure-End-EMAS-Project-Manual.pdf). Konfidens: Hög.

**HYA — Cape Cod Gateway (Barnstable Municipal)**
EMAS finns, del av en $25M (95% FAA/MassDOT-finansierad) banrenovering;
banan öppnade igen okt 2023, EMAS-delen separat spårad med mål **mars
2025**. ⚠ **Vår signal (id 10, "EMAS reconstruction") har fortfarande
confidence="planned"** – om det verkliga projektet redan slutfördes mars
2025 som källorna antyder, ligger vår signal sannolikt efter verkligheten
och borde granskas/uppdateras manuellt (ingen ändring gjord här). Källor:
[CapeCod.com](https://www.capecod.com/newscenter/runway-6-24-reopens-as-cape-cod-gateway-airport/), [FlyHYA](https://flyhya.com/cape-cod-gateway-airport-prepares-to-begin-a-25-million-airport-improvement-project/). Konfidens: Hög (befintlig/ersätts), Låg (exakt 2003-installationsår).

**BGM — Greater Binghamton (Edwin A. Link Field)**
Två bäddar (bana 16, 34). Installationsår ej funnet. Källor: FAA Chart
Supplement (spegllad via [fltplan.com](https://www.fltplan.com/AirportInformation/KBGM.htm)). Konfidens: Hög (existens), okänt (år).

**STP — St Paul Downtown (Holman Field)**
Två bäddar, DER 14 och DER 32 – matchar vår signal exakt ("replacement at
both ends"). Källa: FAA Chart Supplement ([AirNav](http://www.airnav.com/airport/stp)). Konfidens: Hög.

**MKC — Charles B. Wheeler Downtown**
Två bäddar, DER 01 och DER 19 (nära Missouri-floden). Källa: FAA Chart
Supplement. Konfidens: Hög.

**FTY — Fulton County Executive (Charlie Brown Field)**
**Ingen EMAS ännu.** RSA-brister dokumenterade (690×150 ft kort på 08,
430×110 ft kort på 26). Ett Draft Environmental Assessment för
säkerhetsförbättringar (inkl. EMAS + banförlängning) släpptes **26 aug
2026**, delvis kopplat till VM 2026-relaterade flygplatsuppgraderingar.
Matchar vår signal exakt (status="design", confidence="programmed").
Källor: [Georgia DOT FTY System Plan](https://www.dot.ga.gov/InvestSmart/Aviation/GAAirportsDocuments/FTY_SysPlan.pdf), [evaint.com](https://evaint.com/fulton-county-to-make-6mn-upgrades-ahead-of-2026-world-cup/). Konfidens: Medel.

**ASE — Aspen/Pitkin County (Sardy Field)**
**Ingen EMAS**, och enligt flygplatsens egen offentliga FAQ (som citerar
en namngiven FAA-tjänsteman, John Bauer) krävs **ingen** EMAS här –
Aspen har tillräcklig standard-säkerhetsyta för sin Group III-design.
⚠ **Detta står i viss spänning mot vår signal** (id 1, "Runway 15/33
future EMAS at both runway ends", confidence="planned"), som bygger på en
"adopted Common Ground Recommendation Airport Map"-resolution (2024).
Möjliga förklaringar: (a) den lokalt antagna framtidskartan visar en EMAS
som en möjlighet/option snarare än ett krav, (b) FAA:s ståndpunkt kan ha
ändrats sedan resolutionen antogs, eller (c) det är två olika,
icke-motsägande saker (en lokalt önskad framtidsinvestering som FAA inte
tvingar fram). **Rekommendation: läs om den ursprungliga källan
(Pitkin County-resolutionen) och jämför datum mot flygplatsens FAQ innan
signalens confidence-nivå tas för given.** Källor: [Aspen Airport
Advisory Board FAQ](https://www.aspenairport.com/about-aspen-airport/airport-advisory-board-faqs/), [Aspen Airport Modernization Projects](https://www.aspenairport.com/modernization/projects/). Konfidens: Hög.

**CRQ — McClellan-Palomar, Carlsbad**
**Inte byggt.** San Diego County godkände en Airport Master Plan dec
2021 som kopplar en 200 ft banförlängning till EMAS vid båda ändar
(säkerhetsmotivering för att slippa full-längds RSA). 20-årig fasad plan;
ingen källa bekräftar byggstart. Matchar vår signal (status="cip",
confidence="programmed"). Källor: [10News](https://www.10news.com/news/san-diego-county-supervisors-approve-runway-extension-at-mcclellan-palomar-airport), [San Diego County FAQ](https://www.sandiegocounty.gov/content/sdc/dpw/airports/palomar/masterplan/faqs.html). Konfidens: Medel.

**PWK — Chicago Executive**
Båda ändar av bana 16/34; bana 34-bädden ~2012-2013, bana 16 klar **7 nov
2015** (första reliever-flygplatsen i Illinois med EMAS). Två färska
incidenter (inkl. en Gulfstream G150 som körde av 3 sept 2025) matchar
våra tre incident-signaler (2016, 2021, 2025) mycket väl – och Sept
2025-händelsen är samma dag som Boca Ratons incident, båda uppmärksammade
i samma FAA-pressmeddelande. Källor: FAA, Zodiac Arresting Systems/PRLog,
CMT Engineering, [AVweb](https://www.avweb.com), lokala Chicago-nyheter. Konfidens: Hög.

**FLL — Fort Lauderdale-Hollywood Intl**
EMAS sedan **2004** på norra banans båda ändar. Öster-bädden **skadades
i den katastrofala Fort Lauderdale-översvämningen april 2023** (matchar
vår signal daterad 2023-04-01 exakt!); provisoriska reparationer
möjliggjorde återöppning 18 maj 2023, full ersättning av 4000+ block
planerad från 14 jan 2025. Källor: Broward County Aviation Dept.,
[SKYbrary KFLL](https://skybrary.aero), Local10 News. Konfidens: Hög.

**PBI — Palm Beach Intl** *(vår databas har detta flygplatsens FAA-LID
felaktigt/skämtsamt registrerat som "DJT" och namnet som "President
Donald J. Trump International" – redan känt och hanterat i en tidigare
utredning, se utreding_2026-07-26-del2/backfill-arbetet)*
EMAS på avgångsänden av bana 14, funnits sedan minst 2011 (använd i en
2011 Gulfstream-incident). Ingen bekräftelse av EMAS på bana 32. Källor:
FDOT flygplatsprofil, Wikipedia, Local10 News (feb 2026, "sex
Florida-flygplatser med EMAS"). Konfidens: Medel-Hög.

**BUR — Hollywood Burbank (fd Bob Hope)**
EMAS på västra änden av bana 8/26 sedan **jan 2002**, byggd direkt efter
Southwest 1455-olyckan (mars 2000, innan EMAS fanns här). Stoppade
Southwest 278 (**dec 2018**, 117 ombord, FAA kallade prestandan
"textbook") – matchar vår signal daterad 2018-12-01 exakt. Vår databas
har **även** en signal daterad 2017-04-01 som forskningen inte hittade
någon offentlig källa för – troligen en mindre uppmärksammad händelse som
bara syns i FAA:s interna incidentkarta, inte i allmän nyhetsbevakning.
Ingen anledning att misstänka fel, bara en observation om att vår
FAA-källa är mer detaljerad än vad öppen webbsökning fångar. Källor:
NTSB (Flight 1455), Wikipedia, ABC7/NBC/CBS (2018-händelsen). Konfidens:
Hög.

**BCT — Boca Raton**
EMAS på båda ändar av bana 5/23, byggd efter en 2012 RSA-studie som
konstaterade EMAS som enda praktiska lösning (begränsad yta pga Spanish
River Blvd. och en el-anläggning). Stoppade en privatjet **3 sept 2025**
(samma dag som PWK-incidenten, båda i samma FAA-pressmeddelande) –
matchar vår signal (2025-09-01) exakt. Källor: [Boca Raton Airport](https://bocaairport.com), FAA-nyhetsrum, CBS12. Konfidens: Hög.

**EYW — Key West Intl**
EMAS på båda ändar av den enda banan (9/27), fanns redan **nov 2011**
(en Citation stoppades 148 ft in i bädden; fyra dagar tidigare hade en
Gulfstream G150 kört av den *motsatta* änden och skadats – ett ofta
citerat före/efter-EMAS-exempel). Matchar vår signal (2011-11-01) exakt.
Källor: Wikipedia, NBAA "The Case for EMAS", Local10 News. Konfidens:
Hög.

**SUA — Witham Field (Martin County)**
EMAS på båda ändar av bana 12/30. Byggnation pågick 2018; en Local10
News-rapport (feb 2026) bekräftar att den nu är i drift – färdigställd
någon gång 2018-2026. Källor: NBAA, FDOT, hometownnewstc.com. Konfidens:
Medel.

**TEX — Telluride Regional**
EMAS på båda ändar av bana 9/27, byggd 2009-2010 (FAA-bidrag 2009), enda
EMAS i Colorado. Källor: Business View Magazine, Airport Improvement
magazine. Konfidens: Hög.

**LEX — Blue Grass**
EMAS på båda ändar av bana 4/22. Installationsår ej funnet – svagaste
belagda posten av de 39 (endast dataaggregatorer, ingen FAA/nyhetskälla).
Konfidens: Medel.

**ROA — Roanoke-Blacksburg Regional**
Ursprunglig EMAS **2004** på bana 16/34, renoverad 2012, **helt ersatt**
med ett nytt 12 MUSD-system (4708 block, Runway Safe/Branch Builds)
apr-maj 2024. Stoppade en United Express ERJ-145 (CommuteAir) **24 sept
2025** i kraftigt regn – flygplatsens första EMAS-aktivering någonsin,
matchar vår signal (2025-09-01) exakt. Separat: en EMAS-plan för bana
6/24 fick miljögodkännande jan 2018 men verkar **aldrig ha byggts** –
flygplatsens egen 2023 Master Plan Update listar fortfarande bana 6/24:s
säkerhetsyta som ett olöst problem (föreslår en bro över I-581 istället).
Källor: ENR (branschutmärkelse), Branch Group, DOT permitting dashboard,
lokala Virginia-nyheter. Konfidens: Hög (16/34), Medel (6/24 aldrig
byggd).

**BKL — Burke Lakefront**
EMAS på västra änden av bana 6L-24R sedan ~2013, stoppade en Beechjet
400A **feb 2018** – matchar vår signal (2018-02-01) exakt. Källor:
Independence Excavating (entreprenör). Konfidens: Medel.

**CRW — Charleston Yeager**
EMAS klar **okt 2007** på bana 5 – fanns alltså redan när en
PSA/US Airways Express CRJ-200 (34 ombord) körde av **19 jan 2010** och
stannade 128 fot in i bädden. Matchar vår signal (2010-01-01) exakt.
Källor: SKYbrary, Aviation Safety Network, Wikipedia. Konfidens: Hög.

**PDK — DeKalb-Peachtree**
EMAS klar **dec 2018** på bana 21L – Georgias första EMAS, 1746 block,
8 MUSD-projekt. Källor: Airport Improvement magazine, AJC, DeKalb County.
Konfidens: Hög.

**GMU — Greenville Downtown**
EMAS på bana 1 sedan **2003** (första allmänflyg-flygplatsen i USA med
EMAS; stoppade en Falcon 900 **2006** – matchar vår signal 2006-07-01
exakt). Ett separat, federalt finansierat projekt (juli 2023, $5M) gäller
**ersättning** av bana 1-bädden och **ny** EMAS på bana 19 – inget fynd
bekräftar att detta är klart än. Källor: FAA, McFarland Johnson, The
Center Square. Konfidens: Hög (bana 1), Medel (bana 19/ersättning).

**RDG — Reading Regional**
EMAS sedan 2009 på bana 13 (väg, träd och Schuylkill-floden bortom
banänden). Källor: FAA/Medium, Aviation View Magazine. Konfidens:
Medel-Hög.

**ORH — Worcester Regional**
Ursprunglig EMAS 2009, **helt ersatt** i en fasad ~10 MUSD-satsning: bana
29-änden 2024, bana 11-änden 2025 (nya bäddar klarar upp till Boeing
737-800). Matchar väl våra 5 USAspending-bidragssignaler (FY2024-2025).
Källor: Spectrum News 1, Massport, FAA/Medium. Konfidens: Hög.

**MMU — Morristown Municipal**
**Inte byggt än.** Fasat projekt: Fas X (nov 2024, klar) förlängde
säkerhetsytan; Fas XI (2025) omfattade blockinköp; Fas XII (2026,
banbeläggning); Fas XIII (2027, faktisk blockinstallation) – dvs. fysisk
färdigställning väntas inte förrän **2027**. Vår USAspending-bidragssignal
(FY2025, "PHASE 11... EMAS BLOCKS FOR RUNWAY 23" enligt tidigare
utredning) matchar fasplanens blockinköpsfas väl. Källor:
[mmuair.com](https://www.mmuair.com/runway-5-23-rehabilitation-project/), Morris County Alliance. Konfidens: Medel-Hög.

**VPC — Cartersville**
EMAS sedan **2021** (banstängning för beläggning/EMAS 31 maj–19 juni
2021). Källor: Cartersville Airport, C.W. Matthews (entreprenör). Osäkert
om båda banändar eller bara en har bädd. Konfidens: Medel-Hög.

**Allegheny County Airport (AGC), West Mifflin, PA** *(databasens
"Allegheny County Airport Authority"-rad utan kod – verifierad identitet:
Allegheny County Airport, FAA-LID AGC, drivs av Allegheny County Airport
Authority som även driver Pittsburgh Intl)*
**Oklart, troligen inte klart än.** Miljögodkännande (FONSI) sept 2022;
materialkontrakt med Runway Safe (~5 MUSD, 4 år) tecknat okt 2023; en
separat installationsupphandling skulle läggas ut 2025; en relaterad
banrenoveringsfas planerades öppna för anbud 20 maj 2026. Ingen källa
bekräftar att bäddarna är fysiskt installerade och i drift. Matchar vår
USAspending-bidragssignal (FY2023) väl – bidraget speglar just
materialupphandlingen, inte ett färdigt system. Källor: Fly Pittsburgh,
FAA FONSI-dokument, ACAA-styrelseprotokoll. Konfidens: Medel.

---

## Förslag / värt att undersöka vidare

1. **HYA (Cape Cod Gateway):** vår signal har `confidence="planned"` men
   flera källor pekar mot att EMAS-ersättningen redan var klar runt mars
   2025 – d.v.s. sannolikt inaktuell status. Föreslår manuell granskning
   och ev. uppdatering (ingen ändring gjord i denna utredning).
2. **ASE (Aspen):** flygplatsens egen offentliga FAQ citerar en namngiven
   FAA-tjänsteman som säger EMAS **inte krävs** här, vilket står i viss
   spänning mot vår "planned"-signal baserad på en 2024 lokal
   resolution. Föreslår att den ursprungliga källan (Pitkin County-
   resolutionen) läses om och jämförs mot FAQ:ns datum/sammanhang.
3. **Allmän observation:** i stort sett alla 39 flygplatsers signaler
   stämmer mycket väl överens med oberoende, offentliga källor – flera
   incident-signaler matchar exakta datum för verkliga, namngivna
   händelser (LGA/Pence 2016, TEB/april 2026, BCT+PWK/sept 2025,
   ROA/CommuteAir sept 2025, CRW/2010, BKL/2018, FLL/översvämningen 2023,
   GMU/Falcon 900 2006, EYW/2011, BUR/Southwest 278). Det här är ett
   starkt tecken på att den bakomliggande FAA-incidentdatan (och därmed
   auto-genererade replacement_after_incident-signaler) är pålitlig.
4. **Ny kandidat, inte i vår 39-lista:** en Local10 News-artikel (feb
   2026) om "sex Florida-flygplatser med EMAS" namnger även **Venice, FL
   (VNC)** – som redan finns i vår `airports`-tabell (id 34, faa_code
   VNC) men saknar signaler/installation. Kan vara värt att undersöka
   som en ny signal/installation-kandidat i ett separat, framtida steg.
5. **BUR:** vår databas har en incident-signal daterad 2017-04-01 som
   inte gick att styrka mot någon offentlig nyhetskälla i denna sökning –
   troligen bara en mindre händelse som enbart syns i FAA:s interna
   incidentkarta. Ingen anledning att misstänka fel, men noterat för
   fullständighetens skull.

## Metodbegränsningar

- Flera installationsår kunde inte beläggas med en primär/auktoritativ
  källa (t.ex. MSP, BGM, LEX, VPC:s exakta bandäckning) – dessa har
  Medel/Låg konfidens och bör inte tas som definitiva utan vidare
  verifiering.
- Sökningen är ett ögonblicksfynd (utförd 2026-07-26/27) – projekt under
  byggnation (t.ex. BOS bana 27, GMU bana 19, AGC, MMU) kan ha ändrat
  status sedan dess.
- Ingen fil, mall eller databasrad har skrivits till eller ändrats som en
  del av detta arbete – rent research- och rapportarbete enligt
  instruktion.
