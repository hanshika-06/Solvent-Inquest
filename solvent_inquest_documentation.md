# ⚖️ Solvent Inquest: System Architecture & Technical Documentation

> **Evidence-first, budget-solvent document Q&A agent strictly compliant with the zero raw-text pre-extraction constraint.**

---

## 1. Architectural Overview & Compliance

Solvent Inquest enforces strict compliance with the hackathon constraint:
> *"No direct access to raw document text. All reading must go through the tool interface listed. You may not design your own additional tools."*

### Zero Raw-Text Pre-Extraction Guarantee
At upload time, Solvent Inquest stores the PDF source and lightweight metadata needed by the document tools. It does not preload or cache raw page text. Document content is accessed only when the agent invokes one of the four allowed document tools. `search_keyword()` returns only page numbers, `list_headings()` returns only heading metadata, and `get_page()` extracts only the requested page.

**Raw page text is not cached at upload time.**

---

## 2. Architecture Diagram

```
USER
  │
  ▼
PDF UPLOAD
  │
  ▼
DOCUMENT TOOL LAYER
  ├── PDF source / reference (raw bytes for on-demand loading)
  ├── Document metadata (doc_id, title, page count)
  ├── Heading metadata (title + page number only)
  └── Keyword → page-number index (page numbers only, lazily populated)
  │
  ▼
AI AGENT (Controller / INTAKE)
  │
  ▼
SOLVENCY GATE (remaining_calls ≥ closure_cost invariant)
  │
  ▼
ONE OF FOUR ALLOWED TOOLS (Desk-metered, max 6 calls per question)
  ├── list_documents()   → [{"doc_id", "title", "pages"}]
  ├── list_headings()    → {"doc_id", "headings": [{"title", "page"}]}
  ├── search_keyword()   → {"keyword", "pages": [page_numbers]}
  └── get_page()         → {"doc_id", "page", "text": "..."}
                                │
                                ▼ (Requested content only)
                             READER (Sealed extractor LLM)
                                │
                                ▼
                           VERIFICATION (Code-side proof & quarantine)
                                │
                                ▼
                             DOCKET (Structured state & succession chains)
                                │
                                ▼
                      DETERMINISTIC VERDICT ENGINE
                                │
                                ▼
                             WRITER (Narrates from Verdict JSON only)
                                │
                                ▼
                             ANSWER
```

---

## 3. The Four Allowed Document Primitives

All document interactions are strictly confined to the following four primitives in `DocumentTools` (`inquest/tools.py`):

| Tool | Parameters | Return Schema | Internal PDF Operation | Cached State |
|---|---|---|---|---|
| `list_documents` | None | `[{"doc_id": str, "title": str, "pages": int}]` | None (reads metadata record) | Document metadata |
| `list_headings` | `doc_id: str` | `{"doc_id": str, "headings": [{"title": str, "page": int}]}` | Inspects ToC / span metadata | Heading title + page number only |
| `search_keyword` | `doc_id: str`, `keyword: str` | `{"keyword": str, "pages": [int]}` | Scans pages on demand for matches | `keyword -> [page_numbers]` only |
| `get_page` | `doc_id: str`, `page_number: int` | `{"doc_id": str, "page": int, "text": str}` | Opens PDF, loads requested page index only, normalizes text | **No persistent caching** |

### Key Invariants:
1. **No Snippets in Search**: `search_keyword()` returns purely a list of integer page numbers.
2. **No Text in Headings**: `list_headings()` returns only section title strings and page integers.
3. **Single Page Reads**: `get_page()` opens the PDF and extracts exactly the single page requested. No surrounding or adjacent pages are read.
4. **No Direct Access**: Non-tool modules (`controller.py`, `desk.py`, `docket.py`, `verdict.py`, `reader.py`, `prompts.py`, `app.py`) never import PyMuPDF or access raw document streams directly.

---

## 4. Solvency Gate & The Petty-Cash Desk

### Hard 6-Call Budget
Every document-tool call must be routed through the `Desk` (`inquest/desk.py`), which counts against `MAX_CALLS = 6`.

### The Solvency Invariant
$$\text{slack} = \text{remaining\_calls} - \text{closure\_cost} \ge 0$$

- **Closure Cost**: The minimal number of calls required to settle all active fact slots (pending searches + essential reads).
- **Reserve Rule**: When $\text{remaining\_calls} = 1$, the only admitted action is `get_page` to guarantee reading the evidence.
- **Duplicate Protection**: Repeated identical queries are rejected at zero cost.

---

## 5. Evidence Verification & Quarantine

1. **Sealed Reader**: A dedicated form-filling prompt (`inquest/reader.py`) sees the single requested page text wrapped in unique cryptographic nonces. It cannot execute tools or see conversation history.
2. **Code-Side Verifier**:
   - Quotes must match verbatim in the normalized page text (`_alnum(quote) in page_alnum`).
   - The extracted value must reside strictly within the quote (`_alnum(value) in _alnum(quote)`).
   - Cue words (for succession/updates) must match a verified deterministic lexicon (`CUE_LEXICON`).
3. **Prompt Injection Quarantine**:
   - Imperative/AI-addressed patterns (`ignore previous instructions`, `reveal system prompt`, etc.) are caught by `DIRECTIVE_STRONG` and quarantined.
   - Quarantined snippets are logged and displayed in the UI, never promoted to testimony, and never passed to the final Writer LLM.

---

## 6. Succession Chains & Deterministic Verdicts

When multiple conflicting values appear across different pages:
- **Explicit Succession**: If an update statement uses replacement phrasing (*"changed to"*, *"now"*, *"superseded"*), links previous values (*"from X to Y"*), or provides explicit *as-of* dates, code deterministically resolves the current value.
- **Scope Differentiation**: Distinct values for different scopes/regions are preserved as multi-scope facts.
- **Unresolvable Conflict**: If values conflict without succession signals or scope distinctions, the engine returns `CONFLICTING` and lists both values rather than guessing.
- **Deterministic Calculator**: Arithmetic calculations (differences, percentage changes, ratios) are evaluated using a safe AST evaluator on extracted numerical values.

---

## 7. Verification & Test Suite

The test suite (`tests/test_compliance.py` and `tests/test_core.py`) executes 28 offline tests covering:
- Upload-time metadata verification (no raw text stored or cached).
- Page-access isolation (`get_page` extracts only the requested page index).
- Keyword search return schemas (`pages` list of integers only).
- Non-tool module isolation (no PyMuPDF imports outside `tools.py`).
- Desk budget enforcement and reserve rule.
- Prompt injection quarantine and fabricated quote rejection.
- Succession resolution, conflict forking, and deterministic calculation.
