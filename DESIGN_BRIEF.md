# RWI — designbrief (godkänd riktning)

Referens: `design_mockup.html` (skickad i chatten) visar riktningen på
signals-tabellen. Detta dokument beskriver hela tokensystemet så det
går att applicera konsekvent på samtliga vyer (dashboard, airports,
signal-detalj, osv), inte bara mockupen.

## Koncept

Grundat i flygplatsens egen visuella värld — skyltar, ATC-instrument,
sektionskartor — istället för generisk mörk fintech-terminal-look.
Signaturelementet är flygplatskoden som en gul "skylt-chip" (som en
riktig taxibane-/banskylt), med flygplatsnamnet som `title`-attribut
(hover).

## Färgtokens

```css
:root{
  --bg:#0B1220; --panel:#131B2C; --panel2:#0F1729; --border:#263349;
  --text:#EDE6D6; --text-dim:#8B96AC; --text-faint:#5A6580;
  --accent:#F2B705; --accent-ink:#3B2B00;
  --high:#D4507A; --high-bg:#3A1A28; --high-ink:#F6C3D4;
  --med:#E8871E; --med-bg:#3A2812; --med-ink:#F7CE9C;
  --low:#4A5A78; --low-bg:#1B2436; --low-ink:#AEB9CE;
  --verified:#3FA34D;
}
```

Använd CSS-variabler genomgående, aldrig hårdkodade hex-värden i
enskilda komponenter — gör temat lätt att finjustera senare från en
enda plats.

## Typografi

- **Display** (rubriker, stat-siffror): `Space Grotesk`, 500/600
- **Body** (brödtext, UI-element): `IBM Plex Sans`, 400/500
- **Mono** (koder, datum, score, koordinater): `IBM Plex Mono`, 400/500

Google Fonts, laddas via `<link>` i `<head>`. Mono-fonten är viktig för
allt tabelldata (kod, år, score) — den gör att siffror radar upp sig
snyggt och känns "instrumentell".

## Signaturkomponent: flygplats-skylt

```html
<span class="sign" title="Manchester–Boston Regional Airport">MHT</span>
```
```css
.sign{
  display:inline-block;background:var(--accent);color:var(--accent-ink);
  font-family:'IBM Plex Mono',monospace;font-weight:500;font-size:12px;
  padding:3px 8px;border-radius:4px;letter-spacing:.02em;
}
```
Använd **överallt** en flygplatskod visas — tabeller, kort,
detaljsidor, kartmarkörer (som popup-etikett). Konsekvens är poängen.

## Confidence som gauge, inte badge-text

```html
<div class="gauge high"><span></span><span></span><span></span>
  <span class="gauge-label">Hög</span></div>
```
```css
.gauge{display:flex;gap:2px;align-items:center;}
.gauge span{width:5px;height:12px;border-radius:1px;background:var(--border);}
.gauge.high span:nth-child(-n+3){background:var(--high);}
.gauge.med span:nth-child(-n+2){background:var(--med);}
.gauge.low span:nth-child(-n+1){background:var(--low-ink);}
.gauge-label{font-size:10px;color:var(--text-faint);margin-left:6px;
  text-transform:uppercase;letter-spacing:.04em;}
```

## Kategori-text — mappningstabell (aldrig råa databasnamn i UI)

| Databasvärde | Visningstext | Färgklass |
|---|---|---|
| `new_installation` | Ny installation | `.cat.new` (teal) |
| `replacement` | Ersättning | `.cat.replace` (amber) |
| `replacement_after_incident` | Efter incident | `.cat.incident` (rosa/high) |
| `study` | Studie | `.cat.study` (slate) |
| `potential_new_construction` | Möjlig ny installation | `.cat.new` |

Bygg denna mappning på **ett** ställe (en dict/funktion), återanvänd i
alla templates/export-script — inte hårdkodat per vy.

## Auto-genererade titlar — gör mindre monotona

Nuvarande: "Replacement expected after incident on 2017-04-01" upprepat
rakt av. Föreslagen mall istället:
`"{airport_name} — EMAS-ersättning väntas efter incident ({datum})"`
eller, om `runway_end` finns: inkludera det för mer specificitet, likt
de manuella signalerna ("Runway 6 departure-end EMAS replacement").

## Statusremsa (toppbar)

Kompakt rad med stora mono-siffror för totalsummor — se mockupen. Håll
den till max 4-5 tal, de viktigaste (flygplatser, installationer,
aktiva signaler, hög confidence). Inte en plats för allt.

## Övrigt att åtgärda under samma runda

- **Document-noden**: om den bara är intern acquisition-cache, ta bort
  från navigeringen. Om den har ett syfte för användaren, ge den
  faktiskt innehåll.
- Respektera övriga tokens (border-radius 4-6px genomgående, hairline
  borders `1px solid var(--border)`, inte tjocka ramar).
- Mobilanpassning: stat-remsan och tabellen ska inte gå sönder på smal
  skärm — testa och skärmdumpa (Playwright, som ni redan gjort tidigare
  för verifiering) innan ni committar.
- Behåll allt annat ni redan byggt (sök/filter/CSV-export/Leaflet-karta)
  — det här är bara ommålning, inte en omstrukturering av
  funktionaliteten.

## Prompt att ge Claude Code

> Implementera designbriefen i DESIGN_BRIEF.md över hela den statiska
> sajten (dashboard, airports-lista, airport-detalj, signals-lista,
> signal-detalj) — inte bara en enskild vy. Applicera tokensystemet
> konsekvent via CSS-variabler, bygg flygplats-skylt-komponenten och
> confidence-gauge-komponenten som återanvändbara byggstenar, och lägg
> till kategori-textmappningen på ett centralt ställe. Ta bort eller
> fyll Document-noden i navigeringen (fråga mig om syftet är oklart
> efter du undersökt koden). Förbättra auto-genererade signal-titlar
> enligt förslaget. Verifiera visuellt med Playwright-skärmdumpar
> (dashboard, mobilvy, mörkt läge om det skiljer sig) innan du
> committar, och kör hela testsviten.
