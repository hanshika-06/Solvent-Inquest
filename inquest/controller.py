"""Solvent Inquest controller. Plain Python loop. The solvency gate decides every call."""
import json
import re
from dataclasses import dataclass, field

from .desk import Desk, MAX_CALLS
from .docket import Docket, Slot, tokens
from .llm import parse_json
from . import prompts, reader, verdict


@dataclass
class Move:
    tool: str
    args: dict
    kind: str            # closing_read | closing_search | explore
    slot_ids: list
    reason: str


def build_moves(d, desk):
    moves, seen = [], set()

    def add(mv):
        k = (mv.tool, json.dumps(mv.args, sort_keys=True))
        if k not in seen:
            seen.add(k)
            moves.append(mv)

    reads = []
    for s in d.active():
        st = d.status(s)
        if st in ("LEADS", "TESTIFIED", "FORKED"):
            order = d.unread_bookends(s) if st == "TESTIFIED" else d.read_order(s)
            if order:
                why = {"LEADS": "read best lead", "TESTIFIED": "bookend probe (check for update/original)",
                       "FORKED": "read another lead to resolve fork"}[st]
                reads.append((s, order[0], why))
    cover = lambda p: sum(1 for x in d.active() if p in d.unread(x))
    reads.sort(key=lambda r: -cover(r[1]))
    for s, p, why in reads:
        add(Move("get_page", {"page_number": p}, "closing_read", [s.id], f"{s.id}: {why}"))
    for s in d.active():
        if d.status(s) == "EMPTY":
            t = d.next_term(s)
            if t:
                add(Move("search_keyword", {"keyword": t}, "closing_search", [s.id], f"{s.id}: first lead search"))
    # explore: headings (map) for floods and for silent slots (unknown terminology)
    if d.headings is None:
        flooded = [s.id for s in d.active() if d.status(s) == "LEADS" and len(d.leads(s)) > 6]
        silent = [s.id for s in d.active() if d.status(s) == "SILENT" and not d.leads(s)]
        if flooded:
            add(Move("list_headings", {}, "explore", flooded, "flood: too many hit pages, need a map"))
        elif silent:
            add(Move("list_headings", {}, "explore", silent, "silent term: look for matching section title"))
    return moves


def gate(mv, d, desk):
    rem, clos = desk.remaining, d.closure()
    slack = rem - clos
    if rem <= 0:
        return False, "no calls remaining", slack
    if rem == 1 and mv.tool != "get_page":
        return False, "reserve rule: last call must be a read", slack
    if mv.kind == "closing_read":
        return True, "closing read", slack
    if mv.kind == "closing_search":
        return (rem >= 2), ("closing search" if rem >= 2 else "no call left to read the result"), slack
    if slack >= 1:
        return True, f"explore allowed (slack {slack})", slack
    return False, f"would leave slack {slack - 1} (insolvent)", slack


def _fallback_intake(question):
    words = [w for w in re.findall(r"[A-Za-z0-9\-]{4,}", question) if w.casefold() not in tokens("") and w.casefold() not in
             {"what", "which", "when", "where", "does", "that", "this", "with", "from", "have", "about", "document"}]
    words = sorted(set(words), key=lambda w: -len(w))[:3]
    return {"slots": [{"id": "S1", "desc": question[:120], "frame": "CURRENT", "contestable": True, "terms": words}],
            "calc": None, "assumption": None}


def investigate(question, tools, doc_id, llm, on_event=None, history_hint=None, max_calls=MAX_CALLS,
                use_list_documents=False, logger=None, second_opinion=None):
    events = []

    def emit(**e):
        events.append(e)
        if on_event:
            on_event(e)

    info = tools.info(doc_id)
    desk = Desk(tools, doc_id, info["pages"], max_calls=max_calls, logger=logger)
    d = Docket(question)

    # ---------- INTAKE (no page text ever) ----------
    u = f"Question: {question}"
    if history_hint:
        u = f"Previous question (for context only): {history_hint}\n" + u
    plan = parse_json(llm.complete(prompts.INTAKE_SYSTEM, u, 800))
    if not isinstance(plan, dict) or not isinstance(plan.get("slots"), list):
        plan = _fallback_intake(question)
        emit(type="note", text="intake JSON invalid: fallback plan used")
    for i, sd in enumerate(plan["slots"][:3], start=1):
        terms = [str(t).strip() for t in (sd.get("terms") or []) if str(t).strip()][:3]
        frame = str(sd.get("frame") or "CURRENT").upper()
        if not (frame in ("CURRENT", "ORIGINAL", "ANY") or re.fullmatch(r"AT:\d{4}", frame)):
            frame = "CURRENT"
        d.add_slot(Slot(id=f"S{i}", desc=str(sd.get("desc") or question)[:160], frame=frame,
                        contestable=bool(sd.get("contestable")), terms=terms))
    c = plan.get("calc")
    if isinstance(c, dict) and c.get("expr"):
        d.calc = {"expr": str(c["expr"]), "label": str(c.get("label", ""))}
    if plan.get("assumption"):
        d.assumption = str(plan["assumption"])[:200]

    if use_list_documents and d.slots:
        r = desk.call("list_documents")
        emit(type="call", n=r.get("n"), tool="list_documents", args={}, summary=desk.trace[-1]["summary"] if r["ok"] else r["reason"])

    # triage: if closure exceeds budget from the start, defer lowest-priority slots
    while d.closure() > desk.remaining and len(d.active()) > 1:
        victim = d.active()[-1]
        victim.deferred = True
        emit(type="note", text=f"insolvent at intake: slot {victim.id} deferred (budget-limited)")
    emit(type="intake", slots=[{"id": s.id, "desc": s.desc, "frame": s.frame, "contestable": s.contestable,
                                "terms": s.terms, "deferred": s.deferred} for s in d.slots.values()],
         closure=d.closure(), slack=desk.remaining - d.closure(), calc=d.calc, assumption=d.assumption)

    # ---------- INQUEST LOOP ----------
    while desk.remaining > 0 and d.open_slots() and desk.rejections < 3:
        chosen = None
        for mv in build_moves(d, desk):
            ok, why, slack = gate(mv, d, desk)
            emit(type="decision", tool=mv.tool, args=mv.args, kind=mv.kind, admitted=ok, why=why,
                 reason=mv.reason, slack=slack, remaining=desk.remaining, closure=d.closure())
            if ok:
                chosen = mv
                break
        if not chosen:
            emit(type="note", text="no admissible move: stopping")
            break
        res = desk.call(chosen.tool, **chosen.args)
        if not res["ok"]:
            emit(type="note", text=f"desk refused ({res['reason']}); costs nothing")
            continue
        n, out = res["n"], res["result"]
        emit(type="call", n=n, tool=chosen.tool, args=chosen.args, summary=desk.trace[-1]["summary"])
        if chosen.tool == "search_keyword":
            d.apply_search(chosen.args["keyword"], out.get("pages", []))
        elif chosen.tool == "list_headings":
            hs = []
            for h in out.get("headings", []):
                if reader.is_directive(h["title"]):
                    d.quarantine.append({"page": h["page"], "snippet": h["title"][:120], "where": "heading"})
                    emit(type="quarantine", page=h["page"], snippet=h["title"][:120])
                else:
                    hs.append(h)
            d.set_headings(hs)
        elif chosen.tool == "get_page":
            page, text = chosen.args["page_number"], out["text"]
            d.pages_read.add(page)
            for snip in reader.scan_directives(text):
                d.quarantine.append({"page": page, "snippet": snip, "where": "page"})
                emit(type="quarantine", page=page, snippet=snip)
            slots = [s for s in d.active() if d.status(s) in ("EMPTY", "LEADS", "TESTIFIED", "FORKED")] or d.active()
            items = reader.read(llm, page, text, slots)
            acc, dropped = reader.verify(items, text, {s.id for s in slots}, second_opinion=second_opinion)
            for a in acc:
                t = d.add_testimony(page=page, **a)
                desk.annotate(n, f"{t.slot}: {t.value} [{t.strength}]")
                emit(type="testimony", id=t.id, page=page, slot=t.slot, value=t.value, quote=t.quote,
                     strength=t.strength, cue=t.cue, cue_word=t.cue_word, modality=t.modality)
            for why, q in dropped:
                emit(type="dropped", page=page, why=why, quote=q)
            d.refresh_standing()
        emit(type="state", closure=d.closure(), slack=desk.remaining - d.closure(), used=desk.used,
             statuses={s.id: d.status(s) for s in d.slots.values()})

    desk.audit()
    d.refresh_standing()

    # ---------- VERDICT (code) then WRITER (LLM sees testimony only) ----------
    v = verdict.decide(d)
    emit(type="verdict", label=v["label"])
    payload = {"question": question, **{k: v[k] for k in ("label", "slots", "calc", "assumption")}}
    answer = llm.complete(prompts.WRITER_SYSTEM, "VERDICT_JSON:\n" + json.dumps(payload, ensure_ascii=False), 700).strip()
    return {
        "question": question, "answer": answer, "verdict": v, "calls": desk.trace, "rejected": desk.rejected,
        "used": desk.used, "max_calls": desk.max_calls, "events": events,
        "testimony": [t.__dict__ | {"strength": t.strength} for t in d.testimony],
        "docket": d.to_dict(),
        "quarantine": d.quarantine, "pages_read": sorted(d.pages_read),
    }
