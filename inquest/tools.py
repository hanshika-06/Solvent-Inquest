"""The four document primitives. Built at upload time; the agent only ever sees tool OUTPUTS.

list_documents(), list_headings(doc_id), get_page(doc_id, page_number), search_keyword(doc_id, keyword)
The text index lives inside this class (it IS the tool implementation) and is never exposed to the
agent/controller except through these four methods.
"""
import re
import unicodedata
import collections
import pymupdf

_QUOTES = str.maketrans({"\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-"})


def normalize_text(t: str) -> str:
    t = unicodedata.normalize("NFKC", t).translate(_QUOTES)
    t = t.replace("\u00ad", "")
    t = re.sub(r"(\w)-[ \t]*\n[ \t]*(\w)", r"\1\2", t)      # de-hyphenate line breaks
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    t = re.sub(r"\n[ \t]*\n+", "\n\n", t)
    return t.strip()


def flat(s: str) -> str:
    return re.sub(r"\s+", " ", s).casefold()


_NUMBERED = re.compile(r"^(\d+(\.\d+){0,3}|[A-Z]|Part [IVX]+|Chapter \d+|Appendix [A-Z]?)[\s.:)]\s*\S")


def _detect_headings(lines):
    """lines: list of (page, text, size, bold). Heuristic: big font, or bold + numbered/ALL CAPS."""
    sizes = collections.Counter()
    for _, t, size, _ in lines:
        sizes[round(size, 1)] += len(t)
    if not sizes:
        return []
    body = sizes.most_common(1)[0][0]
    out, seen = [], set()
    for page, t, size, bold in lines:
        t = re.sub(r"\s+", " ", t).strip()
        if not (3 <= len(t) <= 90) or re.fullmatch(r"[\d\W]+", t):
            continue
        if re.match(r"(?i)^(figure|table|fig\.)\s*\d", t):
            continue
        if t.endswith((",", ";")) or (t.endswith(".") and not _NUMBERED.match(t)):
            continue
        big = size >= body * 1.18
        if big or (bold and (_NUMBERED.match(t) or (t.isupper() and len(t) > 4))):
            key = (t.casefold(), page)
            if key not in seen:
                seen.add(key)
                out.append({"title": t, "page": page})
    return out[:500]


class DocumentTools:
    def __init__(self):
        self._docs = {}

    # ---- upload-time indexing (tool implementation, not agent-visible) ----
    def add_pdf(self, source, title=None) -> str:
        doc = pymupdf.open(stream=source, filetype="pdf") if isinstance(source, (bytes, bytearray)) else pymupdf.open(source)
        pages, lines = [], []
        for i, page in enumerate(doc, start=1):
            pages.append(normalize_text(page.get_text("text")))
            try:
                for b in page.get_text("dict")["blocks"]:
                    if b.get("type") != 0:
                        continue
                    for ln in b["lines"]:
                        spans = ln["spans"]
                        if not spans:
                            continue
                        txt = "".join(s["text"] for s in spans)
                        size = max(s["size"] for s in spans)
                        bold = any((s["flags"] & 16) or "bold" in s["font"].lower() for s in spans)
                        lines.append((i, txt, size, bold))
            except Exception:
                pass
        toc = []
        try:
            toc = [{"title": t.strip(), "page": p} for _, t, p in doc.get_toc() if p and p > 0]
        except Exception:
            pass
        headings = toc if len(toc) >= 3 else _detect_headings(lines)
        doc_id = f"doc{len(self._docs) + 1}"
        meta_title = title or (doc.metadata or {}).get("title") or doc_id
        self._docs[doc_id] = {"title": meta_title, "pages": pages, "flat": [flat(p) for p in pages], "headings": headings}
        return doc_id

    # ---- harness-side registry info (NOT an agent tool call) ----
    def info(self, doc_id):
        d = self._docs[doc_id]
        return {"doc_id": doc_id, "title": d["title"], "pages": len(d["pages"])}

    # ---- the four primitives ----
    def list_documents(self):
        return [{"doc_id": k, "title": v["title"], "pages": len(v["pages"])} for k, v in self._docs.items()]

    def list_headings(self, doc_id):
        d = self._docs.get(doc_id)
        if not d:
            return {"error": "unknown doc_id"}
        return {"doc_id": doc_id, "headings": list(d["headings"])}

    def get_page(self, doc_id, page_number):
        d = self._docs.get(doc_id)
        if not d:
            return {"error": "unknown doc_id"}
        if not isinstance(page_number, int) or not (1 <= page_number <= len(d["pages"])):
            return {"error": f"page_number must be 1..{len(d['pages'])}"}
        return {"doc_id": doc_id, "page": page_number, "text": d["pages"][page_number - 1]}

    def search_keyword(self, doc_id, keyword):
        d = self._docs.get(doc_id)
        if not d:
            return {"error": "unknown doc_id"}
        k = flat(normalize_text(str(keyword)))
        if not k:
            return {"keyword": keyword, "pages": []}
        k2 = k.replace("-", "")
        hits = [i + 1 for i, f in enumerate(d["flat"]) if k in f or k2 in f.replace("-", "")]
        return {"keyword": keyword, "pages": hits}
