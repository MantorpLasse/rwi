# Utredning: Gadelius greenEMAS-installationslista

Datainmatning (inte ren research – databasen är ändrad). Källa: Gadelius
(Runway Safe Groups japanska licenspartner) officiella produktsida,
hämtad och verifierad 2026-07-26/27:
https://www.gadelius.com/products/license_engineering/greenemas_e.html

Sidan listar en kronologisk lista över greenEMAS-installationer (Runway
Safes produktlinje av greenEMAS/återvunnet-glas-block): 2014 Chicago
Midway, 2016 Zurich International, 2017 Roland Garros (Réunion), 2018
Dzaoudzi Pamandzi International (Mayotte), 2019 Saarbrücken, sep 2019
Tokyo Haneda International, okt 2019 Northolt (UK), aug 2022 CGH
(Congonhas, Brasilien). Alla stämmer mot vad du beskrev i uppdraget.

Skript: `scripts/add_gadelius_greenemas_installations.py` (körbart flera
gånger utan att skapa dubbletter – letar upp flygplatser/källor innan
skapande). Test: `tests/test_add_gadelius_greenemas_installations.py` (7
tester, alla gröna).

## Vad som gjordes

**6 nya flygplatser skapade**, alla med en ny `Installation`
(`type=greenEMAS`, `confirmed_vendor="Runway Safe"`, `source`=Gadelius-
sidan, `status=active`):

| Kod | Namn | Land | Installationsår |
|---|---|---|---|
| ZRH | Zurich International Airport | Schweiz | 2016 |
| RUN | Roland Garros Airport | Frankrike (Réunion) | 2017 |
| DZA | Dzaoudzi Pamandzi International Airport | Frankrike (Mayotte) | 2018 |
| SCN | Saarbrücken Airport | Tyskland | 2019 |
| HND | Tokyo Haneda International Airport | Japan | 2019 (sep) |
| NHT | RAF Northolt | Storbritannien | 2019 (okt) |

**DZA/Mayotte**: notes flaggar explicit att Mayotte är en fransk
utomeuropeisk region i Moçambique-kanalen, inte Madagaskar – troligen
samma flygplats som en tidigare research-pass felaktigt antog låg i
Madagaskar (per ditt uppdrag).

**NHT/RAF Northolt**: notes flaggar att det är en militär/affärsflygplats,
inte kommersiell trafik.

**MDW (Chicago Midway)**: fanns redan (Installation id 26, generisk
FAA-karta, `install_year=None`). Den lämnades orörd. En **ny, separat**
Installation-rad (id 74) skapades: `install_year=2014`, `runway_end=22L`,
`confirmed_vendor=Runway Safe`, källa = Gadelius-sidan (bekräftar året).
Notes kompletterar med detaljerna från PRWeb-pressmeddelandet som redan
fanns i tidigare research: första bädden klar nov 2014 på bana 22L, totalt
fyra bäddar utlovade till slutet av 2016.

**ORD (Chicago O'Hare)**: Gadelius lista nämner *inte* O'Hare alls – bara
Midway. Men samma PRWeb-pressmeddelande om Midways greenEMAS-utbyggnad
säger att O'Hare fick greenEMAS "shortly after", utan exakt datum. Fanns
redan (Installation id 27, generisk FAA-karta, `type=EMASMAX`) – lämnad
orörd. En **ny, separat** Installation-rad (id 75) skapades:
`type=greenEMAS`, `install_year=None` (ospecificerat i källan, precis som
du föreslog), källa = PRWeb-artikeln (inte Gadelius, som inte nämner ORD).

**CGH (Congonhas)**: Gadelius bekräftar oberoende aug 2022 – matchar redan
registrerat `install_year=2022` på den befintliga Installation-raden (id
71, från `scripts/add_brazil_expansion.py`). `type` rättades från
generisk `"EMAS"` till `"greenEMAS"`. Notes fick en tillagd rad (gamla
noten bevarad) som löser den tidigare öppna frågan: SDU:s (planerade)
EMASMAX vs CGH:s greenEMAS är olika produktlinjer – ingen motsägelse om
båda kallas "första" i sina respektive kategorier.

## Källor tillagda

Två nya `Source`-rader (`source_type=news`):
1. Gadelius-sidan (publisher="Gadelius") – används av de 6 nya
   flygplatserna, MDW:s nya rad och CGH:s uppdatering.
2. PRWeb-pressmeddelandet (publisher="PRWeb", publicerad 2015-06-17) –
   används av ORD:s nya rad, citerad i text i MDW:s notes.

## Verifiering

- `scripts/add_gadelius_greenemas_installations.py` kört: 6 nya
  flygplatser, 2 nya källor, 8 nya Installation-rader, CGH uppdaterad.
- `scripts/export_static_site.py --output site` kört om utan fel.
- Hela testsviten (236 tester, inkl. 7 nya) grön.

## Öppna frågor / begränsningar

- Gadelius sida bekräftar bara **att** varje flygplats fick greenEMAS ett
  visst år, inte antal bäddar/banändar (utom för MDW/ORD där PRWeb ger mer
  detalj). Installationsår för ZRH/RUN/DZA/SCN/HND/NHT bör betraktas som
  medel-hög konfidens (en enda, om än officiell, källa) tills en andra
  källa hittas – motsvarande övriga en-källa-poster i databasen.
- RUN/DZA saknar egna flygbolagskonsekvenser/incident-koppling i denna
  körning – ingen signal skapad, bara Installation, eftersom Gadelius
  redan bekräftar att arbetet är utfört (inte en framtida möjlighet).
