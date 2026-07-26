# Utredning: uppföljning på svaga poster (BGM, MSP, VPC m.fl.)

Ren research – ingen kod, mall eller databas har ändrats. Uppföljning på
2026-07-26-utredningen (`docs/utreding_status_flygplatser.md`), med fokus
på de flygplatser som fick Medel/Låg konfidens eller "installationsår
okänt". Metod: webbsökning + riktade träffar på airportimprovement.com
(som gav ett starkt, primärkälle-nära fynd för LEX i förra sessionen),
FAA:s egna AIP-bidragshistorik-PDF:er (hämtade och lästa direkt med
pdfplumber, inte bara sökmotorns sammandrag) samt flygplatsers egna
officiella dokument (t.ex. Airport Master Plan Updates).

**Huvudfynd i korthet:**
- **BGM** – stort genombrott. "Installationsår okänt" gällde bara den
  *ursprungliga* 2002-installationen; hittade primärkälla (FAA:s egen
  bidragshistorik) för en $12,3M ersättning 2011-2012, **och** upptäckte
  att flygplatsens egen 2021 Airport Master Plan Update redan
  bekräftar ett pågående "Runway 16 EMAS"-projekt (2021-2028, ~$10,9M)
  som matchar våra befintliga signaler nästan krona för krona.
- **MSP** – fortfarande olöst trots bred sökning (FAA-grant-PDF:er,
  airport-technology.com, Runway Safe-referenser). Negativt resultat
  dokumenterat, konfidens oförändrad Medel.
- **VPC** – delvis nedgraderat. Hittade projektbekräftelse (två
  entreprenörssidor) för 2021-arbetet, men kunde **inte** verifiera
  påståendet "EMAS vid båda ändar" mot en spårbar primärkälla – vår
  egen FAA-post har heller inget `runway_end`-värde. Konfidens för
  "båda ändar" bör alltså betraktas som Låg, inte Medel-Hög.
- **LEX** – redan löst i en tidigare session (se
  `scripts/update_lex_emas_details.py`, körd 2026-07-26): inte längre
  en öppen fråga, nämns här bara för spårbarhet.
- Övriga svaga poster (FTY, SUA, BKL, GMU bana 19, RDG, MMU, AGC, HYA,
  CRQ): riktad airportimprovement.com-sökning gav inga nya träffar
  utöver vad förra utredningen redan hittade – ingen ändring i
  konfidens för dessa.

---

## BGM — Greater Binghamton Airport

### Vad som var oklart

Förra utredningen: "Hög (existens), okänt (år)" – två bäddar (16, 34)
bekräftade via FAA Chart Supplement, men inget installationsår.

### Vad som hittades nu

**Ursprunglig installation (2002):** Wikipedia anger att bana 16/34
förkortades till 7 100 fot 2002 för att ge plats åt EMAS. Ingen
oberoende primärkälla hittad för själva 2002-årtalet (samma
begränsning som förra gången) – behåll Medel konfidens för just detta
årtal.

**Första ersättningen (2011-2012) – nu Hög konfidens, primärkälla
hittad:** FAA:s egen bidragshistorik-PDF för FY2011
(`fy2011-aip-grants.pdf`, hämtad och läst direkt) innehåller raden:

> EA NYC NY BGM Greater Binghamton/Edwin A Link Field P 61
> $12,312,938 ($1,280,136 entitlement + $11,032,802 discretionary) –
> "Improve Runway Safety Area [Improvements to Runway 16-34 Runway
> Safety Areas (Construction)] - 16/34"

Beloppet ($12,3M) matchar exakt Wikipedias uppgift om "$12.3 million
federal grant in September 2011" (rapportdatum på PDF:en: 2011-11-30,
FY2011). Wikipedia anger att projektet ersatte det gamla 2002-systemet
och förlängde banan till 7 304 fot, klart november 2012.

**Ett andra, separat och redan pågående projekt (2021-2028) – ny,
viktig upptäckt:** BGM:s egen 2021 Airport Master Plan Update
("8-BGM-AMPU-Financial-Feasibility.pdf", McFarland Johnson, hämtad och
läst direkt) listar i sin kapitalbudget (Tabell 8-1):

> Short Term (2021-2022): "Runway 16 EMAS – Design" $500 000
> Phase II (2023-2028): "Runway 16 EMAS – Construction Phase I"
> $7 425 000, "Runway 16 EMAS – Construction Phase II" $3 000 000
> (totalt ca $10,9M, 90 % FAA / 5 % delstat / 5 % lokalt)

Det här är **inte samma projekt** som 2011-2012-ersättningen – det är
ett tredje, nyare steg specifikt för bana 16, med design 2021-2022 och
konstruktion utsträckt till 2028.

**Matchning mot vår egen databas (stark korsverifiering):** BGM har
redan `Signal 6` ("Runway 16 departure EMAS project", `category=
new_installation`, `confidence=programmed`) plus fem
USAspending-bidragssignaler:

| Signal | Belopp | FAA-år |
|---|---|---|
| id 59 | $481K | FY2021 |
| id 49 | $5,4M | FY2023 |
| id 55 | $1,6M | FY2023 |
| id 58 | $1,0M | FY2023 |
| id 60 | $415K | FY2026 |

FY2021-beloppet ($481K) ligger mycket nära AMPU:s "Design"-post
($500K, varav $450K FAA-andel). De tre FY2023-posterna summerar till
$8,0M, vilket ligger nära AMPU:s "Construction Phase I" ($7,425M,
$6,683M FAA-andel). FY2026-posten ($415K) passar som en sen
finjusteringspost inför/under Phase II. Med andra ord: databasens
befintliga signaler var redan rätt spårade – den här utredningen ger
dem bara en tydlig, namngiven, officiell källa (AMPU:n) att stå på
istället för att bara vila på råa USAspending-belopp.

**Ingen bekräftelse hittades** på att Runway 16 EMAS-projektet är
fysiskt klart än (sökningar på 2023-2025-nyheter gav bara BGM:s
$32-47,8M **terminal**-moderniseringsprojekt, ett helt separat
initiativ). Rimligt givet tidslinjen (konstruktion till 2028).

### Rekommendation

Ingen kodändring gjord här, men om/när databasen uppdateras är detta
ett bra tillfälle att koppla `Signal 6` och de fem USAspending-
signalerna till en gemensam `Source`-rad för AMPU:n (`
https://binghamtonairport.com/wp-content/uploads/2023/01/8-BGM-AMPU-Financial-Feasibility.pdf`),
och eventuellt sätta `target_year=2028` på `Signal 6`.

---

## MSP — Minneapolis-St Paul International

### Vad som var oklart

"Installationsår ej bekräftat" – EMAS på avgångsänden av bana 12R
(160×216 ft), källa bara Runway Safes egen referenssida.

### Vad som gjordes

Sökte brett: FAA:s AIP-bidragshistorik (ingen träff för MSP med
"EMAS"/"arresting" i beskrivningen i de PDF:er som kontrollerades),
airport-technology.com:s projektsida (nämner inte EMAS alls),
runwaysafe.com-referensen (samma information som redan fanns – inget
årtal), samt riktade sökningar på "EMAS 30L installed 2005/2006/2008"
och FAA Part 139-historik. Ingen av dessa gav ett årtal.

### Resultat

**Fortfarande olöst.** Detta är nu den svagast belagda posten av de
tre prioriterade (svagare än BGM och VPC), eftersom flera oberoende
sökvägar gett noll träffar snarare än ett osäkert/motsägelsefullt
fynd. Konfidens oförändrad: Medel (installation bekräftad genom FAA
Chart Supplement/Runway Safe, år okänt).

**Förslag för en framtida session:** kontakta Metropolitan Airports
Commission direkt, eller sök i MSP:s egna Airport Capital Improvement
Plan-dokument (samma typ av dokument som löste BGM-frågan här) – MAC
publicerar liknande kapitalbudgetar på metroairports.org.

---

## VPC — Cartersville Airport

### Vad som var oklart

"Osäkert om båda banändar eller bara en har bädd" – EMAS sedan 2021,
banstängning 31 maj–19 juni 2021.

### Vad som hittades nu

Två projektsidor bekräftar att en EMAS-installation var del av
2021-banrenoveringen:

- **C.W. Matthews** (entreprenör): "In May 2021 we received the go
  ahead to break ground on the Cartersville Airport Runway profile
  correction, EMAS pad installation & all new lighting and signage
  contract" – 21 dagars banstängning. Anger **inte** vilken/vilka
  banändar.
- **Croy Engineering** (projekterande ingenjörsfirma): bekräftar att
  "EMAS-systemen designades tillsammans med" ett
  banbeläggnings-/RSA-projekt, och att en omdesign av dagvattensystemet
  gav $1,5M i besparingar som möjliggjorde RSA-/EMAS-arbetet. Anger
  inte heller explicit "båda ändar" i en direkt citerbar mening.

En tidigare sökning (denna session) gav en sammanfattning som
påstod "the City of Cartersville was able to justify to GDOT and the
FAA the construction of an EMAS bed at each end of the runway" – men
detta gick **inte** att spåra till en verifierbar primärkälla vid
uppföljning: Cartersville Airports egen Wikipedia-sida nämner inte
EMAS alls, och AirNav:s bansida (KVPC) nämner inte heller EMAS.

### Resultat

**Nedgradering, inte uppgradering.** Påståendet om "båda ändar" kunde
inte bekräftas mot en spårbar källa denna gång – och vår egen
databaspost (Installation id 23, FAA-källa) saknar `runway_end`-värde,
till skillnad från t.ex. BGM/STP/MKC som har explicita
"båda ändar"-noteringar från samma FAA-källa. Rekommendation: sätt
konfidensen för "båda ändar vid VPC" till **Låg** tills en primärkälla
(FAA Chart Supplement, NOTAM, eller flygplatsens egen Master Plan)
hittas som anger runway ends explicit. Installationsåret 2021 i sig
kvarstår väl belagt (två oberoende projektsidor, samma tidsfönster som
databasens befintliga notering).

---

## LEX — redan löst (spårbarhetsnotering)

Förra utredningens post för LEX ("installationsår okänt, svagaste
belagda posten av de 39") är **inte längre öppen** – i en tidigare
session (2026-07-26, samma dag) hittades och verifierades en
Airport Improvement-artikel (nov 2021) som bekräftar install_year=2022,
confirmed_vendor="Runway Safe" och projektekonomin. Se
`scripts/update_lex_emas_details.py` och dess körning tidigare denna
session. Nämns här bara så att den här rapporten är fullständig mot
den ursprungliga listan av svaga poster.

---

## Övriga svaga poster: riktad airportimprovement.com-kontroll

Utöver BGM/MSP/VPC gjordes en lättare sökpassage
(`site:airportimprovement.com EMAS <flygplats>`) mot resten av
Medel/Låg-listan: FTY, SUA (Witham Field), BKL (Burke Lakefront), GMU
(bana 19-ersättningen), RDG, MMU, AGC (Allegheny County), HYA (Cape
Cod Gateway), CRQ (McClellan-Palomar).

**Inga nya träffar** utöver vad förra utredningen redan hittade eller
vad som redan är känt:

- **PDK (DeKalb-Peachtree)** dök upp i sökningen (redan Hög konfidens,
  ingen ändring) – artikeln "New Arrestor Bed at DeKalb-Peachtree
  Signals Industry Change" nämner i förbigående ett forskningsbesök i
  Greenville (GMU), men gav ingen ny information om GMU:s bana
  19-status.
- Övriga (FTY, SUA, BKL, RDG, MMU, AGC, HYA, CRQ) gav inga
  airportimprovement.com-artiklar alls i sökresultaten – antingen
  saknar tidningen täckning av dessa mindre flygplatser, eller är
  artiklarna inte indexerade för sökning på det sättet.

Ingen konfidensändring för någon av dessa nio.

---

## Bifynd (ej en del av uppdraget, men värt att notera)

Samma FAA FY2011-bidragshistorik-PDF som löste BGM-frågan innehåller
även en rad för **Elmira/Corning Regional (ELM), NY**: "Extend Runway
(401Ft) of Runway 24 and Parallel Taxiway A Including Purchase and
Installation of EMAS Blocks for RSA" – $8 556 627 ($7,14M
discretionary). Om ELM inte redan finns i vår databas med en
EMAS-installation kan detta vara en ny kandidat att undersöka i ett
separat, framtida steg (samma mönster som Venice/VNC-fyndet i förra
utredningen).

## Metodbegränsningar

- FAA:s AIP-bidragshistorik-PDF:er är genomsökta sida för sida med
  pdfplumber för de årtal som kontrollerades (FY2010-2012); äldre eller
  nyare årtal (t.ex. det ursprungliga BGM-2002-beloppet, eller
  eventuella MSP-bidrag) är inte uttömmande genomsökta – tidskrävande
  att göra manuellt för samtliga ~20 årtal FAA publicerar.
- WebSearch-verktygets sammanfattningar visade sig minst en gång
  (VPC "each end"-påståendet) innehålla text som inte gick att spåra
  till en verifierbar primärkälla vid uppföljning – behandlat som
  obekräftat snarare än falskt, men en påminnelse om att verifiera
  sammanfattningar mot den faktiska sidan innan de tas för sanning.
- Sökningen är ett ögonblicksfynd (2026-07-26/27).
- Ingen fil, mall eller databasrad har skrivits till eller ändrats som
  en del av detta arbete – rent research- och rapportarbete enligt
  instruktion.
