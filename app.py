import json
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import streamlit as st

from inquest.controller import investigate
from inquest.llm import LLM
from inquest.tools import DocumentTools

st.set_page_config(page_title="Solvent Inquest", page_icon="⚖️", layout="wide")

BADGE = {
    "ANSWER": ("🟢", "SUPPORTED"), "ANSWER_WITH_DISCLOSURE": ("🟢", "SUPPORTED (unread pages disclosed)"),
    "CONFLICTING": ("🟠", "CONFLICTING"), "PARTIAL": ("🟡", "PARTIAL"),
    "INSUFFICIENT_NOT_IN_DOC": ("⚪", "INSUFFICIENT: not in document"),
    "INSUFFICIENT_NOT_STATED": ("⚪", "INSUFFICIENT: pages read, not stated"),
    "INSUFFICIENT_BUDGET": ("⚪", "INSUFFICIENT: budget-limited"), "NO_QUESTION": ("⚪", "NO DOCUMENT QUESTION"),
}


def fmt_event(e):
    t = e["type"]
    if t == "intake":
        sl = "; ".join(f"{s['id']}={s['desc']} [{s['frame']}{', contestable' if s['contestable'] else ''}]" for s in e["slots"])
        return f"**INTAKE** slots: {sl} · closure cost {e['closure']} · slack {e['slack']}"
    if t == "decision":
        mark = "✅" if e["admitted"] else "⛔ declined"
        return f"{mark} `{e['tool']}({', '.join(f'{k}={v}' for k, v in e['args'].items())})`: {e['reason']} · {e['why']} (remaining {e['remaining']}, closure {e['closure']})"
    if t == "call":
        args = ", ".join(f"{k}={v}" for k, v in e["args"].items())
        return f"**CALL {e['n']}** `{e['tool']}({args})` → {e['summary']}"
    if t == "testimony":
        cue = f" · cue: *{e['cue_word']}*" if e["cue"] != "NONE" else ""
        return f"📄 p.{e['page']} {e['slot']} = **{e['value']}** [{e['strength']}]{cue}"
    if t == "quarantine":
        return f"⚠️ instruction-like text on p.{e['page']}: treated as data, not obeyed: _{e['snippet'][:90]}_"
    if t == "dropped":
        return f"🗑 evidence dropped (p.{e['page']}): {e['why']}"
    if t == "state":
        return f"state → used {e['used']}/6 · closure {e['closure']} · slack {e['slack']} · {e['statuses']}"
    if t == "verdict":
        return f"**VERDICT** {e['label']}"
    return f"_{e.get('text', '')}_"


def render_docket(res, key="active"):
    v = res["verdict"]
    icon, label = BADGE.get(v["label"], ("⚪", v["label"]))
    used = res["used"]
    st.markdown(f"### {icon} {label}")
    st.markdown("**Budget** " + "●" * used + "○" * (res["max_calls"] - used) + f"  ({used}/{res['max_calls']} calls)")
    for s in v["slots"]:
        with st.container(border=True):
            st.markdown(f"**{s['id']}** {s['desc']} · `{s['status']}` → **{s['outcome']}**")
            for a in s["answers"]:
                scope = f" ({', '.join(a['scope'])})" if a.get("scope") else ""
                st.markdown(f"**{a['value']}**{scope} · pages {a['pages']}")
                if a.get("chain") and len(a["chain"]) > 1:
                    st.caption("succession: " + " → ".join(f"{c['value']} (p.{c['pages']})" for c in a["chain"]) + f" · {a['why']}")
                for q in a["quotes"][:3]:
                    st.caption(f"p.{q['page']}: “{q['quote']}”")
            if s["disclosure_pages"]:
                st.caption(f"unread pages that mention it: {s['disclosure_pages']}")
            if s["searched"]:
                st.caption("searched: " + ", ".join(s["searched"]))
    if res["quarantine"]:
        st.warning("Quarantine: instruction-like text was found and **not obeyed**:\n" +
                   "\n".join(f"- p.{q['page']}: {q['snippet'][:100]}" for q in res["quarantine"]))
    with st.expander("Full tool trace (every call)", expanded=True):
        for c in res["calls"]:
            args = ", ".join(f"{k}={v}" for k, v in c["args"].items())
            st.markdown(f"**CALL {c['n']}** `{c['tool']}({args})` → {c['summary']}" + ("  \n" + " · ".join(c["found"]) if c["found"] else ""))
        if res["rejected"]:
            st.caption("refused by desk (cost nothing): " + json.dumps(res["rejected"]))
    with st.expander("Decision log (gate)"):
        for e in res["events"]:
            st.markdown(fmt_event(e))
    st.download_button("Download trace JSON", json.dumps(res, indent=2, default=str), file_name="trace.json", key=f"dl_{key}")


# ---------------- sidebar ----------------
with st.sidebar:
    st.title("⚖️ Solvent Inquest")
    st.caption("Evidence-first, budget-solvent document agent.\n\n6 tool calls per question.")
    st.divider()
    up = st.file_uploader("Upload PDF", type=["pdf"])
    if up is not None:
        sig = (up.name, up.size)
        if st.session_state.sig != sig:
            with st.spinner("Preparing the tool interface (indexing pages for the four tools)..."):
                tools = DocumentTools()
                st.session_state.doc_id = tools.add_pdf(up.getvalue(), title=up.name)
                st.session_state.tools, st.session_state.sig, st.session_state.messages = tools, sig, []
        info = st.session_state.tools.info(st.session_state.doc_id)
        st.success(f"**{info['title']}**\n\n{info['pages']} pages ready")
    st.divider()
    if st.button("Clear chat"):
        st.session_state.messages = []
        st.rerun()

if "tools" not in st.session_state:
    st.session_state.tools, st.session_state.doc_id, st.session_state.sig, st.session_state.messages = DocumentTools(), None, None, []

st.header("Ask the document")
if st.session_state.doc_id is None:
    st.info("Upload a PDF in the sidebar to begin.")

for idx, m in enumerate(st.session_state.messages):
    with st.chat_message(m["role"]):
        st.markdown(m["content"])
        if m.get("result"):
            render_docket(m["result"], key=f"msg_{idx}")

q = st.chat_input("Ask a question about the document", disabled=st.session_state.doc_id is None)
if q:
    st.session_state.messages.append({"role": "user", "content": q})
    with st.chat_message("user"):
        st.markdown(q)
    with st.chat_message("assistant"):
        try:
            llm = LLM()
        except Exception:
            st.error("LLM service is not configured. Please configure the server environment.")
            st.stop()
        prev = next((m["content"] for m in reversed(st.session_state.messages[:-1]) if m["role"] == "user"), None)
        with st.status("Investigating (each call is priced against the cost of closing the question)...", expanded=True) as status:
            try:
                res = investigate(q, st.session_state.tools, st.session_state.doc_id, llm,
                                  on_event=lambda e: status.write(fmt_event(e)), history_hint=prev)
                status.update(label=f"Done: {res['used']}/{res['max_calls']} calls used", state="complete")
            except Exception:
                status.update(label="Investigation halted", state="error")
                st.error("LLM service is not configured. Please configure the server environment.")
                st.stop()
        st.markdown(res["answer"])
        render_docket(res, key=f"msg_{len(st.session_state.messages)}")
    st.session_state.messages.append({"role": "assistant", "content": res["answer"], "result": res})
