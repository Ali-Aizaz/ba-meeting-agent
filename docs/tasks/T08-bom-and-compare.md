# T08 — BOM and option comparisons

**Priority:** Diagram only · **Do not build**

## Why

The new brief lists “Car parts / BOM tables and option comparisons” under **Extension (diagram only, do not build)**.

The 13 Oct brief required `build_bom` and `compare` as live tools. That requirement is withdrawn. The one diagram (T17) shows them as a later extension.

## What the diagram should show

A dashed box, not on the built path:

- `build_bom(system)` would return part, quantity, estimated cost, and supplier from a parts table.
- `compare(option_a, option_b, criteria)` would return a short trade-off table.
- Both would read the same knowledge base, still without sending the meeting transcript out.

## Acceptance criteria

- [ ] The T17 diagram includes this box and labels it as not built.
- [ ] The agent has no `build_bom` or `compare` tool in this submission.
