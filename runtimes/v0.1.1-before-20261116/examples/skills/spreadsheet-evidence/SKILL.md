---
name: spreadsheet-evidence
description: Inspect attached Excel workbooks when cell positions, fills, labels or other formatting carry task evidence.
---

Treat the original workbook as read-only. Record its filename and SHA256 in your evidence.

An empty-looking cell can still carry essential fill or border information. Preserve worksheet
coordinates and formatting when extracting evidence; exporting only cell values to CSV can
lose the information needed to answer the question.

Use an available spreadsheet library or inspect the XLSX ZIP/XML using Python's standard
library. If inspecting XML, resolve workbook sheet relationships, shared strings, cell style
indices, and style fill references. Distinguish foreground/background colors and explicit RGB
from indexed/theme colors; report unresolved interpretation instead of silently treating all
cells as unformatted. Include explicit blank cells that carry a style.

Write a compact `file-evidence.json` with the attachment hash, sheet names, the relevant cell
coordinates and resolved features, and how the extracted evidence supports your conclusion.
Keep the task's requested answer format separate from this diagnostic artifact. This skill
provides a file-reading method, not a solution or benchmark reference answer.
