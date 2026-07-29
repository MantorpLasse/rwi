# Utredning: "Senast uppdaterat"-flöde

**Fråga:** Har `Signal`, `Installation`, `Source` och `Incident` `created_at`/`updated_at`-fält?

**Svar: Nej, ingen av de fyra har det.** Detta är en utredning, inget är byggt.

## Vad som kontrollerades

Läste `app/models/signal.py`, `installation.py`, `source.py`, `incident.py` i sin helhet, samt
`app/database.py` (ingen `Base`-mixin lägger till tidsstämplar automatiskt) och sökte igenom
hela `app/` efter `created_at`/`updated_at`.

| Modell | `created_at` | `updated_at` | Övriga datumfält som finns |
|---|---|---|---|
| `Signal` | Nej | Nej | `target_year`, `planning_year`, `procurement_year`, `construction_start`, `completion_date`, `last_verified_at` (finns i schemat men **0/67 rader har det ifyllt**) |
| `Installation` | Nej | Nej | `install_year`, `replacement_year` (kalenderår, inte tidsstämplar) |
| `Source` | Nej | Nej | `published_date` (48/63 ifyllt), `retrieved_at` (47/63 ifyllt) - men det är källans/hämtningens datum, inte "när denna databasrad senast ändrades" |
| `Incident` | Nej | Nej | `incident_date` (händelsens datum, inte radens ändringsdatum) |

Ingen av de fyra modellerna har någon mekanism (event listener, `onupdate`, mixin) som skulle
kunna fylla i sådana fält retroaktivt.

### Två närliggande fynd värda att känna till

**1. `Airport` har redan `created_at`/`updated_at`** (`app/models/airport.py:27-28`):
```python
created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
```
Verifierat mot live-databasen: alla 86 flygplatser har båda fälten ifyllda, och `onupdate`
fungerar redan i praktiken (t.ex. flygplats-id 2 har `updated_at` några timmar efter
`created_at`, från en senare redigering samma dag). Det här är alltså ett fungerande,
redan validerat mönster i just den här kodbasen - om fälten läggs till på de fyra andra
modellerna är `Airport`s implementation den naturliga mallen att kopiera, **men** notera att
`AcquisitionRun` (nyare kod, se nedan) använder `DateTime(timezone=True)` och
`datetime.now(UTC)` istället för `Airport`s äldre naiva `datetime.utcnow` - det nyare mönstret
är att föredra för nya fält.

**2. Det finns redan ett tidsstämplat delsystem - men det är inte kopplat till dessa fyra
modeller.** `app/models/acquisition.py` definierar `AcquisitionSource`, `AcquisitionRun` och
`Snapshot` (webbskrapnings-proveniens: när hämtades vilken FAA-sida, med vilken HTTP-status,
vilken hash). Dessa har egna `created_at`/`started_at`/`completed_at`/`retrieved_at`-fält och
används redan av `app/services/acquisition.py` och `app/scripts/capture_faa_emas.py`. Men
`Source`, `Signal`, `Installation` och `Incident` har **ingen foreign key** till
`Snapshot`/`AcquisitionRun` - subsystemet spårar när en *källsida* hämtades från nätet, inte
när en *databasrad* skapades eller ändrades. Oanvändbart som genväg utan att först bygga en
koppling som inte finns idag.

**3. En sparsam, manuell "ändringslogg" finns redan gömd i fritext-fälten.** Flera
importskript skriver in rader som `[2026-07-26] install_year satt till 2012, bekräftat via...`
direkt i `notes`/`summary`. Sökte igenom hela databasen efter mönstret `[YYYY-MM-DD]`:

| Fält | Rader med `[YYYY-MM-DD]`-stämpel |
|---|---|
| `Signal.notes` | 13 av 67 |
| `Signal.supplier_reason` | 0 av 67 |
| `Installation.notes` | 9 av 149 |
| `Incident.summary` | 0 av 26 |

Det är en reell, daterad ändringshistorik - men täcker bara 13-20 % av raderna och är en
biprodukt av enskilda skript, inte ett systematiskt mönster. Bra som komplement, otillräckligt
som enda källa.

## Git-historiken över `docs/utredning_*.md` och `scripts/`

- `docs/utredning_*.md`: 8 filer finns i repot, men **bara 1 commit någonsin** har rört någon
  av dem (`a392cf6`). De verkar ha skrivits/committats i klump snarare än en gång per fynd -
  git ger nästan inget tidsupplöst signal härifrån.
- `scripts/`: 31 skript, 20 commits har rört katalogen, av totalt 83 commits på `main` över en
  dryg vecka (2026-07-20 till 2026-07-27). Till skillnad från `docs/utredning_*.md` är detta
  ett starkt spår: nästan varje commit motsvarar en tydligt avgränsad, namngiven
  data-förändring (t.ex. `a392cf6 feat: signal->installation graduation UI, plus a large EMAS
  install-year backfill`, `6fda8c5 feat: attach official-airport-page primary sources for ZQN
  and WLG`) - flygplatskoder och vad som gjordes står ofta rakt i commit-meddelandet.
- **Databasen är gitignorad** (`data/*.db`, se `.gitignore:6`) - `data/runway_safe.db` är
  aldrig incheckad. Git känner alltså bara till *när ett skript skrevs och committades*, inte
  *när/om det kördes* eller *vilka specifika rad-id:n det påverkade*. I praktiken sammanfaller
  de här i det här repot (ett skript skrivs, körs och committas i samma svep, ett skript per
  ändring) - men det är en tolkning av arbetssättet, inte en garanti, och ett skript som körs
  om senare (t.ex. för att fånga nya rader) syns inte alls om inte skriptfilen också ändras.

## Två vägar framåt (inget byggt)

### A. Lägg till `created_at`/`updated_at` nu - gäller bara framåt

Lägg till fälten på `Signal`, `Installation`, `Source`, `Incident` enligt `AcquisitionRun`s
mönster:
```python
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))
```
**Migrering:** Projektet har ingen Alembic/migrationsverktyg - `scripts/init_db.py` kör bara
`Base.metadata.create_all()`, som **inte** ändrar redan existerande tabeller. Att lägga till
kolumnerna på en databas som redan har data kräver ett litet engångsskript (`ALTER TABLE ...
ADD COLUMN ...`), i linje med hur repot redan gör punktinsatser
(`scripts/rename_sandiford_to_standiford.py`, `scripts/backfill_airport_codes.py` m.fl.).

**Den ärliga bieffekten:** de 149 installationerna, 67 signalerna, 63 källorna och 26
incidenterna som redan finns skulle antingen (a) alla få samma `created_at`
(migreringstillfället) - vilket ser ut som "allt skapades samtidigt", en synlig lögn om man
inte förklarar den, eller (b) lämnas `NULL` för befintliga rader och UI:t visar "Okänt" för dem,
vilket är ärligare men gör att "Senast uppdaterat"-listan är tom/ointressant tills tillräckligt
med ny aktivitet hunnit ske efter migreringen.

**Fördel:** Det här är den *enda* vägen som ger exakt det ursprungliga UI-kravet
(typ + flygplats + beskrivning + datum + **tillförlitlig länk till rätt detaljsida**), eftersom
det är kopplat till den faktiska raden, inte en tolkning av en commit.

### B. Härled från git-loggen över `scripts/`

Bygg en enkel "Ändringslogg"-sida som listar commit-meddelanden (rubrikrad) + datum, filtrerat
till commits som rör `scripts/` (eventuellt även `app/models/`). Kräver ingen schemaändring,
kan byggas idag, och historiken är faktiskt bra (83 välavgränsade commits över en vecka).

**Den ärliga bieffekten:** det här blir en **utvecklings-logg, inte en rad-logg**. En commit
som "attach official-airport-page primary sources for ZQN and WLG" rör två flygplatser i ett
svep - git vet inte vilka specifika `Signal`/`Installation`-id:n som byttes, så en tillförlitlig
"länka till exakt rätt detaljsida per post" går inte att garantera rent mekaniskt (skulle kräva
att man parsar/gissar utifrån commit-textens fritext, eller manuellt taggar varje commit med
berörda id:n - ett efterhandskonstruerat lager ovanpå git, inte en riktig källa).

### C. Komplement, inte ersättning: mina de befintliga `[YYYY-MM-DD]`-stämplarna i fritext

Ett regex-baserat extraktionsskript skulle kunna plocka ut de 13+9 rader som redan har
inline-datum och visa dem separat, tydligt märkta som "manuella anteckningar", inte som en
fullständig ändringslogg (matchar hur `.card.annotation`/"Min bedömning"-mönstret redan
särskiljer manuella anteckningar från källdata på sajten idag). Täcker för lite av datan för
att stå ensamt.

## Rekommendation

Om målet är den ursprungliga specen (typ, flygplats, kort beskrivning, datum, **länk till rätt
detaljsida**, 15 senaste) - bara **Alternativ A** kan leverera det tillförlitligt. Alternativ B
är snabbare att bygga och kräver ingen migrering, men blir per nödvändighet en grövre
utvecklings-logg utan garanterade per-rad-länkar, och Alternativ C är för glest för att stå för
sig själv. En rimlig ordning: bygg A (skapar värde från och med nu), och överväg B som en
separat, tydligt märkt "Tidigare (härlett från utvecklingshistorik)"-sektion för perioden innan
tidsstämplarna fanns, om historiskt sammanhang är viktigt att visa.

Inget av detta är byggt ännu - väntar på besked om riktning innan jag går vidare.
