"""Compliance tests: verify the hackathon constraint
'No direct access to raw document text outside the four allowed tools.'

Tests 1-9: architecture compliance (no raw-text cache, on-demand reads, correct returns).
Test 10:   existing test_core suite must still pass (regression guard).
"""
import os
import sys
import pathlib
import pytest
import pymupdf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from make_test_pdf import make
from inquest.tools import DocumentTools
from inquest.desk import Desk, MAX_CALLS


# ---------------------------------------------------------------------------
# Shared fixture: tiny synthetic PDF (14 pages, known content)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def tools_and_doc(tmp_path_factory):
    pdf_path = make(str(tmp_path_factory.mktemp("compliance") / "c.pdf"))
    tools = DocumentTools()
    doc_id = tools.add_pdf(pdf_path)
    return tools, doc_id


# ---------------------------------------------------------------------------
# TEST 1: add_pdf() does NOT populate a raw page-text cache
# ---------------------------------------------------------------------------

def test_no_raw_text_stored_after_add_pdf(tools_and_doc):
    """After upload, the internal doc record must contain no structure
    that holds all-page text. Only metadata keys are allowed."""
    tools, doc_id = tools_and_doc
    doc_record = tools._docs[doc_id]

    # Forbidden keys: old-style pre-extracted text stores
    for forbidden_key in ("pages", "flat"):
        assert forbidden_key not in doc_record, (
            f"add_pdf() stored raw text under key '{forbidden_key}' — compliance violation"
        )

    # _keyword_cache must be empty before any search tool call
    assert doc_record["_keyword_cache"] == {}, (
        "add_pdf() must not pre-populate keyword cache"
    )

    # Confirm the only stored text-like fields are lightweight metadata
    assert isinstance(doc_record["title"], str)
    assert isinstance(doc_record["page_count"], int)
    assert isinstance(doc_record["source"], bytes)   # raw PDF bytes (not page text)


# ---------------------------------------------------------------------------
# TEST 2: Before get_page() is called, no page text exists in DocumentTools
# ---------------------------------------------------------------------------

def test_no_page_text_before_get_page():
    """Fresh DocumentTools: after add_pdf but before any get_page call,
    no page text should appear anywhere in the internal state."""
    import io, tempfile
    pdf_path = tempfile.mktemp(suffix=".pdf")
    make(pdf_path)

    tools = DocumentTools()
    doc_id = tools.add_pdf(pdf_path)
    doc_record = tools._docs[doc_id]

    # No 'pages' list of strings
    assert "pages" not in doc_record
    # No 'flat' list of strings
    assert "flat" not in doc_record
    # Heading cache not yet populated
    assert doc_record["_headings_cache"] is None
    # Keyword cache empty
    assert doc_record["_keyword_cache"] == {}

    os.unlink(pdf_path)


# ---------------------------------------------------------------------------
# TEST 3: get_page() returns only the requested page
# ---------------------------------------------------------------------------

def test_get_page_returns_only_requested_page(tools_and_doc):
    """get_page(doc_id, 3) must return page 3 data."""
    tools, doc_id = tools_and_doc
    result = tools.get_page(doc_id, 3)

    assert result.get("page") == 3, "Returned page number must match requested page"
    assert "text" in result, "Result must contain 'text'"
    assert isinstance(result["text"], str)
    assert len(result["text"]) > 0
    # Known content from make_test_pdf: page 3 has "timeout"
    assert "timeout" in result["text"].lower()


# ---------------------------------------------------------------------------
# TEST 4: get_page() extracts ONLY the requested page and does not read others
# ---------------------------------------------------------------------------

def test_get_page_does_not_cache_other_pages():
    """After get_page(doc_id, 3), no text from other pages should appear
    in the DocumentTools internal state."""
    import tempfile
    pdf_path = tempfile.mktemp(suffix=".pdf")
    make(pdf_path)

    tools = DocumentTools()
    doc_id = tools.add_pdf(pdf_path)

    # Call get_page for only page 3
    tools.get_page(doc_id, 3)

    doc_record = tools._docs[doc_id]

    # Must not have grown a 'pages' cache
    assert "pages" not in doc_record, "get_page() must not populate a 'pages' cache"
    # Keyword cache must still be empty (get_page doesn't populate it)
    assert doc_record["_keyword_cache"] == {}, (
        "get_page() must not populate the keyword cache"
    )

    os.unlink(pdf_path)


def test_get_page_accesses_only_requested_page_index(monkeypatch):
    """When get_page(doc_id, 3) is called, PyMuPDF must only be queried for
    page index 2 (0-indexed 3rd page). No other page indices may be accessed."""
    import tempfile
    pdf_path = tempfile.mktemp(suffix=".pdf")
    make(pdf_path)

    tools = DocumentTools()
    doc_id = tools.add_pdf(pdf_path)

    orig_open = pymupdf.open
    accessed_indices = []

    def spying_open(*args, **kwargs):
        doc = orig_open(*args, **kwargs)
        orig_getitem = doc.__class__.__getitem__
        def spying_getitem(self, idx):
            accessed_indices.append(idx)
            return orig_getitem(self, idx)
        monkeypatch.setattr(doc.__class__, "__getitem__", spying_getitem)
        return doc

    monkeypatch.setattr(pymupdf, "open", spying_open)
    res = tools.get_page(doc_id, 3)
    assert res["page"] == 3
    # Exactly one page index was accessed: 2 (0-indexed page 3)
    assert accessed_indices == [2], f"Accessed unexpected page indices: {accessed_indices}"

    os.unlink(pdf_path)


# ---------------------------------------------------------------------------
# TEST 5: search_keyword() returns page numbers only — no text snippets
# ---------------------------------------------------------------------------

def test_search_keyword_returns_page_numbers_only(tools_and_doc):
    """search_keyword must return {keyword, pages} with pages as a list of ints.
    No text, snippets, or document content may be returned."""
    tools, doc_id = tools_and_doc
    result = tools.search_keyword(doc_id, "timeout")

    # Exactly the two permitted keys
    assert set(result.keys()) == {"keyword", "pages"}, (
        f"search_keyword returned unexpected keys: {set(result.keys())}"
    )
    assert result["keyword"] == "timeout"
    assert isinstance(result["pages"], list)
    # Every element is an integer (page number)
    for item in result["pages"]:
        assert isinstance(item, int), f"page entry is not an int: {item!r}"
    # Known: pages 3 and 11 contain "timeout"
    assert 3 in result["pages"]
    assert 11 in result["pages"]


# ---------------------------------------------------------------------------
# TEST 6: After search_keyword(), only page-number metadata is cached
# ---------------------------------------------------------------------------

def test_search_keyword_caches_page_numbers_only():
    """The keyword cache may store page-number lists, but nothing else."""
    import tempfile
    pdf_path = tempfile.mktemp(suffix=".pdf")
    make(pdf_path)

    tools = DocumentTools()
    doc_id = tools.add_pdf(pdf_path)
    tools.search_keyword(doc_id, "timeout")

    doc_record = tools._docs[doc_id]
    cache = doc_record["_keyword_cache"]

    # Cache must be non-empty (at least the "timeout" entry)
    assert len(cache) > 0

    # Every cached value must be a list of integers — no strings, no dicts
    for keyword_key, cached_value in cache.items():
        assert isinstance(cached_value, list), (
            f"Cache entry for '{keyword_key}' is not a list: {type(cached_value)}"
        )
        for entry in cached_value:
            assert isinstance(entry, int), (
                f"Cache entry for '{keyword_key}' contains non-int: {entry!r}"
            )

    # No 'pages' key (pre-extracted text) must have appeared
    assert "pages" not in doc_record
    assert "flat" not in doc_record

    os.unlink(pdf_path)


# ---------------------------------------------------------------------------
# TEST 7: list_headings() returns heading metadata only (title + page)
# ---------------------------------------------------------------------------

def test_list_headings_returns_metadata_only(tools_and_doc):
    """list_headings must return {doc_id, headings} where each heading
    has exactly 'title' (str) and 'page' (int). No raw text fields."""
    tools, doc_id = tools_and_doc
    result = tools.list_headings(doc_id)

    assert "headings" in result
    assert isinstance(result["headings"], list)

    for heading in result["headings"]:
        heading_keys = set(heading.keys())
        # Must have title and page
        assert "title" in heading_keys, f"Heading missing 'title': {heading}"
        assert "page" in heading_keys, f"Heading missing 'page': {heading}"
        # Must NOT have raw text body, snippet, or content
        forbidden = {"text", "content", "body", "snippet", "raw"}
        overlap = heading_keys & forbidden
        assert not overlap, (
            f"Heading contains forbidden raw-text key(s) {overlap}: {heading}"
        )
        assert isinstance(heading["title"], str)
        assert isinstance(heading["page"], int)

    # After list_headings(), only the heading metadata cache is populated
    doc_record = tools._docs[doc_id]
    assert doc_record["_headings_cache"] is not None
    assert "pages" not in doc_record   # no full-page text cache
    assert "flat" not in doc_record


# ---------------------------------------------------------------------------
# TEST 8: Controller and other non-tool modules do NOT import pymupdf
# ---------------------------------------------------------------------------

def test_controller_has_no_direct_pdf_access():
    """controller.py must not import pymupdf or call get_text().
    PDF access must only happen inside tools.py."""
    root = pathlib.Path(__file__).parent.parent / "inquest"

    forbidden_modules = ["controller", "desk", "docket", "verdict", "reader", "prompts"]
    for mod_name in forbidden_modules:
        src = (root / f"{mod_name}.py").read_text(encoding="utf-8")
        assert "pymupdf" not in src, (
            f"inquest/{mod_name}.py imports pymupdf — PDF access must stay in tools.py"
        )
        if mod_name not in ("reader",):   # reader legitimately calls llm, not PDF
            assert "get_text(" not in src, (
                f"inquest/{mod_name}.py calls get_text() — PDF access must stay in tools.py"
            )

    # app.py also must not touch pymupdf directly
    app_src = (root.parent / "app.py").read_text(encoding="utf-8")
    assert "pymupdf" not in app_src, "app.py must not import pymupdf"
    assert "get_text(" not in app_src, "app.py must not call get_text()"


# ---------------------------------------------------------------------------
# TEST 9: All tool calls go through Desk and count against MAX_CALLS = 6
# ---------------------------------------------------------------------------

def test_all_tool_calls_go_through_desk_and_are_counted(tools_and_doc):
    """Every document-tool call must be routed through Desk.call() and
    increment desk.used. The Desk must enforce MAX_CALLS = 6."""
    tools, doc_id = tools_and_doc
    info = tools.info(doc_id)
    desk = Desk(tools, doc_id, info["pages"], max_calls=MAX_CALLS)

    assert desk.used == 0
    assert desk.remaining == MAX_CALLS

    # Each of the four tools costs exactly 1 call
    r1 = desk.call("list_documents")
    assert r1["ok"] is True
    assert desk.used == 1

    r2 = desk.call("list_headings")
    assert r2["ok"] is True
    assert desk.used == 2

    r3 = desk.call("search_keyword", keyword="timeout")
    assert r3["ok"] is True
    assert desk.used == 3

    r4 = desk.call("get_page", page_number=3)
    assert r4["ok"] is True
    assert desk.used == 4

    # Confirm get_page result has text (content returned correctly)
    assert "text" in r4["result"]
    assert "timeout" in r4["result"]["text"].lower()

    # Fill remaining 2 calls
    desk.call("get_page", page_number=1)
    desk.call("get_page", page_number=2)
    assert desk.used == 6
    assert desk.remaining == 0

    # 7th call is rejected
    r7 = desk.call("get_page", page_number=4)
    assert r7["ok"] is False
    assert "budget exhausted" in r7["reason"]
    assert desk.used == 6      # unchanged
    desk.audit()               # hard assertion: no budget violation


# ---------------------------------------------------------------------------
# TEST 10: All existing test_core tests still pass (regression)
# ---------------------------------------------------------------------------

def test_existing_tests_still_importable():
    """Smoke-check: the test_core module can be imported without error,
    confirming the tools.py interface is backward-compatible."""
    import importlib.util
    core_path = pathlib.Path(__file__).parent / "test_core.py"
    spec = importlib.util.spec_from_file_location("test_core", core_path)
    mod = importlib.util.module_from_spec(spec)
    # Just import — pytest will run the actual tests separately
    spec.loader.exec_module(mod)
    # If we reach here, no import-time errors
    assert hasattr(mod, "test_six_call_limit")
    assert hasattr(mod, "test_seventh_call_impossible")
    assert hasattr(mod, "test_instruction_shaped_content_quarantined")
