"""The four document primitives — hackathon-compliant implementation.

ARCHITECTURAL CONTRACT:
  add_pdf()        → stores PDF source bytes + lightweight metadata ONLY.
                     No page text is extracted or cached at upload time.
  list_documents() → document metadata only (doc_id, title, page count)
  list_headings()  → heading title + page number only (computed on first call, cached as metadata)
  search_keyword() → page-number list only (scanned on demand; only page numbers cached)
  get_page()       → text of ONE requested page, extracted on demand from stored PDF bytes

RAW PAGE TEXT IS NEVER PRE-EXTRACTED OR STORED.
Document content is produced only inside the tool that is called.
"""
import re
import unicodedata
import collections
import pymupdf

_QUOTES = str.maketrans({"\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"',
                          "\u2013": "-", "\u2014": "-"})


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


def _detect_headings_from_doc(doc):
    """Inspect span metadata from an open PyMuPDF document.
    Returns [{title, page}] only — no raw page text is retained.
    """
    lines = []
    for i, page in enumerate(doc, start=1):
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

    sizes = collections.Counter()
    for _, t, size, _ in lines:
        sizes[round(size, 1)] += len(t)
    if not sizes:
        return []
    body = sizes.most_common(1)[0][0]
    out, seen = [], set()
    for page_num, t, size, bold in lines:
        t = re.sub(r"\s+", " ", t).strip()
        if not (3 <= len(t) <= 90) or re.fullmatch(r"[\d\W]+", t):
            continue
        if re.match(r"(?i)^(figure|table|fig\.)\s*\d", t):
            continue
        if t.endswith((",", ";")) or (t.endswith(".") and not _NUMBERED.match(t)):
            continue
        big = size >= body * 1.18
        if big or (bold and (_NUMBERED.match(t) or (t.isupper() and len(t) > 4))):
            key = (t.casefold(), page_num)
            if key not in seen:
                seen.add(key)
                out.append({"title": t, "page": page_num})
    return out[:500]


class DocumentTools:
    def __init__(self):
        self._docs = {}

    # ---- upload-time indexing (stores metadata only, never raw page text) ----
    def add_pdf(self, source, title=None) -> str:
        """Store PDF source bytes and lightweight metadata only.
        No page text is extracted, normalized, or cached here.
        """
        # Ensure we hold bytes (not a file path) for repeatable on-demand reading
        if isinstance(source, (bytes, bytearray)):
            source_bytes = bytes(source)
        else:
            with open(source, "rb") as fh:
                source_bytes = fh.read()

        doc = pymupdf.open(stream=source_bytes, filetype="pdf")
        page_count = len(doc)
        meta_title = title or (doc.metadata or {}).get("title") or ""

        # Extract ToC — heading titles + page numbers only (no body text)
        toc_headings = []
        try:
            toc_headings = [
                {"title": t.strip(), "page": p}
                for _, t, p in doc.get_toc()
                if p and p > 0
            ]
        except Exception:
            pass
        doc.close()

        doc_id = f"doc{len(self._docs) + 1}"
        self._docs[doc_id] = {
            # --- stored at upload time ---
            "source": source_bytes,          # PDF bytes — needed by every tool
            "title": meta_title or doc_id,   # document title metadata
            "page_count": page_count,         # total page count metadata
            "toc": toc_headings,              # ToC entries: [{title, page}] only
            # --- lazily populated on first tool call ---
            "_headings_cache": None,          # filled by list_headings(); title+page only
            "_keyword_cache": {},             # keyword -> [page_numbers] only, no text
        }
        return doc_id

    # ---- harness-side registry info (NOT an agent tool call) ----
    def info(self, doc_id):
        d = self._docs[doc_id]
        return {"doc_id": doc_id, "title": d["title"], "pages": d["page_count"]}

    # ---- the four primitives ----

    def list_documents(self):
        """Returns document-level metadata only. No page text."""
        return [
            {"doc_id": k, "title": v["title"], "pages": v["page_count"]}
            for k, v in self._docs.items()
        ]

    def list_headings(self, doc_id):
        """Returns heading title + page number only.
        Headings are computed from the PDF on the first call and cached as metadata.
        Raw page text is never stored or returned.
        """
        d = self._docs.get(doc_id)
        if not d:
            return {"error": "unknown doc_id"}

        if d["_headings_cache"] is None:
            toc = d["toc"]
            if len(toc) >= 3:
                # Use ToC metadata directly (already title+page only)
                d["_headings_cache"] = toc
            else:
                # Heuristic detection: inspect span metadata, store title+page only
                doc = pymupdf.open(stream=d["source"], filetype="pdf")
                try:
                    d["_headings_cache"] = _detect_headings_from_doc(doc)
                finally:
                    doc.close()

        return {"doc_id": doc_id, "headings": list(d["_headings_cache"])}

    def get_page(self, doc_id, page_number):
        """Extracts ONLY the requested page from the PDF on demand.
        No other pages are read. No text is cached. One call = one page.
        """
        d = self._docs.get(doc_id)
        if not d:
            return {"error": "unknown doc_id"}
        if not isinstance(page_number, int) or not (1 <= page_number <= d["page_count"]):
            return {"error": f"page_number must be 1..{d['page_count']}"}

        doc = pymupdf.open(stream=d["source"], filetype="pdf")
        try:
            page = doc[page_number - 1]          # 0-indexed; only this page is loaded
            text = normalize_text(page.get_text("text"))
        finally:
            doc.close()

        return {"doc_id": doc_id, "page": page_number, "text": text}

    def search_keyword(self, doc_id, keyword):
        """Scans the PDF for the keyword on demand. Returns page-number list ONLY.
        No text snippets or page content is returned or cached.
        Repeated searches for the same keyword use the cached page-number list.
        """
        d = self._docs.get(doc_id)
        if not d:
            return {"error": "unknown doc_id"}
        k = flat(normalize_text(str(keyword)))
        if not k:
            return {"keyword": keyword, "pages": []}

        # Cache hit: page-number list only
        if k in d["_keyword_cache"]:
            return {"keyword": keyword, "pages": list(d["_keyword_cache"][k])}

        # Scan PDF on demand — page text is used only for matching, never stored
        k2 = k.replace("-", "")
        doc = pymupdf.open(stream=d["source"], filetype="pdf")
        try:
            hits = []
            for i, page in enumerate(doc, start=1):
                page_flat = flat(normalize_text(page.get_text("text")))
                if k in page_flat or k2 in page_flat.replace("-", ""):
                    hits.append(i)
        finally:
            doc.close()

        # Store page numbers only — no page text
        d["_keyword_cache"][k] = hits
        return {"keyword": keyword, "pages": hits}
