# Runway Safe Intelligence

Första körbara MVP:n för att följa EMAS-installationer, projekt, flygplatser och källor.

## Funktioner

- Dashboard med nyckeltal
- Lista över flygplatser och projekt
- Fritextsökning
- Filter på land, status, år och lägsta score
- Projektdetaljer med källor
- Seed-data för de amerikanska projekt vi redan identifierat
- SQLite + SQLAlchemy
- FastAPI + Jinja2 + Bootstrap

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

## Återställ databasen

```powershell
Remove-Item .\data\runway_safe.db
python -m app.seed
```

## Nästa steg

1. Formulär för att skapa och ändra flygplatser/projekt
2. Historik över statusändringar
3. CSV-import/export
4. Dokumentbevakning
5. Automatisk scoring
6. PDF-indexering och fulltextsökning
