# Architecture memo: Solvent Inquest (draft: edit after real-LLM testing)

**Problem.** Search returns page numbers without content, get_page returns one page, and exceeding 6 calls fails the question regardless of correctness. A call therefore buys the *right to read*, not knowledge, and the real task is deciding what to spend calls on and when it is no longer possible to answer reliably.

**Design.** A plain-Python controller keeps a *docket* per question: slots (atomic facts the answer needs), leads (search hit pages; never evidence), and testimony (verbatim-verified quotes). Before every call a *solvency gate* computes `slack = remaining calls - closure cost` (cheapest calls still needed to settle every slot) and refuses any exploratory move that would make slack negative. Closing reads are always allowed; the last call must be a read. The desk that owns the raw tools counts before executing, so the cap holds even if every other component misbehaves.

**Why each part exists.**
- *Bookend probes*: for slots that may be updated, read the latest and earliest lead; with page-only search these are the likeliest places for an update and the original.
- *Sealed reader*: only one LLM call sees page text; it has no tools or secrets and can only fill a typed form. Code checks every quote appears verbatim on the page, the value is inside the quote, the cue word is in a fixed lexicon and in the quote, and instruction-shaped text is quarantined. The planner and writer never see raw pages.
- *Succession chains*: conflicts are resolved by code: explicit from/to links, as-of dates, or a single value carrying update wording ("changed to", "now", "no longer"). "Later page wins" is never used. No signal = CONFLICTING, both shown.
- *Verdicts*: ANSWER, ANSWER+disclosure (pages known but unread), CONFLICTING, INSUFFICIENT (not in document / pages read but not stated / budget-limited). Declining is a first-class, deterministic outcome.
- *Calculator*: arithmetic is done in code on verified values, not by the LLM.

**Known failure modes not fixed (and intended fix).**
1. Reader mislabels a slot or invents a cue: verifier catches fabricated quotes, not mis-assigned ones; fix: second-opinion check on strong testimony for numeric slots.
2. Cue lexicon misses phrasings ("superseded by", non-English): fails safe to CONFLICTING; fix: extend lexicon.
3. Flood searches (generic terms hit many pages): handled with heading map and disclosure, but ranking is lexical only.
4. Search is literal substring: synonyms depend on the intake LLM's term plan; headings rescue helps only if titles match.
5. Table/figure-only values and scanned pages are not read; no OCR.
6. A document can lie in plain prose; we only defend against instructions, not false facts.
7. Page text is truncated by extraction quirks (columns, math); normalization handles hyphenation only.
8. Not yet tested against a live model; prompts may need tuning.
