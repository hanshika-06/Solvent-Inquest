from dotenv import load_dotenv
load_dotenv()
from inquest.tools import DocumentTools
from inquest.controller import investigate
from inquest.llm import LLM

tools = DocumentTools()
doc = tools.add_pdf("CSCI415009_V2.pdf")
llm = LLM()

q = "What game was played by Arthur Samuel's learning program and how did it learn to play better?"
print("Investigating:", q)
res = investigate(q, tools, doc, llm)
print("Verdict:", res["verdict"]["label"])
print("Calls used:", res["used"], "/ 6")
for c in res["calls"]:
    found_str = str(c["found"]).encode("ascii", "replace").decode("ascii")
    print("  Call", c["n"], c["tool"], c["args"], "->", c["summary"], "found:", found_str)
print("Answer:\n", res["answer"].encode("ascii", "replace").decode("ascii"))
