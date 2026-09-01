# Catalog KPI extract — Part 1

- Source: `Catalog Altivar Process ATV600 variable speed drives.pdf`
- Drive rating rows: **346**
- Passive filter rows: **93**
- Outputs: `drive_ratings.csv`, `passive_filters.csv`, `catalog_kpis.xlsx`

## Scope
- Drive tables: PDF pages 24–31, 86–87, 113–114 (ATV660), 119–120 (ATV680)
- Passive filters: PDF pages 52–55

## DC choke
- **No DC-choke accessory part-number table** in the three client PDFs.
- Column `has_dc_choke` is a **feature flag** (true for typical ATV630/650 Process 200–480 V; false for ATV680 LH / ATV660; unknown for 500–690 V / Y6).

## Variant default (approved)
- `is_default_variant=true` prefers normal Process refs over `…Z` cabinet for the same kW/duty/supply.
- Cabinet codes stay in the dataset for when the user asks for cabinet integration.

## ATV660
- Compact Drive Systems tables on Catalog pp.113–114.
- Used for Standard harmonics >315 kW (and available in 110–315 band; sizing prefers ATV630 there).
- If Excel locks `drive_ratings.csv`, extract writes `drive_ratings_new.csv` and the sizing loader prefers that file until the main CSV is free to replace.

## Next
- Part 2: gold-validate ~20–30 rows against the PDF by eye.
