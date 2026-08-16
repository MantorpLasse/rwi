# Public UI Slice 3B report

## Changes

- Historical installation records are now a native collapsed disclosure with
  an accurate record count. Their source-derived free text remains present,
  but each “Detaljer från källan” block is separately collapsed.
- BGM’s empty current-presence state now reads “Aktuell EMAS-status ej
  verifierad” with the approved historical/status-review explanation. It does
  not claim presence or absence.
- Empty Incident cards are omitted.
- Shared disclosure styling adds native keyboard-visible focus treatment.

## Safety and limitations

No database, domain, evidence, Signal, or reconciliation data changed. Raw
Signal source notes remain absent. The dashboard and Signals information
architecture were not changed. Runway sidebar integration was deferred: its
data remains available and unchanged, while this polish focused on the
approved high-impact history/mobile compression.

## Verification

- Focused static tests: 16 passed.
- Static site regenerated.
- Screenshots: `docs/ui/screenshots/slice3b/desktop-dashboard.png`,
  `desktop-bgm.png`, `desktop-cgf.png`, `desktop-mdw.png`,
  `mobile-dashboard.png`, `mobile-bgm.png`, and `mobile-mdw.png`.

## Local review

```powershell
.\.venv\Scripts\python.exe -m http.server 8000 --directory site
```

No commit, push, or deployment was performed.
