# ⚖️ Solvent Inquest

> **Evidence-first, budget-solvent document Q&A agent.**  
> Ask questions about uploaded PDFs. The system answers using **at most 6 tool calls** per question — with strict budget enforcement, deterministic verdict logic, and prompt-injection protection.

---

## ✨ Features

- 🔍 **Keyword search + targeted page reads** — no vector database, no embeddings
- 💰 **Solvency gate** — every move is checked against the closure cost before execution
- 📜 **Verbatim evidence only** — all answers are grounded in quoted text from the document
- 🔗 **Succession chains** — automatically resolves amended/updated values across pages
- 🛡️ **Prompt injection protection** — quarantine filter + nonce-wrapped page text
- ⚖️ **Deterministic verdicts** — `ANSWER`, `CONFLICTING`, `PARTIAL`, `INSUFFICIENT_*`
- 🧮 **Built-in calculator** — handles multi-slot arithmetic (differences, ratios, percentages)

---

## 🚀 Quick Start

### 1. Clone & install

```bash
git clone https://github.com/hanshika-06/Solvent-Inquest.git
cd Solvent-Inquest
pip install -r requirements.txt
```

### 2. Configure API key

```bash
cp .env.example .env
# Edit .env and add your GEMINI_API_KEY
```

Your `.env` file (never commit this):
```
GEMINI_API_KEY=your-gemini-api-key-here
INQUEST_PROVIDER=gemini
INQUEST_MODEL=gemini-2.0-flash
```

### 3. Run

```bash
streamlit run app.py
```

Open [http://localhost:8501](http://localhost:8501), upload a PDF, and start asking questions.

### 4. Run tests (no API key needed)

```bash
python -m pytest -q tests
```

---

## 🏗️ Architecture

```
User Question
     │
     ▼
INTAKE (LLM) ──► Slots (1-3 atomic facts)
     │
     ▼
INQUEST LOOP (max 6 tool calls)
  build_moves() → gate() → Desk.call() → reader.read() → verify()
     │
     ▼
VERDICT (deterministic code)
     │
     ▼
WRITER (LLM narrates from verdict JSON only)
```

### The Solvency Invariant

> At every step: `remaining_calls ≥ closure_cost`

The gate **refuses** any move that would leave the system unable to settle open slots within the remaining budget.

---

## 📁 Project Layout

```
app.py                  ← Streamlit UI (chat + docket panel + trace + JSON download)
requirements.txt
.env.example            ← Copy to .env and add your API key

inquest/
  tools.py      ← 4 document primitives: list_documents, list_headings, get_page, search_keyword
  desk.py       ← Budget controller (MAX 6 calls, duplicate refusal, reserve rule)
  docket.py     ← Per-question state: slots, leads, testimony, succession chains, closure cost
  controller.py ← Orchestrator: INTAKE → solvency-gated loop → VERDICT → WRITER
  reader.py     ← Sealed reader (only LLM that sees page text) + code-side verifier
  verdict.py    ← Deterministic ANSWER/CONFLICTING/INSUFFICIENT + safe calculator
  llm.py        ← LLM wrapper (Gemini / Anthropic / OpenAI)
  prompts.py    ← INTAKE and WRITER system prompts

tests/
  fake_llm.py       ← Deterministic LLM stub (no API key needed)
  test_core.py      ← 11 offline tests: budget, succession, injection, calculator, etc.
  conftest.py
  make_test_pdf.py
```

---

## 🔌 LLM Support

| Provider | Default Model | Environment Variable |
|---|---|---|
| **Gemini** (default) | `gemini-2.0-flash` | `GEMINI_API_KEY` |
| Anthropic | `claude-sonnet-4` | `ANTHROPIC_API_KEY` |
| OpenAI | `gpt-4o` | `OPENAI_API_KEY` |

Switch provider via `.env`:
```
INQUEST_PROVIDER=anthropic
INQUEST_MODEL=claude-sonnet-4
ANTHROPIC_API_KEY=your-key
```

---

## 🧪 What's Tested (Offline, Fake LLM)

- ✅ Budget wall (hard 6-call cap)
- ✅ Reserve rule (last call must be `get_page`)
- ✅ Duplicate call refusal
- ✅ Supersession chain (amended values resolved correctly)
- ✅ Scope-free fork → `CONFLICTING`
- ✅ Missing info → `INSUFFICIENT_NOT_IN_DOC`
- ✅ Multi-slot + arithmetic calculator
- ✅ Prompt injection quarantine (directive cannot become testimony)
- ✅ Fabricated quote dropped by verifier
- ✅ UI renders correctly

---

## ⚠️ Limitations

- **Text-only** — scanned/image PDFs return no text
- **No memory across questions** — each question starts fresh
- **Tables** — text is extracted flat; row/column structure is lost
- **Charts/graphs** — completely invisible (no vision model)

---

## 📄 License

MIT
