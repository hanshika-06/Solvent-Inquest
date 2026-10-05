import sys, os, json
sys.path.insert(0, os.path.abspath("."))
sys.path.insert(0, os.path.abspath("tests"))

from inquest.tools import DocumentTools
from inquest.controller import investigate
from fake_llm import FakeLLM
from make_test_pdf import make

# 1. Create synthetic PDF
pdf_path = make("test_doc.pdf")
tools = DocumentTools()
doc_id = tools.add_pdf(pdf_path)

print("=" * 70)
print("DEMO 1: 'What is the timeout?' (Succession + Injection Quarantine)")
print("=" * 70)

q = "What is the timeout?"
intake = {
    "slots": [{"id": "S1", "desc": "timeout value", "frame": "CURRENT", "contestable": True, "terms": ["timeout"]}],
    "calc": None,
    "assumption": None
}
llm = FakeLLM(intake, compromised_reader=True)
res = investigate(q, tools, doc_id, llm)

print("\n--- DETAILED EVENT LOG ---")
for e in res["events"]:
    etype = e["type"].upper()
    details = {k: v for k, v in e.items() if k != "type"}
    print(f"[{etype:10}] {details}")

print("\n--- EXECUTED TOOL CALLS (HARD BUDGET = 6) ---")
for c in res["calls"]:
    print(f"Call #{c['n']}: {c['tool']}({c['args']}) -> {c['summary']} (found: {c['found']})")

print("\n--- QUARANTINE LOG (PROMPT INJECTION CAUGHT) ---")
for q_item in res["quarantine"]:
    print(f"Page {q_item['page']} ({q_item['where']}): {q_item['snippet']}")

print("\n--- DETERMINISTIC VERDICT OBJECT ---")
print(json.dumps(res["verdict"], indent=2))

print("\n--- FINAL WRITER ANSWER (WRITER NEVER SAW RAW PAGE TEXT) ---")
print(res["answer"])

print("\n" + "=" * 70)
print("DEMO 2: 'What is the refund window?' (Contradiction / Conflict Fork)")
print("=" * 70)

q2 = "What is the refund window?"
intake2 = {
    "slots": [{"id": "S1", "desc": "refund window", "frame": "CURRENT", "contestable": True, "terms": ["refund window"]}],
    "calc": None,
    "assumption": None
}
res2 = investigate(q2, tools, doc_id, FakeLLM(intake2))
print("Verdict:", res2["verdict"]["label"])
print("Slot Outcome:", res2["verdict"]["slots"][0]["outcome"])
print("Answers:", [a["value"] for a in res2["verdict"]["slots"][0]["answers"]])
print("Answer text:", res2["answer"])

print("\n" + "=" * 70)
print("DEMO 3: 'By what percent did revenue grow from 2023 to 2024?' (Deterministic Calculator)")
print("=" * 70)

q3 = "By what percent did revenue grow from 2023 to 2024?"
intake3 = {
    "slots": [
        {"id": "S1", "desc": "revenue in 2023", "frame": "ANY", "contestable": False, "terms": ["revenue"]},
        {"id": "S2", "desc": "revenue in 2024", "frame": "ANY", "contestable": False, "terms": ["revenue"]}
    ],
    "calc": {"expr": "(S2 - S1) / S1 * 100", "label": "percent change"},
    "assumption": None
}
res3 = investigate(q3, tools, doc_id, FakeLLM(intake3))
print("Verdict:", res3["verdict"]["label"])
print("Slot 1 (2023):", res3["verdict"]["slots"][0]["answers"][0]["value"])
print("Slot 2 (2024):", res3["verdict"]["slots"][1]["answers"][0]["value"])
print("Code-computed Calc:", res3["verdict"]["calc"])
print("Final Answer:", res3["answer"])
