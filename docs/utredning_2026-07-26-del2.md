# Utredning 2026-07-26, del 2 – genomförande

Uppföljning av [utredning_2026-07-26.md](utredning_2026-07-26.md). Båda
godkända förslagen är nu byggda, körda mot skarp databas och testade. Detta
körde kod- och schemaändringar (inte bara data), så hela testsviten kördes
efter varje steg enligt instruktion.

---

## 1. Signal→Installation-graduering

### Vad som byggdes

- **`Signal.installation_id`** – ny nullable FK-kolumn (`app/models/signal.py`),
  med idempotent migrationshjälpare
  (`ensure_signal_installation_id_column` i det nya skriptet) för databaser
  som redan finns. `test_model_contract.py` uppdaterad med den nya
  kolumnen/relationen så schemat fortsätter vara skyddat mot oavsiktlig drift.
- **`scripts/graduate_signal_to_installation.py`** – enligt förslaget:
  medvetet manuellt (`--signal-id`, `--type`, `--install-year`), skapar en ny
  `Installation`-rad (kopierar `airport`/`runway`/`source`/
  `confirmed_vendor`/`notes`, `status="active"`), sätter
  `Signal.status="completed"` och `Signal.installation_id`. Vägrar (raise)
  om signalen redan är `completed` – ingen dubblett kan skapas av misstag.
  4 tester, alla gröna.
- **UI**: `status="completed"` får en distinkt grön `pill done`-stil (samma
  ton som `pill vendor`, ny CSS-klass) och etiketten "Färdigställd" istället
  för det råa engelska ordet. Signal-detaljsidan visar "→ Se installation"
  när `installation_id` är satt, länkad till en ny ankare
  (`#installation-{id}`) på flygplatssidans installationskort.

### Körning mot WLG (signal 65)

```
python -m scripts.graduate_signal_to_installation --signal-id 65 --type EMAS --install-year 2026
Signal 65 marked completed -> Installation 73 (airport_id=79, type=EMAS, install_year=2026, source_id=50).
```

Resultat, verifierat direkt i databasen:

| | Före | Efter |
|---|---|---|
| Signal 65 status | `None` | `completed` |
| Signal 65 installation_id | – | `73` |
| Installation 73 | fanns inte | `airport_id=79, source_id=50, type=EMAS, install_year=2026, status=active, confirmed_vendor=Runway Safe` |

`source_id=50` är samma Wellington Airports officiella källa som redan låg
på signalen – kopierades in oförändrad, ingen ny Source skapades.
`install_year=2026` valdes utifrån notisens "fysisk installation klar mars
2026"-uppgift (redan i signalens notes sedan tidigare research).

**ZQN krävde ingen åtgärd** – existerar redan som Installation (id 72), har
aldrig varit en Signal, se del 1.

---

## 2. Signal-listans radgruppering

### Vad som byggdes

- **`app/static_export/build.py`**: ny `_group_signal_views()` grupperar
  `signal_views` på `(airport_id, category)`. Grupper av storlek 1 renderas
  precis som tidigare (`kind="single"`); grupper >1 blir en samlingsrad
  (`kind="group"`, `count`, `best_score` = högsta `probability_score` i
  gruppen) plus samtliga underliggande signal-vyer oförändrade. Ordningen
  följer den befintliga sorteringen (`probability_score` fallande), så en
  grupp dyker upp vid sin bästa medlems rankningsplats.
- **`app/static_export/templates/_components.html`**: ny `signal_row(signal,
  root, css_class, data_group)`-makro – återanvänds för både fristående
  rader och gruppmedlemmar, så de aldrig kan glida isär.
- **`signals_list.html`**: samlingsrad med räknare ("N signaler –
  {kategori}") och en `▸`/`▾`-knapp som via ren vanilla-JS (ingen
  byggkedja) visar/döljer medlemsraderna, indragna under samlingsraden.
  Sök-/status-/landsfiltret uppdaterat: när ett filter är aktivt "plattas"
  grupperna ut (dolda gruppheader, matchande barnrader visas direkt) istället
  för att en sökträff kan gömmas inuti en hopfälld grupp.
- **Ingen ändring av underliggande data** – ren presentationsändring i
  export-lagret. `data.json` innehåller fortfarande exakt en post per Signal.

### Resultat mot skarp data

13 grupper (samma som identifierades i utredningens del 1), 36
gruppmedlemsrader totalt:

```
group-summary count: 13
group-child count:   36
```

PWK (`airport_id=37`) grupperas nu som **"3 signaler – Efter incident"**
under en samlingsrad, `group-toggle` med id `37-replacement_after_incident` –
exakt det ursprungliga önskemålet.

### Tester

6 nya/uppdaterade tester i `tests/test_static_export.py`:
gruppering av tre PWK-liknande signaler, att en ensam signal INTE grupperas,
att `data.json` förblir en post per signal, samt `pill done`/"Se
installation"/ankar-id för en graduerad signal.

---

## Testkörning

```
pytest -q
223 passed
```

(upp från 214 vid föregående session – 9 nya tester: 4 för
graduate-skriptet, 5 nya/justerade i test_static_export.py, plus
schemakontraktet i test_model_contract.py uppdaterat för den nya kolumnen.)

Export kördes rent efter varje steg (`python -m scripts.export_static_site
--output site`).

## Databasändringar

- Backup tagen till scratchpad innan `graduate_signal_to_installation`
  kördes mot `data/runway_safe.db`.
- Enda skrivningen mot skarp data: Signal 65 (`status`, `installation_id`)
  och en ny Installation-rad (id 73). Inget annat ändrat, inget raderat.

## Inget kvarstår att besluta för dessa två punkter

Båda är byggda, körda och testade enligt godkännandet. Nästa kandidat för
graduering (om/när fler signaler bekräftas fysiskt färdigbyggda via en
primärkälla) körs på samma sätt:
`python -m scripts.graduate_signal_to_installation --signal-id <id> --type <EMAS/EMASMAX/greenEMAS> --install-year <år>`.
