# Utredning 2026-07-26

Ren utredning – inget kodat eller ändrat i databasen. Tre separata frågor,
rapporterade i ordning. Punkt 1 och 2 hänger ihop (lösningen på 2 beror på
statusen i 1); punkt 3 är fristående.

---

## 1. Signal→Installation-luckan: nuvarande tillstånd för WLG och ZQN

Kollat direkt i `data/runway_safe.db`. De två flygplatserna befinner sig i
**varsin motsatt hälft** av samma lucka:

### WLG (Wellington) – finns bara som Signal, ingen Installation

| Fält | Värde |
|---|---|
| Signal.id | 65 |
| category | `new_installation` |
| **status** | **`None`** |
| confidence | `high` |
| confirmed_vendor | `Runway Safe` |
| source_id | 50 – Wellington Airports egen sida, `source_type=news`, publicerad 2026-03-25 |
| Installation-rad | **finns inte** |

Notes-fältet innehåller redan (från tidigare sessioner) forskning som visar
fysisk installation klar mars 2026, och nu (från förra sessionen) den
officiella sidans bekräftade siffror (+143 m landning, +37 m start). Källan
och texten säger med andra ord "det här är klart" – men signalen ligger
kvar med `category=new_installation`, `status=None`, som om det fortfarande
vore en öppen order. Det är precis den bild du beskriver: "Ny installation,
status –".

### ZQN (Queenstown) – finns bara som Installation, ingen Signal

| Fält | Värde |
|---|---|
| Installation.id | 72 |
| type | `EMAS` |
| **status** | `active` |
| install_year | 2025 |
| confirmed_vendor | `Runway Safe` |
| source_id | 49 – Queenstown Airports pressmeddelande, `source_type=news`, klart 2025-03-12 |
| Signal-rad | **finns inte, har aldrig funnits** |

ZQN gick aldrig via någon Signal alls. Den skapades direkt som en
`Installation`-rad i `scripts/add_rw_shareholder_letter_signals.py`
(commit `73f3106`), eftersom källan (aktieägarbrevet) redan då antydde att
den skulle vara klar H1 2025 – ingen "leverans pågår"-fas fanns att
representera.

### Varför skillnaden uppstod

Grep på `Installation(` i hela kodbasen ger bara tre träffar:
`scripts/add_brazil_expansion.py` (CGH), `scripts/add_rw_shareholder_letter_signals.py`
(ZQN) och `scripts/import_faa_csv.py` (den stora Tableau-CSV:n, som
sannolikt står för merparten av alla amerikanska Installation-rader,
inklusive MDW). `scripts/import_faa_runway_ends.py` (NASR/APT_ARS) **skapar
aldrig** nya Installation-rader – den bara berikar `runway_id`/`runway_end`
på rader som redan finns.

Slutsats: **det finns ingen kodväg någonstans i projektet som tar en
befintlig Signal och producerar en motsvarande Installation-rad.** Alla tre
skapelseställena är ursprungsimporter (bulk-CSV, Brasilien-expansion, ett
direkt ZQN-inlägg) – ingen av dem är en "gradueringsprocess" från Signal.
Din beskrivning stämmer exakt: ingen etablerad process finns, och WLG är nu
i praktiken det första konkreta exemplet på att luckan biter.

Sidoiakttagelse, samma symptom fast mildare: fyra signaler i hela databasen
har `status=None` – SDU (64), **WLG (65)**, CLT (66), MSP (67) – alla från
samma skript (`add_rw_shareholder_letter_signals.py`). Det skriptet satte
aldrig något statusvärde alls för order-signalerna. Inte i sig samma
problem som Signal→Installation-luckan, men bidrar till samma
"limbo"-känsla och är värt att komma ihåg när lösningen i punkt 2 utformas
(en bra lösning bör rimligen sätta *någon* status även vid vanligt
skapande, inte bara vid "graduering").

---

## 2. Förslag: generell Signal→Installation-övergång (rapport, ej byggd)

### Idé

1. **Nytt, dokumenterat statusvärde**: `Signal.status = "completed"` (fritext
   redan idag, som `aip_grant`/`iija_grant` på `Source.source_type` – inget
   schema-/enum-tvång behövs).
2. **Litet, manuellt skript** – t.ex. `scripts/graduate_signal_to_installation.py`
   – i samma stil som `scripts/attach_source_to_signal.py` och
   `scripts/add_zqn_wlg_official_source_confirmation.py`:
   - `--signal-id` (obligatoriskt, en signal i taget – **medvetet manuellt**,
     se resonemang nedan).
   - Skapar en ny `Installation`-rad: `airport`/`runway` från signalen,
     `source` = signalens nuvarande `source_id` (den starkaste källan),
     `confirmed_vendor` kopieras, `notes` kopieras (bevarar hela
     källkedjan), `status="active"`.
   - `type` (EMASMAX/greenEMAS/EMAS) och `install_year` går inte att
     härleda säkert ur en Signal idag – måste ges som flaggor
     (`--type`, `--install-year`) eller läsas manuellt ur `notes` av den
     som kör skriptet.
   - Sätter `Signal.status = "completed"` (signalen tas *inte* bort – följer
     samma "inget raderas, bara avlänkas/markeras"-princip som redan gäller
     för gamla `Source`-rader vid `attach_source`).
3. **Valfri men rekommenderad utökning**: en ny nullable kolumn
   `Signal.installation_id` (FK till `installations.id`), tillagd via samma
   idempotenta `ensure_*_column()`-mönster som redan finns för
   `confirmed_vendor`/`external_id`. Ger en explicit länk att rendera
   ("→ Se installation") istället för att bara lita på airport+datum-gissning.
4. **UI**: `_CATEGORY`/statusmappningen i `app/static_export/build.py` och
   signal-listans/detaljvyns mallar behöver en distinkt, "avslutad"-stil för
   `status=completed` (nedtonad pill, inte samma look som en öppen order) –
   annars ser en graduerad signal bara ut som en trasig/okänd status.

### Varför manuellt, inte automatiskt

Att avgöra om en källa **verkligen** bekräftar fysisk färdigställning
(kontra "order signerad", "leverans planerad", "80 % klar") är precis den
typ av tolkning projektet redan medvetet håller manuell på andra ställen –
jämför regel 2 i PLAN_FORENKLING.md (`add_source_and_flag_keywords`): en
nyckelordsträff skapar bara en `confidence=low`-kandidat som väntar på att
läsas och höjas manuellt, aldrig en automatisk sanning. Samma princip bör
gälla här: ett skript som *kan* köras på en signal i taget, inte ett som
letar igenom alla signaler och gissar vilka som är "klara".

### Direkt tillämpbart om förslaget godkänns

- **WLG (signal 65) är redo att graduera nu** – källan bekräftar redan
  fysisk färdigställning.
- **ZQN kräver ingen åtgärd** – finns redan som Installation, har aldrig
  varit en Signal, så det finns inget att migrera bakåt.

Detta är ett förslag, inte en plan i sig – vill du ha en riktig Plan (med
migrationssteg, testtäckning etc.) för att bygga det här, säg till så
växlar jag till planläge.

---

## 3. PWK-radgruppering (rapport, ej byggd)

### Rotorsak – datan är korrekt, presentationen är inte

PWK (Chicago Executive) har tre riktiga incidenter i `incidents`-tabellen
(2016-01-01, 2021-02-01, 2025-09-01), alla `incident_type=EMAS activation`.
`app/models/incident.py:68-89` har en SQLAlchemy `after_insert`-event som
**medvetet** skapar en Signal per Incident (regel 1: "Every insert
automatically creates a matching high-confidence Signal – no manual review
step"). Det är korrekt, dokumenterat beteende – tre incidenter ska ge tre
signaler. Titlarna blir nästan identiska, bara datumet skiljer:

- "Chicago Executive — EMAS-ersättning väntas efter incident (2016-01-01)"
- "Chicago Executive — EMAS-ersättning väntas efter incident (2021-02-01)"
- "Chicago Executive — EMAS-ersättning väntas efter incident (2025-09-01)"

`app/static_export/templates/signals_list.html` renderar en `<tr>` per
signal med noll grupperingslogik, och `build_site()`/`_signal_view()` i
`app/static_export/build.py` sorterar bara på `probability_score` – ingen
gruppering finns någonstans i export-pipen idag.

### Omfattning – inte ett PWK-specifikt problem

Sökte på `(airport_id, category)` med fler än en signal, över hela
databasen:

| Flygplats | Kategori | Antal |
|---|---|---|
| BGM | replacement | 5 |
| ORH | replacement | 5 |
| BOS | new_installation | 4 |
| JFK | replacement_after_incident | 3 |
| **PWK** | **replacement_after_incident** | **3** |
| MHT | replacement | 2 |
| HYA | replacement | 2 |
| TEB | replacement_after_incident | 2 |
| BUR | replacement_after_incident | 2 |
| EYW | replacement_after_incident | 2 |
| LEX | new_installation | 2 |
| MSP | replacement | 2 |
| ROA | replacement | 2 |

13 grupper totalt. En generell lösning (gruppera per flygplats+kategori),
inte en PWK-quickfix, är alltså rätt omfattning – matchar också hur du
formulerade uppdraget.

### Föreslagen lösning (rapport, ej byggd)

- I `build_site()`: gruppera `signal_views` på `(airport_id, category)` till
  en ny vymodell, t.ex. `signal_group` – `count`, högsta confidence/senaste
  score i gruppen (för sortering/badge), plus listan av de underliggande
  (oförändrade) signal-vyerna.
- Grupper med `count == 1` renderas exakt som idag – en helt vanlig rad,
  inget beteende ändras för normalfallet.
- Grupper med `count > 1` renderas som **en rad** med en räknar-badge
  (t.ex. "3 signaler") och en `<details>/<summary>` (ren HTML, ingen
  byggkedja – i linje med README:s "ingen Tailwind/Node-byggkedja") som vid
  klick expanderar och visar varje enskild signal-rad nedanför, precis som
  idag.
- **Ingen ändring av underliggande data** – detta är enbart en
  presentations-/vy-lager-ändring i `app/static_export/build.py` +
  `signals_list.html`. Noll ändringar i `app/models`, `app/services` eller
  något importskript.
- Sök-/filter-JS i `signals_list.html` behöver en liten justering så att en
  sökträff i en undanfälld barnrad expanderar/visar sin föräldragrupp –
  flaggar det som en implementationsdetalj att göra rätt, inte bara en
  mallfråga.

---

## Sammanfattning – vad väntar på beslut

1. **WLG/ZQN-status rapporterad** ovan – inget ändrat.
2. **Signal→Installation-gradueringsförslag** – redo att byggas om du
   godkänner riktningen (manuellt skript + nytt statusvärde
   `completed`, ev. `Signal.installation_id`-kolumn).
3. **PWK-gruppering** – redo att byggas som en ren presentationsändring
   (gruppera på flygplats+kategori, expanderbar rad) om du godkänner
   ansatsen.

Inget av detta är byggt än. Säg till vilket/vilka du vill gå vidare med.
