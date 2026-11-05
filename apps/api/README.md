# API entry point

The service lives in `src/invoice_automation/api/` rather than here, so that
it imports as `invoice_automation.api.main:app` with no path manipulation and
shares the package with the rules, the pipeline and the database access it
depends on (see `docs/decisions.md`, ADR-011).

Run it:

```powershell
.\.venv\Scripts\python.exe -m uvicorn invoice_automation.api.main:app --reload
```

Documentation at http://127.0.0.1:8000/docs. Every endpoint except `/health`
requires the `X-API-Key` header.
