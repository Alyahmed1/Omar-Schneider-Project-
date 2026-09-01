# Schneider VFD Compliance + Part Lookup

Local web app for Schneider Electric drive engineers.

## Workflow

1. **Size** — kW lines → recommended drive → look up, combine datasheet & attach for compliance
2. **Compliance sheet** — upload consultant PDF → draft **Yes / No / N/A** → export Excel/Word
3. **Learn from corrections** — client-fixed sheet error loop only

**Combined datasheet** (separate helper tab, not a step): paste multiple part numbers → look up on Schneider → download one merged Product Datasheet PDF. Does not attach for compliance.

Compliance **requires** attached datasheets from Step 1.

## Quick start (port 8001)

```powershell
cd C:\Users\moham\Downloads\OMAR_PROJECT_SCHNEIDER
.\run.ps1
```

Or:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8001
```

Open **http://127.0.0.1:8001**

## Local LLM (Ollama)

Yes/No, IP rules, and page citations stay rule-based. Ollama only rewrites the short **Why** remark.

Leave Ollama running in the tray with `qwen2.5:3b` pulled. `.\run.ps1` sets `LLM_ENABLED=true` so Generate polishes remarks.

To turn it off:

```powershell
$env:LLM_ENABLED = "false"
.\run.ps1
```

If Ollama is down, generate still works with the original remarks.

If you start uvicorn yourself, set `$env:LLM_ENABLED = "true"` in that same PowerShell window.

## Notes

- Status values: **Yes** = complies, **No** = does not comply, **N/A** = not applicable (primary answer).
- Offered drive family is free text typed by the engineer.
- Datasheets are scraped from public Schneider product document links.
- Always verify drafts before formal submittal.
