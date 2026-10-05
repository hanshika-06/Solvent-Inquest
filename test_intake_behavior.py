from dotenv import load_dotenv
load_dotenv()
from inquest.tools import DocumentTools
from inquest.controller import investigate
from inquest.llm import LLM

tools = DocumentTools()
doc = tools.add_pdf("CSCI415009_V2.pdf")
llm = LLM()

print("=" * 60)
print("Case 1: User types non-question keywords: 'prime minister'")
print("=" * 60)
res1 = investigate("prime minister", tools, doc, llm)
print("Slots generated at intake:", len(res1["docket"]["slots"]))
print("Calls used:", res1["used"])
print("Verdict:", res1["verdict"]["label"])
print("Answer:\n", res1["answer"].encode("ascii", "replace").decode("ascii"))

print("\n" + "=" * 60)
print("Case 2: User asks an actual question: 'Who is the prime minister?'")
print("=" * 60)
res2 = investigate("Who is the prime minister mentioned in the document?", tools, doc, llm)
print("Slots generated at intake:", len(res2["docket"]["slots"]))
print("Calls used:", res2["used"])
print("Verdict:", res2["verdict"]["label"])
for c in res2["calls"]:
    print("  Call", c["n"], c["tool"], c["args"], "->", c["summary"])
print("Answer:\n", res2["answer"].encode("ascii", "replace").decode("ascii"))
