# Mock knowledge base: TF-IDF search over EN/AR markdown docs in data/kb/.
#
# Arabic text gets light normalization (strip diacritics, unify alef/yaa/taa
# marbuta, drop the "ال" prefix) so simple word matching works in both languages.
import math
import random
import re
import time
from collections import Counter

from fastapi import FastAPI

from common.config import DATA_DIR
from services.chaos import Chaos

app = FastAPI(title="Knowledge Base")
chaos = Chaos("knowledge_base")
app.include_router(chaos.router)

WORD = re.compile(r"\w+", re.UNICODE)
DIACRITICS = re.compile(r"[ً-ْـ]")


def tokenize(text: str) -> list[str]:
    text = DIACRITICS.sub("", text.lower())
    text = re.sub("[إأآ]", "ا", text).replace("ة", "ه").replace("ى", "ي")
    tokens = []
    for t in WORD.findall(text):
        if t.startswith("ال") and len(t) > 4:
            t = t[2:]
        if len(t) > 1:
            tokens.append(t)
    return tokens


class Index:
    def __init__(self):
        self.docs: list[dict] = []
        self.tf: list[Counter] = []
        self.idf: dict[str, float] = {}
        self.built_at = 0.0

    def build(self) -> None:
        docs = []
        for path in sorted((DATA_DIR / "kb").glob("*.md")):
            text = path.read_text(encoding="utf-8")
            first = text.splitlines()[0] if text else path.stem
            body = "\n".join(text.splitlines()[1:]).strip()
            docs.append({"doc_id": path.stem, "title": first.lstrip("# ").strip(), "body": body})
        self.docs = docs
        self.tf = [Counter(tokenize(d["title"] + " " + d["body"])) for d in docs]
        df = Counter(t for tf in self.tf for t in tf)
        n = len(docs)
        self.idf = {t: math.log((1 + n) / (1 + c)) + 1 for t, c in df.items()}
        self.built_at = time.time()

    def search(self, q: str, k: int) -> list[dict]:
        terms = tokenize(q)
        scored = []
        for doc, tf in zip(self.docs, self.tf):
            length = math.sqrt(sum(tf.values())) or 1
            score = sum(tf[t] * self.idf.get(t, 0) for t in terms) / length
            if score > 0:
                scored.append((score, doc))
        scored.sort(key=lambda x: -x[0])
        return [{"doc_id": d["doc_id"], "title": d["title"], "snippet": self.snippet(d["body"], terms),
                 "score": round(s, 3)} for s, d in scored[:k]]

    def snippet(self, body: str, terms: list[str]) -> str:
        # The paragraph that matches the question best (most distinct, rarest query words)
        # comes first, followed by the rest of the (short) document as context.
        paras = [p.strip() for p in body.split("\n") if p.strip()]
        if not paras:
            return ""
        wanted = set(terms)
        best = max(paras, key=lambda p: sum(self.idf.get(t, 0) for t in wanted & set(tokenize(p))))
        return " ".join([best] + [p for p in paras if p is not best])[:600]


INDEX = Index()
INDEX.build()


@app.get("/search")
def search(q: str, k: int = 3):
    corrupt = chaos.check()
    if corrupt:
        # A corrupted index returns unrelated documents (or nothing).
        junk = random.sample(INDEX.docs, k=min(1, len(INDEX.docs))) if random.random() < 0.5 else []
        return {"query": q, "results": [{"doc_id": d["doc_id"], "title": d["title"],
                                          "snippet": "", "score": 0.0} for d in junk]}
    return {"query": q, "results": INDEX.search(q, k)}


@app.post("/admin/rebuild")
def rebuild():
    # Rebuild the index from the source docs (this also clears index corruption).
    INDEX.build()
    if chaos.mode == "corrupt":
        chaos.mode = "none"
    return {"rebuilt": True, "docs": len(INDEX.docs)}


@app.get("/admin/state")
def state():
    return {"service": "knowledge_base", "status": chaos.status(), "docs": len(INDEX.docs),
            "index_terms": len(INDEX.idf), "index_built_at": INDEX.built_at,
            "index_integrity": "failed" if chaos.mode == "corrupt" else "ok"}
