# Utredning: FAA Fact Sheet 2011/2016 – resten av innehållet

Databasändring. Fortsättning på `docs/utredning_faa_factsheet_2011_2016.md`
– går igenom **allt** som inte ingick i den föregående, snävare listan:
resten av båda fact sheets huvudtabeller plus 2016:s "additional projects
currently under contract"-tabell. Skript:
`scripts/import_faa_fact_sheets_resten.py` (idempotent, testad, 4 tester).

## Alla 47 flygplatser fanns redan i databasen

Kontrollerade samtliga innan skrivning. **Ingen ny flygplats behövde
skapas** den här gången (till skillnad från förra körningens Dutchess
County) – alla hade redan en generisk, årtalslös FAA-karta-sourcad
Installation-rad. Samma mönster som förra körningen: 44 flygplatser fick
en ny, separat, daterad Installation-rad (gamla raden orörd); 3 fick bara
en kompletterande not (se nedan, ingen ny rad).

## De 44 nya, daterade raderna

Alla sourcade till 2016 Fact Sheet (inget fall i den här omgången där 2011
är mer detaljerat, till skillnad från Groton-New London i förra
körningen).

| Flygplats | install_year | System | Detalj |
|---|---|---|---|
| JFK | 1996 | 2 | (1999)/2007(2014) – ersättningar. Historiskt världens första EMAS. |
| **MSP** | **1999** | 1 | (2008) ersatt. **Löser den tidigare öppna frågan i `docs/utredning_svaga_poster.md`** om okänt installationsår! |
| Little Rock (LIT) | 2000 | 2 | 2000/2003. |
| Burbank (BUR) | 2002 | 1 | Breddad 2008. Matchar tidigare research. |
| Greenville Downtown (GMU) | 2003 | 1 | GA, retrofit 2010 (ny detalj, skild från det redan kända bana 19-projektet). |
| Roanoke Regional (ROA) | 2004 | 1 | Matchar tidigare research. |
| Fort Lauderdale (FLL) | 2004 | 2→4 | Växte till 4 system 2014. |
| LaGuardia (LGA) | 2005 | 2→4 | Ersatt 2014, växte till 4 system 2015. |
| Boston Logan (BOS) | 2005 | 2 | System B ersatt både 2012 och 2014. |
| Teterboro (TEB) | 2006 | 1→3 | Växte till 3 system (2011, 2013). |
| Chicago Midway (MDW, ESCO-raden) | 2006 | 2 | Skild från Runway Safes greenEMAS (redan hanterat). Fotnot "****" **odefinierad i dokumentet** – flaggat, ej gissat. |
| Merle K Smith/Cordova (CDV) | 2007 | 1 | |
| Charleston Yeager (CRW) | 2007 | 1 | Matchar tidigare research. |
| Wilkes-Barre/Scranton (AVP) | 2008 | 2 | |
| San Luis Obispo (SBP) | 2008 | 2 | |
| Newark Liberty (EWR) | 2008 | 1→2 | Växte 2015. |
| St Paul Downtown (STP) | 2008 | 2 | Matchar tidigare research. |
| Worcester Regional (ORH) | 2008 | 2 | Ursprunglig, senare helt ersatt 2024/2025 (redan känt). |
| Reading Regional (RDG) | 2009 | 1 | GA. Matchar tidigare research. |
| Kansas City Downtown (MKC) | 2009 | 2 | Matchar tidigare research. |
| Key West (EYW) | 2010 | 1→2 | Växte 2015. Matchar tidigare research. |
| Arcata-Eureka (ACV) | 2010 | 1 | |
| Telluride Regional (TEX) | 2010 | 2 | Matchar tidigare research. |
| Lafayette (LFT) | 2011 | 2 | + ett tredje system "under contract" (fall 2016, ej bekräftat). |
| Cleveland Hopkins (CLE) | 2011 | 2 | Ny sedan 2011-dokumentet. |
| Trenton-Mercer (TTN) | 2012 | 4 | Ny sedan 2011-dokumentet. |
| New Bern (EWN) | 2012 | 1 | Ny sedan 2011-dokumentet. |
| Memphis (MEM) | 2013 | 1 | Ny sedan 2011-dokumentet. |
| Burke Lakefront (BKL) | 2013 | 1 | Matchar tidigare research. |
| San Francisco (SFO) | 2014 | 4 | Matchar tidigare research. |
| T.F. Green (PVD) | 2014 | 1→2* | Se anmärkning om dubbelrader nedan. |
| Addison (ADS) | 2014 | 1 | |
| Chicago Executive (PWK) | 2014 | 1→2* | Matchar tidigare research (~2012-2015). |
| Reagan National (DCA) | 2014 | 3 | |
| Monterey (MRY) | 2015 | 1 | Se anmärkning om dubbelrader nedan. |
| Oakland (OAK) | 2015 | 1 | |
| Nome (OME) | 2015 | 1 | |
| Lehigh Valley (ABE) | 2015 | 2 | |
| John Tune (JWN) | 2015 | 1 | |
| Kodiak (ADQ) | 2015 | 2 | |
| Rutland (RUT) | 2015 | 1 | |
| Sikorsky (BDR) | 2015 | 1 | |
| McAllen International (MFE) | 2015 | 1 | |
| "Sandiford" (SDF) | 2015 | 1 | Se identitetsanmärkning nedan. |

## Dubbelrader i 2016-dokumentet (T.F. Green, Chicago Executive, Monterey)

Tre flygplatser förekommer **två gånger** i 2016-dokumentets huvudtabell.
För T.F. Green och Chicago Executive går datumet från ett tidigare år
(2014) till "fall 2015" – tolkat som **två separata system** (rimligt,
samma mönster som andra flygplatser i tabellen som växer). För Monterey
anger **båda** raderna samma år (2015) – tolkat som en **dubblettrad i
källdokumentet**, inte två system. Detta är en tolkning, inte en
FAA-bekräftad sanning – flaggat i respektive rads notes.

## Identitetsanmärkning: "Sandiford" är sannolikt "Standiford"

Både fact sheets och vår egen databas stavar namnet **"Sandiford"**
(Louisville, KY). Oberoende sökning visar att detta med mycket stor
sannolikhet är en felstavning av **"Standiford"** (Standiford Field, det
historiska namnet på Louisville Muhammad Ali International, IATA/ICAO/FAA
**SDF**) – ett $18,8M Runway 11-29 Safety Area Improvement-projekt med
EMAS färdigställdes där "by late 2015", vilket matchar fact sheet:s "fall
2015" nästan exakt. **Flygplatsnamnet är inte ändrat** i denna körning
(en namnkorrigering är utanför omfånget för en dataimport) – flaggat här
för en framtida rättelse, i linje med hur DJT/PBI-namnfrågan hanterades
tidigare.

## De 3 "under contract"-flygplatserna – bara noter, ingen ny rad

2016-dokumentets "additional projects currently under contract"-tabell
namnger fyra flygplatser; en (Martin County/SUA) var redan klar i förra
körningen. Av de tre kvarvarande hade **ingen** en bekräftad
färdigställandeår i något av dokumenten – bara en förväntan ("expected
2016") – så de fick en kompletterande not på sin befintliga
Installation-rad istället för en ny, daterad rad:

- **DeKalb/Peachtree (PDK):** vår egen tidigare research
  (`docs/utreding_status_flygplatser.md`) har redan bättre, mer specifik
  data (dec 2018, 1 746 block, 8 MUSD) – notan länkar bara ihop de två
  källorna, skriver inte över.
- **Venice (VNC):** kopplad till Local10 News-artikeln som redan nämndes
  i `docs/utreding_status_flygplatser.md`s "Ny kandidat"-avsnitt.
  **Rättelse av ett tidigare antagande:** den utredningen antog att VNC
  saknade Installation/Signal helt – det stämmer inte, en generisk
  FAA-post fanns redan (id 22).
- **Boca Raton (BCT):** vår egen tidigare research hade redan "efter
  2012" (Hög konfidens) men utan specifikt år – "expected 2016" är den
  mest specifika dateringen som finns, men fortfarande en projektion.

## Verifiering

- `scripts/import_faa_fact_sheets_resten.py` kört: 44 nya
  Installation-rader, 3 kompletterande noter, 0 nya flygplatser.
- Ett verkligt idempotens-fel hittades och rättades under utvecklingen:
  de tre "under contract"-noterna innehöll inte källans URL, så
  återkörnings-spärren (`if url not in notes`) triggade aldrig – varje
  körning hade lagt till samma not på nytt. Fixat innan den slutgiltiga
  körningen (URL:en ingår nu i varje not), verifierat med två körningar i
  rad.
- `scripts/export_static_site.py --output site` kört om utan fel.
- Hela testsviten (259 tester, inkl. 4 nya) grön.

## Metodbegränsningar

- Fotnoten "****" på Chicago Midways ESCO-rad är odefinierad i
  dokumentets egen legend – inte gissad, bara flaggad.
- Tolkningen av dubbelraderna (T.F. Green/Chicago Executive som 2 system,
  Monterey som 1) är rimlig men inte oberoende verifierad mot en
  tredje källa.
- "Sandiford"/"Standiford"-identiteten är inte bekräftad direkt mot FAA,
  bara via oberoende webbsökning som råkar matcha tidslinjen väl.
- Ingen av de tre "under contract"-flygplatserna fick ett satt
  `install_year` – bara noter. Om någon vill ha ett hårt årtal där krävs
  en starkare källa (t.ex. en branschartikel om faktiskt färdigställande,
  liknande LEX-mönstret från en tidigare session).
