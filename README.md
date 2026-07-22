# Runway Safe Intelligence

Investeringsresearch för Runway Safe: vilka flygplatser har EMAS idag, vilka
incidenter har skett, och vilka är på väg att beställa. Se
[PLAN_FORENKLING.md](PLAN_FORENKLING.md) för den fulla bakgrunden och planen.

## Datamodell

Fem kärntabeller: `Airport`, `Runway`, `Installation`, `Incident`, `Signal`,
`Source`. En `Signal` är "det här kan bli en framtida order" – ersätter den
gamla Project/Observation/Verification/Fact/Intelligence-pipelinen. Två enkla
regler skapar signaler automatiskt:

1. En `Incident` skapar alltid en `confidence=high`-signal (arresting-material
   måste bytas efter en aktivering).
2. En `Source` vars titel/sammanfattning nämner EMAS/RSA/"runway safety
   area"/"arresting system" skapas via `add_source_and_flag_keywords()` och ger
   en `confidence=low`-signal som väntar på manuell granskning.

## Två sätt att titta på datan

- **FastAPI + Jinja2** (`app/main.py`) – levande server, bra om du vill kunna
  klicka runt lokalt under utveckling.
- **Statisk export** (`scripts/export_static_site.py`) – genererar en
  fristående HTML-sajt (ingen server behövs för att läsa den, bara för att
  uppdatera den). Handbyggd CSS, ingen Tailwind/Node-byggkedja, stöd för
  ljust/mörkt läge och mobil. Rekommenderas för att faktiskt läsa datan;
  passar GitHub Pages/Netlify.

## Installation på Windows

```powershell
cd runway-safe-intelligence
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m app.seed
uvicorn app.main:app --reload
```

Öppna sedan:

- Webbapp: http://127.0.0.1:8000
- API-dokumentation: http://127.0.0.1:8000/docs

### Statisk export

```powershell
python -m scripts.export_static_site --output site
```

Öppna `site/index.html` direkt i webbläsaren, eller servera mappen (t.ex.
`python -m http.server --directory site`).

## Återställ databasen

```powershell
Remove-Item .\data\runway_safe.db
python -m app.seed
```

## Nästa steg

Se "Föreslagen ordning" i [PLAN_FORENKLING.md](PLAN_FORENKLING.md). Kvar:

1. Lägg in sparade PDF-länkar som `Source`-rader för att se flödet
   end-to-end
2. RSA-regeln (FAA Runway Ends Table → `potential_new_construction`-signal)
3. Automatisk PDF-crawling
4. Internationell bevakning (Zürich, UK, Nya Zeeland, Madagaskar)
