import os
import pytest
from fake_llm import FakeLLM
from make_test_pdf import make
from inquest.tools import DocumentTools
from inquest.desk import Desk
from inquest.controller import investigate, gate, Move
from inquest.docket import Docket, Slot
from inquest.reader import verify
from inquest.verdict import safe_eval, to_number


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    p = make(str(tmp_path_factory.mktemp("pdf") / "t.pdf"))
    tools = DocumentTools()
    return tools, tools.add_pdf(p)


def S(i, desc, terms, frame="CURRENT", cont=True):
    return {"id": i, "desc": desc, "frame": frame, "contestable": cont, "terms": terms}


def run(env, q, intake, compromised_reader=False, **kw):
    tools, doc = env
    llm = FakeLLM(intake, compromised_reader=compromised_reader)
    return investigate(q, tools, doc, llm, **kw), llm


# 1. Six-call limit
def test_six_call_limit(env):
    tools, doc = env
    desk = Desk(tools, doc, 14, max_calls=6)
    for p in range(1, 7):
        res = desk.call("get_page", page_number=p)
        assert res["ok"] is True
    assert desk.used == 6
    assert len(desk.trace) == 6
    assert desk.remaining == 0
    desk.audit()


# 2. Seventh call is impossible
def test_seventh_call_impossible(env):
    tools, doc = env
    desk = Desk(tools, doc, 14, max_calls=6)
    for p in range(1, 7):
        desk.call("get_page", page_number=p)
    # Attempt 7th call
    seventh = desk.call("get_page", page_number=7)
    assert seventh["ok"] is False
    assert seventh["reason"] == "budget exhausted"
    assert desk.used == 6
    assert len(desk.trace) == 6
    assert desk.rejections == 1
    assert desk.rejected[0]["tool"] == "get_page"
    assert desk.audit()


# 3. Last call must be a read (Reserve Rule) & duplicate refusal
def test_reserve_rule_and_duplicates(env):
    tools, doc = env
    desk = Desk(tools, doc, 14, max_calls=6)
    for p in range(1, 6):
        assert desk.call("get_page", page_number=p)["ok"]
    assert desk.remaining == 1
    # Search is refused because 1 call remaining must be reserved for get_page
    assert desk.call("search_keyword", keyword="timeout")["ok"] is False
    assert "reserve rule" in desk.rejected[-1]["reason"]
    # List headings is also refused
    assert desk.call("list_headings")["ok"] is False
    # Duplicate page read refused without cost
    assert desk.call("get_page", page_number=3)["ok"] is False
    assert desk.used == 5
    # Valid final get_page succeeds
    assert desk.call("get_page", page_number=6)["ok"] is True
    assert desk.used == 6


# 4. Planner cannot violate budget (Solvency Gate)
def test_planner_cannot_violate_budget(env):
    tools, doc = env
    desk = Desk(tools, doc, 14, max_calls=6)
    d = Docket("Test question")
    s = Slot(id="S1", desc="fact", frame="CURRENT", contestable=True, terms=["t1", "t2"])
    d.add_slot(s)
    # Simulate remaining = 1, explore move must be refused by gate
    desk.used = 5
    mv_explore = Move("list_headings", {}, "explore", ["S1"], "explore test")
    ok, why, slack = gate(mv_explore, d, desk)
    assert ok is False
    assert "reserve rule" in why or "would leave slack" in why or "insolvent" in why


# 5. Leads are never treated as evidence
def test_leads_never_treated_as_evidence(env):
    d = Docket("What is the timeout?")
    s = Slot(id="S1", desc="timeout", frame="CURRENT", contestable=True, terms=["timeout"])
    d.add_slot(s)
    # Apply search results (leads)
    d.apply_search("timeout", [3, 11])
    assert d.leads(s) == [3, 11]
    assert len(d.testimony) == 0
    # Status is LEADS, resolve() returns NONE (not evidence)
    assert d.status(s) == "LEADS"
    res = d.resolve(s)
    assert res["state"] == "NONE"


# 6. Fabricated quotes are rejected
def test_fabricated_quotes_rejected(env):
    page_text = "The system operates 5 offices worldwide."
    items = [{"slot": "S1", "quote": "The system operates 999 offices worldwide.", "value": "999 offices"}]
    acc, dropped = verify(items, page_text, {"S1"})
    assert len(acc) == 0
    assert len(dropped) == 1
    assert "not found verbatim" in dropped[0][0]


# 7. Values outside quotes are rejected
def test_values_outside_quotes_rejected(env):
    page_text = "The system operates 5 offices worldwide."
    # Quote is real, but value is not in quote
    items = [{"slot": "S1", "quote": "The system operates 5 offices worldwide.", "value": "10 offices"}]
    acc, dropped = verify(items, page_text, {"S1"})
    assert len(acc) == 0
    assert len(dropped) == 1
    assert "value not inside quote" in dropped[0][0]


# 8. Invalid cue words are rejected
def test_invalid_cue_words_rejected(env):
    page_text = "The timeout is 30 seconds for all requests."
    # Reader claims cue REPLACES with fake cue word not in quote
    items = [{"slot": "S1", "quote": "The timeout is 30 seconds for all requests.", "value": "30 seconds",
              "cue": "REPLACES", "cue_word": "superseded"}]
    acc, dropped = verify(items, page_text, {"S1"})
    assert len(acc) == 1
    # Cue and cue_word reset to NONE
    assert acc[0]["cue"] == "NONE"
    assert acc[0]["cue_word"] is None


# 9. Instruction-shaped page content is quarantined
def test_instruction_shaped_content_quarantined(env):
    res, llm = run(env, "What is the timeout?", {"slots": [S("S1", "timeout value", ["timeout"])]}, compromised_reader=True)
    assert res["verdict"]["label"] == "ANSWER"
    # Prompt injection found and quarantined
    assert len(res["quarantine"]) > 0
    assert any("IGNORE ALL" in q["snippet"].upper() for q in res["quarantine"])
    # Injection never leaked into verified testimony
    assert all("IGNORE ALL" not in t["quote"].upper() for t in res["testimony"])
    # Injection never passed to Writer LLM
    assert all("IGNORE ALL" not in w.upper() for w in llm.writer_inputs)


# 10. Explicit succession resolves a conflict
def test_explicit_succession_resolves_conflict(env):
    res, _ = run(env, "How many offices does the company operate?", {"slots": [S("S1", "number of offices", ["offices"])]})
    v = res["verdict"]
    assert v["label"] == "ANSWER"
    a = v["slots"][0]["answers"][0]
    assert a["value"].startswith("8")
    assert a["link"] == "assumed"
    assert len(a["chain"]) == 2
    assert a["chain"][0]["value"].startswith("5")
    assert a["chain"][1]["value"].startswith("8")


# 11. No succession signal produces CONFLICTING
def test_fork_is_conflicting_not_guess(env):
    res, _ = run(env, "What is the refund window?", {"slots": [S("S1", "refund window", ["refund window"])]})
    assert res["verdict"]["label"] == "CONFLICTING"
    vals = {a["value"] for a in res["verdict"]["slots"][0]["answers"]}
    assert vals == {"14 days", "21 days"}


# 12. Arithmetic is performed by code
def test_arithmetic_performed_by_code(env):
    intake = {
        "slots": [
            S("S1", "revenue in 2023", ["revenue"], "ANY", False),
            S("S2", "revenue in 2024", ["revenue"], "ANY", False)
        ],
        "calc": {"expr": "(S2 - S1) / S1 * 100", "label": "growth percentage"}
    }
    res, _ = run(env, "By what percent did revenue grow from 2023 to 2024?", intake)
    assert res["verdict"]["label"] == "ANSWER"
    assert res["verdict"]["calc"]["result"] == 30.0
    # Verify safe_eval directly with scale scaling
    assert safe_eval("S2 - S1", {"S1": to_number("$100 million"), "S2": to_number("$130 million")}) == 30000000.0


# 13. Budget exhaustion produces INSUFFICIENT or ANSWER+DISCLOSURE appropriately
def test_budget_exhaustion_and_disclosure(env):
    slots = [S(f"S{i}", f"fact {i}", [f"term{i}"], "ANY", False) for i in range(1, 4)]
    res, _ = run(env, "question needing too many calls", {"slots": slots})
    assert res["used"] <= 6
    assert res["verdict"]["label"].startswith("INSUFFICIENT")


# 14. System safely declines instead of hallucinating
def test_system_safely_declines_instead_of_hallucinating(env):
    res, _ = run(env, "Who founded the company?", {"slots": [S("S1", "founder", ["founder", "founded"], "ANY", False)]})
    assert res["verdict"]["label"] == "INSUFFICIENT_NOT_IN_DOC"
    assert res["used"] <= 3


# 15. Second-opinion check extension hook
def test_second_opinion_hook(env):
    page_text = "The revenue in 2024 was $130 million."
    items = [{"slot": "S1", "quote": "The revenue in 2024 was $130 million.", "value": "$130 million"}]
    
    # Second opinion validator that rejects slot S1 because of semantic mismatch
    def reject_validator(sid, val, quote, text):
        return False

    acc_rej, dropped_rej = verify(items, page_text, {"S1"}, second_opinion=reject_validator)
    assert len(acc_rej) == 0
    assert "second-opinion check failed" in dropped_rej[0][0]

    # Second opinion validator that accepts
    def accept_validator(sid, val, quote, text):
        return True

    acc_ok, dropped_ok = verify(items, page_text, {"S1"}, second_opinion=accept_validator)
    assert len(acc_ok) == 1
    assert acc_ok[0]["value"] == "$130 million"


# 16. Docket serialization
def test_docket_serialization(env):
    res, _ = run(env, "What is the timeout?", {"slots": [S("S1", "timeout", ["timeout"])]})
    docket_dict = res["docket"]
    assert isinstance(docket_dict, dict)
    assert "slots" in docket_dict
    assert "testimony" in docket_dict
    assert "pages_read" in docket_dict
    assert len(docket_dict["testimony"]) >= 1


# 17. Real 204-Page Sample PDF test
LOCAL_SAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "CSCI415009_V2.pdf")
DOWNLOADS_SAMPLE = r"c:\Users\admin\Downloads\CSCI415009_V2.pdf"
SAMPLE = LOCAL_SAMPLE if os.path.exists(LOCAL_SAMPLE) else (DOWNLOADS_SAMPLE if os.path.exists(DOWNLOADS_SAMPLE) else "/mnt/user-data/uploads/CSCI415009_V2.pdf")


@pytest.mark.skipif(not os.path.exists(SAMPLE), reason="sample pdf not present")
def test_real_sample_pdf_204_pages():
    t = DocumentTools()
    d = t.add_pdf(SAMPLE)
    info = t.info(d)
    assert info["pages"] == 204
    # Test literal search
    assert 25 in t.search_keyword(d, "admissible")["pages"]
    assert 152 in t.search_keyword(d, "value iteration")["pages"]
    assert t.search_keyword(d, "controversial")["pages"] == [1]     # de-hyphenation works
    # Test headings extraction
    headings = t.list_headings(d)["headings"]
    assert len(headings) >= 20
    assert any("Search" in h["title"] for h in headings)
