"""Deterministic stand-in for the LLM so the harness (budget, gate, chains, verifier) is testable offline."""
import json
import re
from inquest.reader import CUE_LEXICON


class FakeLLM:
    def __init__(self, intake, compromised_reader=False):
        self.intake = intake
        self.compromised = compromised_reader
        self.n_calls = 0
        self.writer_inputs = []
        self.reader_inputs = []

    def complete(self, system, user, max_tokens=1000):
        self.n_calls += 1
        if "ROLE:INTAKE" in system:
            return json.dumps(self.intake)
        if "ROLE:READER" in system:
            self.reader_inputs.append(user)
            return json.dumps(self._read(user))
        if "ROLE:WRITER" in system:
            self.writer_inputs.append(user)
            v = json.loads(user.split("VERDICT_JSON:\n", 1)[1])
            vals = [a["value"] for s in v["slots"] for a in s["answers"]]
            return f"{v['label']}: " + "; ".join(vals) + (f" calc={v['calc']['result']}" if v.get("calc") else "")
        return ""

    def _read(self, user):
        slots = json.loads(re.search(r"SLOTS:\n(\[.*?\])\n", user, re.S).group(1))
        text = re.search(r"<<<PAGE \w+>>>\n(.*?)\n<<<END", user, re.S).group(1)
        items = []
        for sent in re.split(r"(?<=[.!?])\s+", text):
            if self.compromised and "IGNORE ALL" in sent.upper():
                items.append({"slot": slots[0]["id"], "quote": sent, "value": sent[:20], "bearing": "DIRECT",
                              "modality": "ASSERTED", "scope": [], "as_of": None, "cue": "NONE",
                              "cue_word": None, "from_value": None})
                continue
            for s in slots:
                years = re.findall(r"\b(?:19|20)\d{2}\b", s["desc"])
                if not any(t.casefold() in sent.casefold() for t in s["terms"]):
                    continue
                if years and not all(y in sent for y in years):
                    continue
                ms = [x for x in re.finditer(r"\$?\d[\d,]*\.?\d*(?:\s*(?:million|seconds|days|offices))?", sent)
                      if x.group().strip() not in years]
                if not ms:
                    continue
                withunit = [x for x in ms if re.search(r'[a-z]', x.group())]
                m = (withunit or ms)[-1]
                cue, cw = "NONE", None
                low = sent.casefold()
                for c in ("REPLACES", "PRIOR"):
                    for w in CUE_LEXICON[c]:
                        if re.search(r"\b" + re.escape(w) + r"\b", low):
                            cue, cw = c, w
                            break
                    if cw:
                        break
                items.append({"slot": s["id"], "quote": sent, "value": m.group().strip(), "bearing": "DIRECT",
                              "modality": "ASSERTED", "scope": [], "as_of": None, "cue": cue, "cue_word": cw,
                              "from_value": None})
        return {"testimony": items}
