# Utredning: FAA Fact Sheet – EMAS (2011 och 2016)

Databasändring. Två FAA-pressmeddelanden ("Fact Sheet – Engineered Material
Arresting System(s) (EMAS)"), 2011-03-07 och 2016-02-04, importerade som
primärkällor. Skript: `scripts/import_faa_fact_sheets_2011_2016.py`
(idempotent, testad). PDF:erna är arkiverade i repot:
`docs/sources/2011-03-07_faa-emas-fact-sheet.pdf` och
`docs/sources/2016-02-04_faa-emas-fact-sheet-skybrary-mirror.pdf`.

## Läsbarhet: 2011 = text, 2016 = bild, löst utan OCR-mjukvara

2011-dokumentet extraherades direkt med `pdfplumber` – ingen komplikation.

2016-dokumentet (hämtat från
`https://skybrary.aero/sites/default/files/bookshelf/1842.pdf`, eftersom
FAA:s egen sida för denna newsId inte längre finns) gav **noll tecken**
med `pdfplumber` på samtliga sex sidor – bekräftar din misstanke om att
det är skannat/bildbaserat.

**OCR-försök:** `choco install tesseract` misslyckades – chocolatey
kräver adminrättigheter som den här sandboxen inte har
("`Access to the path 'C:\ProgramData\chocolatey\lib-bad' is denied`").
Istället för att dra in en tung pip-baserad OCR-stack (t.ex. `easyocr` +
`torch`, som redan hade börjat laddas ner innan jag stoppade den) för ett
sex-sidors dokument: renderade sidorna till PNG med `PyMuPDF` (ren
pip-installation, inget systembinärt beroende) och läste dem **direkt**
med min egen bildsyn – jag är en multimodal modell, så det var mer
tillförlitligt än OCR ändå för ett rent tabelldokument. Alla sex sidor
lästes fullständigt och korrekt.

## Prioriteringsregel: 2016 > 2011, utom där 2011 är mer detaljerat

Som instruerat: 2016 har företräde där båda dokumenten täcker samma
flygplats, **utom** för Groton-New London, där 2011-dokumentet faktiskt
ger mer detaljerad information (fasad tidslinje sommar 2011/höst 2012)
än 2016-dokumentets förenklade "2011". Där är 2011 satt som primärkälla
(`source_id`) och 2016 citeras i notes som bekräftelse på att båda
systemen är färdigställda.

## Mönster: nya, separata Installation-rader (inte skriva över)

Alla berörda flygplatser hade redan en generisk FAA-karta-sourcad
Installation-rad (`install_year=None`) från en tidigare import. I linje
med samma mönster som `scripts/add_gadelius_greenemas_installations.py`
använde för MDW/ORD: varje flygplats fick en **ny, separat**
Installation-rad med Fact Sheet-årtalet, den gamla raden är orörd.

## 1. ORD-korrigering

**Chicago O'Hare: install_year=2008 (2 system).** Både 2011- och
2016-dokumentet är överens ("Chicago-O'Hare, Chicago, IL, 2, 2008" i
båda) – rättar en tidigare gissning om ~2016-2017, som var **8 år fel**.

## 2. Fyllda originalår

| Flygplats | install_year | System | Detalj |
|---|---|---|---|
| BGM (Greater Binghamton) | 2002 | 2 | 2016 spjälkar upp: system A 2002→ersatt 2012 (matchar det kända $12,3M FAA FY2011-bidraget, se `docs/utredning_svaga_poster.md`), system B 2009 ("retrofitted bed"). 2011 visar bara grundfaktumet. |
| HYA (Cape Cod Gateway/Barnstable Municipal) | 2003 | 1 | Oförändrat mellan dokumenten. |
| MHT (Manchester-Boston) | 2007 | 1 | Oförändrat. |
| CLT (Charlotte Douglas) | 2008 | 1 | Oförändrat. |
| SUA (Witham Field/Martin County) | 2011 | 2 | 2011: "under contract" (ej klart vid publicering). 2016: bekräftat färdigställt. |
| PBI (Palm Beach, DB-kod "DJT") | 2011 | 1 | Samma mönster som SUA: 2011 = kontrakt, 2016 = bekräftat klart. |

## 3. Nya kandidater – alla utom en fanns redan

Kontrollerade samtliga innan skrivning. **Alla utom Dutchess County fanns
redan** i databasen (med en generisk, årtalslös FAA-installation) – fick
alltså en ny, kompletterande Installation-rad snarare än en ny flygplats:

| Flygplats | install_year | System | Detalj |
|---|---|---|---|
| ROC (Rochester International) | 2001 | 1 | Oförändrat. |
| BTR (Baton Rouge Metropolitan) | 2002 | 1 | Oförändrat. |
| LRD (Laredo International) | 2006 | 1 | + retrofit 2012 enligt 2016. |
| SAN (San Diego International) | 2006 | 1 | Oförändrat. |
| Smith Reynolds (INT) | 2010 | 1 | Oförändrat. |
| New Castle County (ILG) | 2010 | 1 | Oförändrat. |
| Republic/Farmingdale (FRG) | 2011 | 2 | Växte från 1 planerat system (2011) till 2 (andra tillagt 2013, enligt 2016). |
| Augusta State (AUG) | 2011 | 2 | 2011 = kontrakt (höst 2011), 2016 = bekräftat klart exakt som planerat. |
| Groton-New London (GON) | 2011 | 2 | 2011 ger fasad tidslinje (sommar 2011/höst 2012) - primärkälla här trots att den är äldre. 2016 bekräftar båda klara. |

**Dutchess County (Poughkeepsie, NY) – genuint ny flygplats**, fanns inte
alls i databasen. Skapad med `install_year=2004`, 1 system,
GA-flygplats (markerad "**" i båda dokumenten).

## Bifynd utanför den explicita listan: ELM

Elmira-Corning (ELM) fanns redan med en Installation utan `install_year`
(från förra sessionens FAA FY2011-bidragsfynd, som medvetet **inte**
satte ett årtal – ett bidragsår är inte samma sak som ett
färdigställandeår). 2016 Fact Sheet bekräftar oberoende
**"Elmira-Corning, Elmira, NY, 1, 2012"** – löser precis den öppna
frågan. `install_year` satt till 2012 direkt på den befintliga raden
(inte en ny rad, eftersom det bara är en enkel årtalsbekräftelse, inte en
ny separat installationshändelse); `source_id` orört (fortfarande
FAA-kartan, som är mest specifik om var bädden ligger).

## Ej berört: resten av dokumentens innehåll

Båda Fact Sheets innehåller betydligt fler flygplatser än vad som
efterfrågades (t.ex. Little Rock, LaGuardia, Boston Logan, Newark,
Worcester, Key West, Telluride, Kansas City Downtown, Trenton-Mercer,
San Francisco, Reagan National, m.fl., plus 2016:s separata
"under contract"-lista med DeKalb/Peachtree, Lafayette, Venice, Boca
Raton). Inget av detta är skrivet till databasen – utanför uppdragets
explicita lista. Kan vara värt en egen framtida session, särskilt
eftersom flera av dessa redan finns i vår databas med årtalslösa
generiska FAA-poster (samma mönster som denna utrednings 17 uppdaterade
rader).

## Verifiering

- `scripts/import_faa_fact_sheets_2011_2016.py` kört: 1 ny flygplats
  (Dutchess County), 2 nya källor, 17 nya Installation-rader, ELM
  uppdaterad.
- `scripts/export_static_site.py --output site` kört om utan fel.
- Hela testsviten (255 tester, inkl. 7 nya) grön.

## Metodbegränsningar

- 2016-dokumentets sidor lästes via bildvision, inte maskinell
  textextraktion – transkriberingen är noggrant avstämd mot varje
  tabellrad, men en efterföljande manuell dubbelkoll mot de sparade
  PDF-sidorna (`docs/sources/2016-02-04_...pdf`) rekommenderas om
  siffrorna någonsin ifrågasätts.
- Datumens ålder är betydande (10-15 år) – flera "additional projects
  under contract"-poster från 2011 kan sedan 2016 (eller sedan dess) ha
  ersatts/byggts ut ytterligare; se respektive rads notes för vilket
  dokument som bekräftade vad.
- Ingen av de icke-efterfrågade flygplatserna i dokumenten är
  kontrollerade mot databasen - se avsnittet ovan.
