# Utredning 2026-07-26, del 4 – gruppering ombyggd enligt mockup + unika USAspending-titlar

Uppföljning av del 2/3. Två beställningar, båda byggda mot `grouping_mockup.html`
(hittades i repo-roten efter att den lagts till där) och verifierade visuellt
med Playwright mot den faktiskt exporterade sajten (inte bara enhetstester).

---

## 1. Grupperingen ombyggd enligt mockupen

### Vad som ändrades jämfört med förra versionen (del 2)

Förra byggets gruppering renderade varje medlem som en egen, indragen
`<tr>` under en generisk samlingsrad ("N signaler – Kategori"). Mockupen
efterfrågade två specifika ändringar, båda genomförda exakt:

1. **Grupprubriken visar nu den högst rankade signalens riktiga titel**,
   stylad identiskt med en vanlig signal-länk (samma `--accent`-gula färg/
   vikt som alla andra rader – ingen egen CSS-nyansering behövdes, den ärver
   redan sajtens globala `a { color: var(--accent) }`). En liten dämpad
   `+N till`-tagg (`--text-faint`) bredvid är det enda som avslöjar att det
   är en grupp.
2. **Expanderat innehåll är EN inset-panel**, inte fler bord-rader: en
   `<tr class="detail-row">` med `colspan="7"` vars enda cell innehåller
   `.detail-wrap` → `.detail-panel` (vänsterkant `border-left:3px solid
   var(--accent)`) → en `.strip` (flex-rad) per signal. Flygplats/Kategori
   upprepas inte i varje strip (redan givet av gruppen).

### Implementation

- **`app/static_export/build.py`**: `_group_signal_views()` förenklad –
  `members[0]` *är* toppsignalen (en delsekvens av en redan poäng-sorterad
  lista är själv sorterad, så inget separat max()-steg behövs). Ny
  `more_count = len(members) - 1`.
- **`app/static_export/templates/_components.html`**: `signal_row()`
  återställd till att bara rendera fristående rader (ingen `data-group`/
  `css_class`-komplexitet kvar); ny `signal_strip(signal, root)`-makro för
  panel-innehållet.
- **`signals_list.html`**: hela raden är nu klickbar (`tr.grouprow`,
  `cursor:pointer`), ingen separat knapp. JS förenklad radikalt jämfört med
  förra versionen – eftersom medlemmar inte längre är egna `<tr>`:n behöver
  filtret bara visa/dölja **grupprad + dess EN detail-rad tillsammans**,
  ingen "platta ut grupper vid sökning"-logik kvar (den logiken behövdes
  bara för den gamla per-rad-modellen).
- **`style.css`**: `.grouprow`/`.caret`/`.grouptitle`/`.more-tag`/
  `.detail-row`/`.detail-wrap`/`.detail-panel`/`.strip` – värden kopierade
  rakt av från mockupen (samma CSS-variabelnamn används redan i
  produktionsstilen, `--panel2`/`--border`/`--accent`/`--text-faint` m.fl.,
  eftersom mockupen byggdes mot samma designtokens).

### Verifierat visuellt med Playwright

Körde headless Chromium mot den faktiskt exporterade `site/signals/index.html`
(inte en mock):

```
PWK grouptitle: Chicago Executive — EMAS-ersättning väntas efter incident (2016-01-01)
PWK more-tag: +2 till
BOS grouptitle: Runway 9/27 RSA and EMAS phase 2
BOS more-tag: +3 till
PWK detail open class present: True
PWK strip count: 3
BOS strip count: 4
PWK row hidden while searching '2016': False   (rätt — en medlem matchar)
BOS row hidden while searching '2016': True    (rätt — ingen medlem matchar)
```

Skärmdumpar (hopfälld hel sida, PWK inzoomad, allt expanderat) bekräftar:
grupptiteln är visuellt omöjlig att skilja från en vanlig signal-länk förutom
den dämpade `+N till`-taggen; expanderat innehåll är en enda inset-panel med
gul vänsterkant, precis som mockupen.

**Notis om "högst rankad":** PWK:s tre incident-signaler har alla samma
`probability_score=8.0` (default för `confidence=high`, se
`DEFAULT_SCORE_BY_CONFIDENCE`). Vid oavgjort avgör den redan existerande
stabila sorteringen (`probability_score` fallande) genom ursprunglig
frågeordning (id-stigande) – här blev det 2016-incidenten, inte den senaste
(2025). Detta är **samma sortering som redan används för score**, precis
som efterfrågat – ingen ny tie-break-regel lades till. Flagga till mig om ni
istället vill bryta oavgjort på nyaste år/incident-datum.

### Tester

`tests/test_static_export.py` uppdaterad: kontrollerar `grouptitle`-texten
= toppsignalens riktiga titel, `+N till`-taggen, att alla medlemmar finns
som `.strip`-element (inte `.group-child`-rader), och att en ensam signal
inte får någon `.grouprow`/`.detail-panel` alls.

---

## 2. Unika titlar för USAspending-genererade signaler

### Problemet

`scripts/import_usaspending_grants.py` gav varje bidrag samma titel:
`"USAspending grant: {mottagare} EMAS"` – identisk för alla bidrag till
samma flygplats (mottagaren, dvs flygplatsägaren, ändras inte mellan
bidrag). I den nya expanderade panelen hade detta gjort BOS:s tre
USAspending-strips olästbart identiska.

### Fix

- **`scripts/import_usaspending_grants.py`**: ny `signal_title(grant)` +
  `_format_amount()` – format `"USAspending grant — ${belopp}, FY{år}"`
  (`$56.2M`, `$9.0M`, `$60K` – skalar automatiskt M/K/rått belopp). Gäller
  alla framtida importer.
- **`scripts/rename_usaspending_signal_titles.py`** (nytt, engångsskript):
  bakåtkompatibel namnbyte av de 25 redan existerande
  `usaspending_grant`-signalerna, med `estimated_total_value_usd`/
  `planning_year` som redan låg lagrat på varje Signal-rad – inget
  återhämtande från USAspending.gov behövdes. Säkert att köra om
  (hoppar över redan omdöpta titlar via prefix-check).

### Körning mot skarp databas

```
python -m scripts.rename_usaspending_signal_titles
{'renamed': 25, 'already_renamed': 0}
```

Exempel, BOS (tre bidrag som tidigare alla hette
"USAspending grant: Massachusetts Port Authority EMAS"):

- `USAspending grant — $56.2M, FY2025`
- `USAspending grant — $9.0M, FY2026`
- `USAspending grant — $60K, FY2023`

### Tester

7 nya tester: `_format_amount`/`signal_title` (skalning M/K/rått,
unikhet mellan två bidrag till samma mottagare, saknat startdatum) i
`tests/test_import_usaspending_grants.py`; `rename_titles()` (byte,
orörda icke-USAspending-signaler, idempotens) i
`tests/test_rename_usaspending_signal_titles.py`.

---

## Testkörning och export

```
pytest -q
229 passed
```

(upp från 223 – 10 nya tester: 3 för titel-helpers, 3 för
rename-skriptet, 4 justerade/nya i test_static_export.py för den
ombyggda grupperingen.)

`python -m scripts.export_static_site --output site` kördes rent efter
kodändringarna, sedan verifierad visuellt med Playwright enligt ovan.

## Databasändringar

- Backup tagen till scratchpad innan `rename_usaspending_signal_titles`
  kördes.
- Enda skrivningen mot skarp data: 25 Signal-titlar omdöpta (samma
  format som framtida importer nu genererar automatiskt). Inget annat
  ändrat, inget raderat.
