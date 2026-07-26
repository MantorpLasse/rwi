# Implementation: förslagen från docs/utreding_faa_tableau.md

Databasändring. Genomför förslagen från `docs/utreding_faa_tableau.md`.
Skript: `scripts/import_faa_tableau_gaps.py` (idempotent, testat, 7
tester). Alla sex flygplatser fick en ny, separat, daterad
Installation-rad (gamla `faa_tableau`-raden orörd) – samma mönster som
alla tidigare fact-sheet-importer.

## Genomfört

| Kod | install_year | Leverantör | Källa | Notis |
|---|---|---|---|---|
| OXC | 2018 | – | Simple Flying (aggregator) | Medel konfidens, ingen starkare källa hittad |
| CGF | 2018 | – | Cuyahoga County pressmeddelande | **2 rader**: bana 06 (322 ft) och bana 24 (435 ft) |
| HXD | 2018 | – | Airport Improvement Magazine | 2019-diskrepans noterad, olöst |
| PHL | 2025 | Runway Safe | phl.org (flygplatsens egen sida) | Matchar befintlig USAspending-signal |
| PDK | 2018 | **Zodiac Aerospace** | Airport Improvement Magazine | Ej Runway Safe/ESCO – se nedan |
| BCT | 2016 | Runway Safe | bocaairport.com (flygplatsens egen sida) | Uppgraderar "efter 2012" till konkret år |

**VNC lämnad orörd** – inget hårt årtal hittades trots ytterligare
sökning (se nedan), precis som instruerat.

## Detaljer per flygplats

**OXC (Waterbury-Oxford):** Inget nytt utöver vad förra rapporten redan
hittade. CT Airport Authority's egen Master Plan Update gick fortfarande
inte att hämta (HTTP 403).

**CGF (Cuyahoga County):** Två separata rader eftersom källan (countyts
pressmeddelande, korsrefererat mot en sökmotorsammanfattning av
countyts "Runway 6/24 Safety Area Improvement Program"-översikt) anger
specifika, olika bäddlängder per banände – samma modell som redan
används för STP/MKC:s "båda ändar"-poster.

**HXD (Hilton Head):** Airport Improvement Magazine anger konkret
"slutet av juni 2018". 2019-uppgiften (en svagare, ospecificerad
aggregator-källa från förra rapporten) kunde inte spåras till en
primärkälla – flaggad i notes som olöst, inte tyst borttagen.

**PHL (Philadelphia):** Använde flygplatsens egen nyhetssida
(`phl.org/newsroom/EMAS`) som källa – det starkaste av de tre redan
citerade alternativen (PHL Airport/6abc/AirlineGeeks). Bekräftar exakt
kostnad ($8 547 648) som matchar den redan befintliga USAspending-
signalen (id 43, $8,5M).

**PDK (DeKalb-Peachtree):** Hittade och läste artikeln direkt ("New
Arrestor Bed at DeKalb-Peachtree Signals Industry Change",
airportimprovement.com). **Viktig rättelse:** leverantören är **Zodiac
Aerospace**, inte Runway Safe/ESCO – artikeln förklarar att Zodiac
lämnade den amerikanska EMAS-marknaden 2018 efter sammanslagningen med
Safran. `confirmed_vendor` satt till "Zodiac Aerospace" istället för
det ospecificerade värde uppdraget antydde.

**BCT (Boca Raton):** `WebFetch` kunde inte läsa
`bocaairport.com/portfolio-items/engineered-materials-arresting-system-emas/`
– verktygets HTML→markdown-konvertering trunkerade sidan till bara
navigeringsmenyn, tre försök i rad. Löste det genom att hämta råsidan
direkt med `curl` och söka igenom HTML:en manuellt – hittade "Budget
$12M" och "Timeline 2016-2017" i sidans faktiska brödtext, plus en
bekräftelse att bäddar finns vid **båda ändar av bana 5/23** och att
leverantören är **"Originally ESCO, now Runway Safe Inc."** Detta är ett
konkret årtalsfynd (`install_year=2016`, start på tidslinjen) – **inte**
"lämna som är" som den ursprungliga rapporten drog slutsatsen av (den
rapporten gav upp efter att WebFetch gav tomt resultat tre gånger).

**VNC (Venice):** Ytterligare sökning gjord (Owen Ames Kimball:s
projektsida, Florida DOT:s flygplatsprofil-PDF, lokala nyhetskällor) –
inget hårt installationsår hittades. Projektsidan ger bara omfattning
(48 000 kvadratfot EMAS, 5 000 fot banrenovering) utan datum. Lämnad
orörd enligt instruktion.

## Verifiering

- `scripts/import_faa_tableau_gaps.py` kört: 6 nya källor, 7 nya
  Installation-rader (CGF fick 2).
- `scripts/export_static_site.py --output site` kört om utan fel.
- Hela testsviten (266 tester, inkl. 7 nya) grön.

## Metodbegränsningar

- BCT-fyndet visar att "kunde inte hämtas" inte alltid betyder "data
  finns inte" – en rå `curl`-hämtning avslöjade innehåll som
  `WebFetch`s konvertering missade. Värt att komma ihåg för framtida
  research: om en sida ger ett misstänkt tomt/trunkerat resultat, prova
  en direkt HTML-hämtning innan slutsatsen "inget hittades" dras.
- HXD:s 2018-vs-2019-diskrepans är fortfarande olöst.
- OXC:s konfidens är fortfarande Medel – CT Airport Authority's AMPU-PDF
  återstår som nästa steg för en starkare källa.
- VNC har fortfarande inget bekräftat installationsår.
