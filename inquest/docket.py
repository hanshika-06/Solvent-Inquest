"""Docket: all state for ONE question. Slots, leads, testimony, succession chains, closure cost, slack."""
import re
import bisect
from dataclasses import dataclass, field

FLOOD = 6
STOP = set("the a an of in on for to and or is are was were be what which who how when where does do did by with "
           "from at as that this it its their his her current currently latest value number amount per".split())

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def tokens(s):
    return {w for w in re.findall(r"[a-z0-9]{3,}", (s or "").casefold()) if w not in STOP}


def canon(v):
    s = (v or "").casefold()
    s = re.sub(r"[,$€£₹]", "", s)
    s = re.sub(r"\s+", " ", s).strip().rstrip(".")
    return s


def parse_when(s):
    if not s:
        return None
    s = s.casefold()
    m = re.search(r"\b((?:19|20)\d{2})-(\d{1,2})", s)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    y = re.search(r"\b((?:19|20)\d{2})\b", s)
    if not y:
        return None
    mon = 0
    for k, v in MONTHS.items():
        if re.search(r"\b" + k, s):
            mon = v
            break
    return (int(y.group(1)), mon)


@dataclass
class Testimony:
    id: str
    page: int
    slot: str
    quote: str
    value: str
    bearing: str = "DIRECT"
    modality: str = "ASSERTED"
    scope: tuple = ()
    as_of: str = None
    cue: str = "NONE"
    cue_word: str = None
    from_value: str = None
    standing: str = "LIVE"

    @property
    def strength(self):
        return "STRONG" if (self.bearing == "DIRECT" and self.modality == "ASSERTED") else "WEAK"

    def to_dict(self):
        return {
            "id": self.id,
            "page": self.page,
            "slot": self.slot,
            "quote": self.quote,
            "value": self.value,
            "bearing": self.bearing,
            "modality": self.modality,
            "scope": list(self.scope),
            "as_of": self.as_of,
            "cue": self.cue,
            "cue_word": self.cue_word,
            "from_value": self.from_value,
            "standing": self.standing,
            "strength": self.strength,
        }


@dataclass
class Slot:
    id: str
    desc: str
    frame: str = "CURRENT"          # CURRENT | ORIGINAL | ANY | AT:<year>
    contestable: bool = False
    terms: list = field(default_factory=list)
    tried: dict = field(default_factory=dict)       # casefolded term -> hit pages
    heading_leads: list = field(default_factory=list)
    deferred: bool = False

    def to_dict(self):
        return {
            "id": self.id,
            "desc": self.desc,
            "frame": self.frame,
            "contestable": self.contestable,
            "terms": list(self.terms),
            "tried": {k: list(v) for k, v in self.tried.items()},
            "heading_leads": list(self.heading_leads),
            "deferred": self.deferred,
        }


class Docket:
    def __init__(self, question):
        self.question = question
        self.slots = {}
        self.order = []
        self.pages_read = set()
        self.testimony = []
        self.headings = None
        self._hpages = []
        self.term_hits = {}
        self.quarantine = []
        self.calc = None
        self.assumption = None
        self._tid = 0

    # ---------- construction ----------
    def add_slot(self, slot):
        self.slots[slot.id] = slot
        self.order.append(slot.id)

    def active(self):
        return [self.slots[i] for i in self.order if not self.slots[i].deferred]

    def add_testimony(self, **kw):
        self._tid += 1
        t = Testimony(id=f"T{self._tid}", **kw)
        self.testimony.append(t)
        return t

    # ---------- leads ----------
    def leads(self, s):
        pages = {p for ps in s.tried.values() for p in ps} | set(s.heading_leads)
        return sorted(pages)

    def unread(self, s):
        return [p for p in self.leads(s) if p not in self.pages_read]

    def next_term(self, s):
        for t in s.terms:
            if t.casefold() not in s.tried:
                return t
        return None

    def apply_search(self, term, pages):
        k = term.casefold()
        self.term_hits[k] = list(pages)
        for s in self.slots.values():
            if k in [t.casefold() for t in s.terms] and k not in s.tried:
                s.tried[k] = list(pages)

    def set_headings(self, headings):
        self.headings = sorted(headings, key=lambda h: h["page"])
        self._hpages = [h["page"] for h in self.headings]
        self.heading_rescue()

    def section_score(self, s, page):
        if not self.headings:
            return 0
        i = bisect.bisect_right(self._hpages, page) - 1
        if i < 0:
            return 0
        return len(tokens(self.headings[i]["title"]) & (tokens(s.desc) | tokens(" ".join(s.terms))))

    def heading_rescue(self):
        """Unknown-terminology path: a silent slot gets leads from heading titles that share words with it."""
        if not self.headings:
            return
        for s in self.active():
            if self.leads(s) or self.next_term(s) and len(s.tried) < 2:
                continue
            want = tokens(s.desc) | tokens(" ".join(s.terms))
            scored = sorted(((len(tokens(h["title"]) & want), h["page"]) for h in self.headings), reverse=True)
            s.heading_leads = [p for sc, p in scored if sc >= 1][:2]

    # ---------- read ordering (bookend probes) ----------
    def read_order(self, s):
        un = self.unread(s)
        if not un:
            return []
        allp = self.leads(s)
        flood = len(allp) > FLOOD
        sec = {p: self.section_score(s, p) for p in un}
        late_first = s.frame != "ORIGINAL"
        if s.contestable and not flood:
            first, last = allp[0], allp[-1]
            pref = [last, first] if late_first else [first, last]
            order = []
            for p in pref:
                if p in un and p not in order:
                    order.append(p)
            rest = sorted([p for p in un if p not in order], key=lambda p: (-sec[p], p))
            return order + rest
        key = (lambda p: (-sec[p], -p)) if (s.contestable and late_first) else (lambda p: (-sec[p], p))
        return sorted(un, key=key)

    def unread_bookends(self, s):
        allp = self.leads(s)
        if not allp:
            return []
        ends = {allp[0], allp[-1]}
        return [p for p in self.read_order(s) if p in ends]

    # ---------- succession chains / contradiction logic ----------
    def resolve(self, s):
        ts = [t for t in self.testimony if t.slot == s.id and t.strength == "STRONG"]
        if not ts:
            return {"state": "NONE"}
        has_signal = any(t.cue in ("REPLACES", "PRIOR") or t.as_of for t in ts)
        groups = {}
        for t in ts:
            key = () if has_signal else tuple(sorted(x.casefold().strip() for x in t.scope))
            groups.setdefault(key, []).append(t)
        answers = []
        for key, g in groups.items():
            r = self._chain(g, s.frame)
            if r is None:
                return {"state": "FORK", "testimony": [t.id for t in ts],
                        "values": sorted({t.value for t in ts})}
            r["scope"] = list(key)
            answers.append(r)
        if len(answers) > 1 and len({canon(a["value"]) for a in answers}) == 1:
            answers = answers[:1]
        return {"state": "SETTLED", "answers": answers}

    def _chain(self, g, frame):
        by = {}
        for t in g:
            by.setdefault(canon(t.value), []).append(t)

        def view(c):
            ts = by[c]
            return {"value": ts[0].value, "pages": sorted({t.page for t in ts}), "ids": [t.id for t in ts]}

        def pick(order, link, why):
            chain = [view(c) for c in order]
            if frame == "ORIGINAL":
                sel = chain[0]
            elif frame.startswith("AT:"):
                sel = chain[-1]
                try:
                    yr = int(frame[3:])
                    dated = [(max(parse_when(t.as_of) or (0, 0) for t in by[c]), c) for c in order]
                    ok = [c for w, c in dated if w and w[0] <= yr]
                    if ok:
                        sel = view(ok[-1])
                except ValueError:
                    pass
            else:
                sel = chain[-1]
            return {"value": sel["value"], "pages": sel["pages"], "ids": sel["ids"],
                    "chain": chain, "link": link, "why": why}

        if len(by) == 1:
            c = next(iter(by))
            return pick([c], "agree", "all supporting statements agree")
        # (a) explicit as-of dates on every distinct value
        when = {c: max((parse_when(t.as_of) for t in ts if parse_when(t.as_of)), default=None) for c, ts in by.items()}
        if all(when.values()) and len(set(when.values())) == len(when):
            order = sorted(by, key=lambda c: when[c])
            return pick(order, "dated", "later as-of date wins")
        # (b) explicit 'from X to Y' links
        succ = {}
        for t in g:
            if t.cue == "REPLACES" and t.from_value and canon(t.from_value) in by and canon(t.from_value) != canon(t.value):
                succ[canon(t.from_value)] = canon(t.value)
        if succ:
            heads = [c for c in by if c not in succ]
            tails = [c for c in by if c not in succ.values()]
            if len(heads) == 1 and len(tails) == 1:
                order, cur, guard = [tails[0]], tails[0], 0
                while cur in succ and guard < 20:
                    cur = succ[cur]
                    order.append(cur)
                    guard += 1
                if len(order) == len(by):
                    return pick(order, "confirmed", "update statement names the old value it replaces")
        # (c) exactly one value carries explicit update wording
        rep = {canon(t.value) for t in g if t.cue == "REPLACES"}
        if len(rep) == 1:
            head = next(iter(rep))
            others = sorted((c for c in by if c != head), key=lambda c: min(t.page for t in by[c]))
            cw = next(t for t in g if t.cue == "REPLACES" and canon(t.value) == head)
            return pick(others + [head], "assumed",
                        f"p.{cw.page} uses update wording ({cw.cue_word!r}); old value not named, so link is assumed")
        # (d) older values are explicitly marked as prior
        prior = [c for c, ts in by.items() if all(t.cue == "PRIOR" for t in ts)]
        non = [c for c in by if c not in prior]
        if prior and len(non) == 1:
            order = sorted(prior, key=lambda c: min(t.page for t in by[c])) + non
            return pick(order, "prior", "older value is explicitly described as previous/former")
        return None  # unresolved -> FORK

    # ---------- status & closure cost ----------
    def status(self, s):
        if s.deferred:
            return "DEFERRED"
        r = self.resolve(s)
        if r["state"] == "FORK":
            return "FORKED"
        if r["state"] == "SETTLED":
            return "TESTIFIED" if (s.contestable and self.unread_bookends(s)) else "SETTLED"
        if not self.leads(s):
            if self.next_term(s) and len(s.tried) < 2:
                return "EMPTY"
            return "SILENT"
        return "LEADS" if self.unread(s) else "EXHAUSTED"

    def reads_needed(self, s):
        st = self.status(s)
        un = self.read_order(s)
        if st == "TESTIFIED":
            return len(self.unread_bookends(s))
        if st == "FORKED":
            return 1 if un else 0
        if st == "LEADS":
            return min(2, len(un)) if s.contestable else 1
        return 0

    def closure(self):
        searches, reads = set(), 0
        for s in self.active():
            st = self.status(s)
            if st == "EMPTY":
                t = self.next_term(s)
                if t and t.casefold() not in self.term_hits:
                    searches.add(t.casefold())
                reads += 1
            else:
                reads += self.reads_needed(s)
        return len(searches) + reads

    def open_slots(self):
        return [s for s in self.active() if self.status(s) in ("EMPTY", "LEADS", "TESTIFIED", "FORKED")]

    def refresh_standing(self):
        for t in self.testimony:
            t.standing = "LIVE"
        for s in self.slots.values():
            r = self.resolve(s)
            if r["state"] == "SETTLED":
                for a in r["answers"]:
                    keep = set(a["ids"])
                    for c in a["chain"]:
                        for i in c["ids"]:
                            if i not in keep:
                                tt = next(x for x in self.testimony if x.id == i)
                                tt.standing = "SUPERSEDED" if s.frame != "ORIGINAL" else "HISTORICAL"
            elif r["state"] == "FORK":
                for i in r["testimony"]:
                    next(x for x in self.testimony if x.id == i).standing = "FORKED"

    def to_dict(self):
        return {
            "question": self.question,
            "slots": [s.to_dict() | {"status": self.status(s), "leads": self.leads(s), "unread": self.unread(s)}
                      for s in (self.slots[i] for i in self.order)],
            "order": list(self.order),
            "pages_read": sorted(self.pages_read),
            "testimony": [t.to_dict() for t in self.testimony],
            "closure": self.closure(),
            "term_hits": {k: list(v) for k, v in self.term_hits.items()},
            "quarantine": list(self.quarantine),
            "calc": self.calc,
            "assumption": self.assumption,
        }

    def to_json(self, indent=2):
        import json
        return json.dumps(self.to_dict(), indent=indent, default=str)
