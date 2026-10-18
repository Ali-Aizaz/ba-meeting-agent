# T08 — Car parts, BOM and trade-off tools (`build_bom`, `compare`)

**Priority:** Must · **Milestone:** M3 Brain & Notion · **Depends on:** T07 · **Estimate:** 2 days

## Why

- "Car parts" row: **knows main parts by system** (battery pack, motor, inverter, chassis, brakes, suspension, body, ADAS sensors, infotainment); **builds a BOM table with part, qty, estimated cost and supplier from our parts pack; flags single-supplier and long-lead parts.**
- Required tools: **`build_bom(system)`**, which returns a part / qty / cost / supplier table, and **`compare(option_a, option_b, criteria)`**, which returns a trade-off table (e.g. 60 vs 75 kWh).
- Live test: **compare 60 vs 75 kWh packs**, and **list the main parts and suppliers for the 60 kWh pack with a cost estimate**.
- Never invent specs, prices or suppliers (C6). Numbers must carry a source or an assumption (C7). An LLM doing arithmetic or "remembering" prices is the main hallucination risk, so this task computes everything **deterministically in code from the pack data**. The LLM only chooses the tool and phrases the result.

## What

**In scope**
- Data model and loader on top of the SQLite tables from T07 (parts, BOM lines, suppliers, variant specs).
- System taxonomy and synonym resolution, plus variant resolution ("60 kWh pack").
- `build_bom(system)`: rows, totals, flags, assumptions, sources.
- `compare(option_a, option_b, criteria)`: a trade-off table built only from pack data, plus clearly labelled derived values.
- Chat-size rendering (≤ 8 rows) and a full-size version for Notion Meeting Notes.

**Out of scope**
- Free-text retrieval (T07). Posting and formatting rules beyond the table body (T12).

## Details

### Data model
```python
Part:     part_no, name, system, variant | None, qty_per_unit, unit_cost, currency,
          supplier_ids: list, lead_time_weeks | None, source_ref
Supplier: supplier_id, name, country | None, lead_time_weeks | None, parts: list, source_ref
VariantSpec: variant_id ("60 kWh"), attributes: dict[name → (value, unit, source_ref)]
```
- Column mapping from the pack's actual headers goes in a small config file (`config/pack_mapping.yaml`), so a header rename is a config change, not a code change. Fill it in when the pack arrives.
- If the pack gives lead time per supplier and not per part, a part's lead time is the **minimum** lead time across its qualified suppliers (state this assumption in output). Use the **maximum** instead if the pack defines lead time that way. Follow the pack.

### System taxonomy and synonyms
Canonical systems (from the PDF): `battery pack`, `motor`, `inverter`, `chassis`, `brakes`, `suspension`, `body`, `ADAS sensors`, `infotainment`. Add others found in the pack (e.g. thermal management, charging) from its data, not by assumption.

Synonyms map (`config/systems.yaml`), e.g.:
- battery, pack, HV battery, cells → `battery pack`
- e-motor, drive motor, traction motor → `motor`
- radar, camera, lidar, ultrasonic → `ADAS sensors`

Variant parsing: regex for `(\d+)\s*kWh` → variant; the remaining words → system. `"60 kWh pack"` → system `battery pack`, variant `60 kWh`.

### `build_bom(system)`
1. Resolve system (+ variant). If unresolvable, return `{"error": "unknown_system", "known_systems": [...]}`. The agent asks one clarifying question (T12): "Which system — battery pack, motor, inverter…?"
   - If the system is a known canonical one but the pack has **no parts for it** (e.g. the pack covers the battery pack and motor only), return `{"error": "no_data_for_system", "system": "infotainment", "systems_in_pack": [...]}`. The agent then says the pack has no parts list for that system and may list the **typical** main parts from general knowledge, clearly labelled, with **no quantities, costs or suppliers** (policy in T07/T13). Example: `Not in the pack. Typical infotainment parts (general knowledge): head unit, display, amplifier, antenna module. No Falcon costs or suppliers available.`
2. Select parts for that system/variant.
3. For each part: `qty`, `unit_cost`, `line_cost = qty × unit_cost` (use `Decimal`), supplier name(s), lead time.
4. **Flags**
   - `single_supplier`: exactly one qualified supplier for the part in the supplier list.
   - `long_lead`: lead time ≥ `bom.long_lead_weeks` (default 12). If the pack defines "long lead" itself, use that definition and say so.
5. Totals: `total_cost` (sum of known line costs). If any part lacks a cost, set `total_is_partial=true` and list the missing parts. **Never estimate a missing price.**
6. Assumptions list: currency, cost basis as stated in the pack (e.g. unit cost at a stated volume), lead-time rule, any partial totals.
7. Sources: list of `source_ref`s.

Output (JSON to the agent):
```json
{"system": "battery pack", "variant": "60 kWh",
 "rows": [{"part": "...", "qty": 1, "unit_cost": 0, "line_cost": 0, "currency": "USD",
           "supplier": "...", "flags": ["single_supplier", "long_lead"]}],
 "total_cost": 0, "total_is_partial": false, "assumptions": ["..."], "sources": ["parts_list.xlsx"]}
```
(The zeros are placeholders. Real values come only from the pack.)

### `compare(option_a, option_b, criteria)`
1. Resolve both options to `VariantSpec`s, or to parts or suppliers if comparing those. Unresolvable → error → one clarifying question. If both options are recognisable engineering concepts that the pack has no data for (e.g. "LFP vs NMC" when the pack only lists one chemistry), return `{"error": "no_data_for_options"}`; the agent may give a short general-knowledge trade-off, labelled as such, with no Falcon numbers (T13 policy). Rows that the pack *does* cover are still returned.
2. `criteria`: a list of attribute names. If empty, use the **default criteria that both options actually have data for**, from: pack cost, usable capacity, range, mass, charge time (e.g. 10–80%), efficiency, supplier risk (single-supplier / long-lead counts from `build_bom`).
3. Each row: `criterion | option_a value | option_b value | Δ`, each value with its `source_ref`.
4. **Missing data** → `"not in pack"`. Never fill from model knowledge.
5. **Derived values** (e.g. pack cost = kWh × $/kWh, or cost per km of range) are computed in code **only from pack inputs**, and the formula is added to `assumptions` (e.g. "Pack cost = 60 kWh × $X/kWh (falcon_specs.pdf p.4)").
6. Neutral summary line built from the data, e.g. `75 kWh: +N km range, +$M cost, +K kg vs 60 kWh`. **No recommendation.** The persona never takes sides (T12). If asked "which should we choose?", restate the trade-off and ask what the deciding criterion is.

### Rendering
- **Chat (T12):** plain-text pipe table, header + ≤ `reply.max_table_rows` (8) rows, ≤ 5 columns. Flags as short markers: `[1-src]` single-supplier, `[long]` long-lead (Meet chat has no formatting; avoid emoji that may render inconsistently). If there are more rows, show the top rows by line cost, then a `+N more` line and the total. The full table goes into Meeting Notes.
- **Notion (T11):** full table block with every row, flags, totals, assumptions and sources.

Chat example shape (values illustrative only):
```
60 kWh pack BOM (src: parts_list.xlsx)
Part | Qty | Est. cost | Supplier
Cell module | 12 | $… | … [1-src][long]
BMS | 1 | $… | …
+5 more · Total ≈ $… (USD, pack unit costs)
```

## Acceptance criteria

- [ ] `build_bom` works for every canonical system present in the pack, and for battery-pack variants (60 / 75 kWh).
- [ ] Totals equal Σ qty × unit_cost exactly (Decimal), with the currency stated.
- [ ] Single-supplier and long-lead flags match a hand-checked list from the pack, with 100% precision and recall.
- [ ] Missing data appears as "not in pack" / partial total. No invented values (test: every number in the output exists in the pack or is a documented derivation).
- [ ] `compare("60 kWh", "75 kWh", [])` returns a table whose rows all have pack values or "not in pack", plus a neutral delta summary.
- [ ] Unknown system or option returns a structured error that leads to one clarifying question.
- [ ] Chat rendering ≤ 1 header + 8 rows + 1 summary line. Notion rendering is complete.

## Tests

| ID | Type | Test | Pass |
|---|---|---|---|
| T08-U1 | unit | Synonym + variant resolution ("HV battery", "60kWh pack", "radar") | Correct system/variant |
| T08-U2 | unit | `build_bom` on fixture pack: rows, line costs, total (Decimal) | Exact match with golden JSON |
| T08-U3 | unit | Flags: single-supplier, long-lead at threshold boundaries (11, 12, 13 weeks) | Correct |
| T08-U4 | unit | Part with missing cost | `total_is_partial=true`; part listed; no estimate |
| T08-U5 | unit | `compare` with complete data / missing criterion / derived metric | Values + "not in pack" + formula in assumptions |
| T08-U6 | unit | **Number provenance property test**: every numeric value in tool output ∈ pack values ∪ documented derivations | Holds for all systems |
| T08-U7 | unit | Chat renderer truncation (20-row BOM) | Header + 8 rows + "+12 more · Total" |
| T08-U8 | unit | Unknown system | Structured error with `known_systems` |
| T08-U9 | unit | Known system with no parts in the fixture pack; `compare` of options absent from the pack | `no_data_for_system` / `no_data_for_options`; no rows invented |
| T08-I1 | integration | Real pack: hand-checked 60 kWh pack BOM and 60 vs 75 kWh comparison | Matches the hand-check spreadsheet |
| T08-I2 | integration | Agent end-to-end (with T12): "Hey BA, list the main parts and suppliers for the 60 kWh pack with a cost estimate" | Reply ≤ 6 lines/one small table, cites source, flags shown |

## Dependencies

- T07 (SQLite tables and source refs).
- Used by T12 (tools), T11 (full tables in Meeting Notes), T16 (live-test items).

## Related resources

- Python `decimal`: https://docs.python.org/3/library/decimal.html
- pandas `read_excel` / `read_csv`: https://pandas.pydata.org/docs/reference/io.html
- Hypothesis (property-based tests): https://hypothesis.readthedocs.io/
- Bill of materials overview: https://en.wikipedia.org/wiki/Bill_of_materials
