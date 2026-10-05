"""Verdict rules: deterministic mapping from slot statuses to ANSWER / CONFLICTING / INSUFFICIENT."""
import ast
import operator
import re

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg}
_SCALE = {"million": 1e6, "mn": 1e6, "m": 1e6, "billion": 1e9, "bn": 1e9, "b": 1e9, "thousand": 1e3, "k": 1e3,
          "crore": 1e7, "lakh": 1e5}


def to_number(v):
    m = re.search(r"(-?\d[\d,]*\.?\d*)\s*(million|billion|thousand|crore|lakh|mn|bn|m|b|k)?\b", v or "", re.I)
    if not m:
        return None
    x = float(m.group(1).replace(",", ""))
    return x * _SCALE.get((m.group(2) or "").lower(), 1)


def safe_eval(expr, env):
    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.Name) and n.id in env:
            return env[n.id]
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        raise ValueError("unsupported expression")
    return ev(ast.parse(expr, mode="eval"))


def slot_report(d, s):
    st = d.status(s)
    r = d.resolve(s)
    rep = {"id": s.id, "desc": s.desc, "frame": s.frame, "status": st, "searched": list(s.tried.keys()),
           "answers": [], "disclosure_pages": []}
    if r["state"] == "SETTLED":
        by_id = {t.id: t for t in d.testimony}
        for a in r["answers"]:
            ids = list(a["ids"])
            for c in a["chain"]:
                for i in c["ids"]:
                    if i not in ids:
                        ids.append(i)
            rep["answers"].append({
                "value": a["value"], "scope": a["scope"], "pages": a["pages"], "link": a["link"], "why": a["why"],
                "chain": [{"value": c["value"], "pages": c["pages"]} for c in a["chain"]],
                "quotes": [{"page": by_id[i].page, "quote": by_id[i].quote} for i in ids]})
        if s.contestable:
            rep["disclosure_pages"] = d.unread(s)
        rep["outcome"] = "ANSWER"
    elif r["state"] == "FORK":
        by_id = {t.id: t for t in d.testimony}
        rep["outcome"] = "CONFLICTING"
        rep["answers"] = [{"value": by_id[i].value, "pages": [by_id[i].page], "quotes": [{"page": by_id[i].page, "quote": by_id[i].quote}]}
                          for i in r["testimony"]]
    elif st == "SILENT":
        rep["outcome"] = "INSUFFICIENT_NOT_IN_DOC"
    elif st == "EXHAUSTED":
        rep["outcome"] = "INSUFFICIENT_NOT_STATED"
    else:  # EMPTY / LEADS / DEFERRED  -> ran out of budget
        rep["outcome"] = "INSUFFICIENT_BUDGET"
        rep["disclosure_pages"] = d.unread(s) if st == "LEADS" else []
    return rep


def decide(d):
    reports = [slot_report(d, s) for s in (d.slots[i] for i in d.order)]
    outs = [r["outcome"] for r in reports]
    if not reports:
        label = "NO_QUESTION"
    elif all(o == "ANSWER" for o in outs):
        label = "ANSWER_WITH_DISCLOSURE" if any(r["disclosure_pages"] for r in reports) else "ANSWER"
    elif any(o == "CONFLICTING" for o in outs):
        label = "CONFLICTING"
    elif any(o == "ANSWER" for o in outs):
        label = "PARTIAL"
    elif any(o == "INSUFFICIENT_BUDGET" for o in outs):
        label = "INSUFFICIENT_BUDGET"
    elif any(o == "INSUFFICIENT_NOT_STATED" for o in outs):
        label = "INSUFFICIENT_NOT_STATED"
    else:
        label = "INSUFFICIENT_NOT_IN_DOC"
    calc = None
    if d.calc and label in ("ANSWER", "ANSWER_WITH_DISCLOSURE"):
        env = {}
        for r in reports:
            n = to_number(r["answers"][0]["value"]) if r["answers"] else None
            if n is None:
                env = None
                break
            env[r["id"]] = n
        if env is not None:
            try:
                res = safe_eval(d.calc["expr"], env)
                calc = {"label": d.calc.get("label", ""), "expr": d.calc["expr"], "result": round(res, 4)}
            except Exception as e:
                calc = None
    return {"label": label, "slots": reports, "calc": calc, "assumption": d.assumption,
            "quarantine": d.quarantine}
