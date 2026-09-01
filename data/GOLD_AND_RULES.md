# How this workbench answers (rules + data — not a trained model)

## Today

| Task | How it works |
|---|---|
| **Sizing (Step 1)** | Lookup in `data/catalog/drive_ratings.csv` (+ `_new` when present). Harmonics families only (no IP picker). |
| **Passive filters** | **5% THDi only** when Harmonics = Low (ATV630). Combined PDF includes VW3A… datasheets when Schneider has them. |
| **Compliance Yes/No/N/A (Step 2)** | IP **rule from the consultant spec** (not a user choice): IP54/55 vs ATV630 → **No**. Then evidence search, gold DB, then keyword rules. |
| **Schneider source** | **Combined Product Datasheet first**, then verified Catalog / Programming / Installation. Never invent pages. **Never** cite `data/references/` files. |
| **Tab 3 · Learn from corrections** | **Error loop only:** client finds a mistake on a sheet we generated → edit → import. Updates rule book **and** appends to the gold DB so that mistake does not repeat. |

No neural network invents kW picks or compliance answers.

## Two stores (do not confuse)

| Store | Folder / file | Used for |
|---|---|---|
| Drive ratings | `data/catalog/drive_ratings*.csv` | kW → part number |
| Gold references | Put sheets in `data/references/` → ingest → `data/gold/reference_answers.jsonl` | Past correct Yes/No + remarks |

Ingest after adding sheets:

```powershell
.\.venv\Scripts\python.exe scripts\ingest_references.py --replace
```

## Past compliance sheets = reference, not source

| Role | Use for | Cite on the sheet? |
|---|---|---|
| Catalog / Installation / Programming | Official pages when verified | **Yes** (after datasheet) |
| Attached Product Datasheets | Drive-specific proof | **Yes — primary** |
| Gold reference DB (`data/references/`) | Auto-fill matching answers | **No** |

## Harmonics + IP family rules

**Low:** &lt;110 kW → ATV630 (+ 5% filter); ≥110 kW → ATV680.  
**Standard:** &lt;110 kW → ATV630; 110–315 kW → ATV630; &gt;315 kW → ATV660.  

**IP (compliance sheet only, not Step 1):** If the consultant clause requires **IP54 or IP55** and the offer is **ATV630 (IP21)** → **No** (needs ATV650). If the offer is ATV650 → **Yes**. Nobody types or selects IP. Gold cannot override this.

## Evidence search (accurate Yes/No)

For each requirement:

1. IP hard rule (gold cannot override)
2. Attached Product Datasheets (cached when you attach)
3. Gold reference DB (answers only — never as Source)
4. Catalog + Programming + Installation together — related hit only
5. Keyword / capability book
6. If nothing related: Why = note “No related evidence…”, empty Source — never filler citations

A miss in the datasheet is **not No** — continue to gold then manuals. Never invent pages. Never cite the 10 project sheets.

Tab 3 refreshes `schneider_sources.json` **only** when the corrected row cites Catalog / Programming / Installation with a page in the manual index.

## Citation accuracy

Pages are catalog printed numbers (e.g. `1/13`) or Programming/Installation footer pages, from keyword hits on the real PDFs. Rating-table clauses still use the sized drive CSV `source_page`.
