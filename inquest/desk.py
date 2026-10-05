"""Petty-cash desk: the ONLY holder of the raw tools. Counts first, then executes. Hard cap = 6."""
import json
import time

TOOLS = ("list_documents", "list_headings", "get_page", "search_keyword")
MAX_CALLS = 6


class Desk:
    def __init__(self, tools, doc_id, page_count, max_calls=MAX_CALLS, logger=None):
        self.__raw = tools            # name-mangled: controller code never touches raw tools
        self.doc_id = doc_id
        self.page_count = page_count
        self.max_calls = max_calls
        self.used = 0
        self.rejections = 0
        self.trace = []               # every EXECUTED call
        self.rejected = []            # every refused call (costs nothing)
        self._keys = set()
        self._logger = logger         # hook for the organizers' call-logging wrapper

    @property
    def remaining(self):
        return self.max_calls - self.used

    @staticmethod
    def _key(name, args):
        if name == "search_keyword":
            return (name, str(args.get("keyword", "")).casefold().strip())
        if name == "get_page":
            return (name, args.get("page_number"))
        return (name,)

    def precheck(self, name, args):
        if name not in TOOLS:
            return f"unknown tool {name!r}"
        if self.remaining <= 0:
            return "budget exhausted"
        if self.remaining == 1 and name != "get_page":
            return "reserve rule: last call must be a get_page (nothing would be left to read the result)"
        if name == "get_page":
            p = args.get("page_number")
            if not isinstance(p, int) or not (1 <= p <= self.page_count):
                return f"page_number out of range 1..{self.page_count}"
        if name == "search_keyword" and not str(args.get("keyword", "")).strip():
            return "empty keyword"
        if self._key(name, args) in self._keys:
            return "duplicate call in this question"
        return None

    def call(self, name, **args):
        args.pop("doc_id", None)
        why = self.precheck(name, args)
        if why:
            self.rejections += 1
            self.rejected.append({"tool": name, "args": args, "reason": why})
            return {"ok": False, "reason": why}
        self.used += 1                                   # COUNT FIRST ...
        self._keys.add(self._key(name, args))
        fn = getattr(self.__raw, name)
        t0 = time.time()
        if name == "list_documents":
            result = fn()
        else:
            result = fn(self.doc_id, **args)             # ... THEN execute (doc_id is injected, not chosen)
        entry = {"n": self.used, "tool": name, "args": args, "ms": int((time.time() - t0) * 1000),
                 "summary": self._summarize(name, result), "found": []}
        self.trace.append(entry)
        if self._logger:
            try:
                self._logger(dict(entry))
            except Exception:
                pass
        return {"ok": True, "result": result, "n": self.used}

    @staticmethod
    def _summarize(name, result):
        if "error" in result if isinstance(result, dict) else False:
            return f"error: {result['error']}"
        if name == "search_keyword":
            return f"pages {result['pages']}" if result["pages"] else "no hits"
        if name == "get_page":
            return f"{len(result['text'])} chars"
        if name == "list_headings":
            return f"{len(result['headings'])} headings"
        return f"{len(result)} documents"

    def annotate(self, n, text):
        for e in self.trace:
            if e["n"] == n:
                e["found"].append(text)

    def audit(self):
        assert self.used <= self.max_calls and len(self.trace) <= self.max_calls, "BUDGET VIOLATION"
        return True
