"""AR policy knowledge base (RAG): chunk Markdown by '## ' heading, embed with Gemini, store and
search in Postgres/pgvector. Customer-specific sections are only returned for that customer."""
import os
import re
from pathlib import Path

import httpx
import psycopg

API = "https://generativelanguage.googleapis.com/v1beta/models"
EMBED_MODEL = os.environ.get("GEMINI_EMBED_MODEL") or "gemini-embedding-001"
DIM = 768
CUSTOMER_TAG = re.compile(r"<!--\s*customer:\s*([\w-]+)\s*-->")

DDL = f"""
CREATE TABLE IF NOT EXISTS ar.kb_chunk (
  id         bigserial PRIMARY KEY,
  doc_id     text NOT NULL,
  doc_title  text NOT NULL,
  section    text NOT NULL,
  customer   text,
  content    text NOT NULL,
  embedding  vector({DIM}) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (doc_id, section)
)"""


def connect():
    return psycopg.connect(host=os.environ.get("POSTGRES_HOST", "postgres"), dbname=os.environ["POSTGRES_DB"],
                           user=os.environ["POSTGRES_USER"], password=os.environ["POSTGRES_PASSWORD"],
                           autocommit=True)


def vec(values) -> str:
    return "[" + ",".join(f"{v:.7f}" for v in values) + "]"


def embed(texts, task_type):
    """Gemini embeddings (768 dims). task_type: RETRIEVAL_DOCUMENT or RETRIEVAL_QUERY."""
    out = []
    for i in range(0, len(texts), 50):
        body = {"requests": [{"model": f"models/{EMBED_MODEL}", "content": {"parts": [{"text": t}]},
                              "taskType": task_type, "outputDimensionality": DIM} for t in texts[i:i + 50]]}
        r = httpx.post(f"{API}/{EMBED_MODEL}:batchEmbedContents", json=body, timeout=60,
                       headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]})
        if r.status_code != 200:
            raise RuntimeError(f"Gemini embedding failed: HTTP {r.status_code} {r.text[:300]}")
        out += [e["values"] for e in r.json()["embeddings"]]
    return out


def chunk_markdown(path: Path):
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^# (.+)$", text, re.M)
    title = m.group(1).strip() if m else path.stem
    chunks = []
    for part in re.split(r"^## ", text, flags=re.M)[1:]:
        heading, _, body = part.partition("\n")
        tag = CUSTOMER_TAG.search(body)
        body = re.sub(r"<!--.*?-->", "", body, flags=re.S).strip()
        if body:
            chunks.append({"doc_id": path.stem, "doc_title": title, "section": heading.strip(),
                           "customer": tag.group(1) if tag else None,
                           "content": f"{title} - {heading.strip()}\n\n{body}"})
    return chunks


def ingest(folder: Path):
    """Full refresh: re-embed all knowledge/*.md files."""
    chunks = [c for p in sorted(folder.glob("*.md")) for c in chunk_markdown(p)]
    vectors = embed([c["content"] for c in chunks], "RETRIEVAL_DOCUMENT")
    with connect() as con:
        con.execute(DDL)
        con.execute("DELETE FROM ar.kb_chunk")
        for c, v in zip(chunks, vectors):
            con.execute("INSERT INTO ar.kb_chunk (doc_id, doc_title, section, customer, content, embedding) "
                        "VALUES (%s, %s, %s, %s, %s, %s::vector)",
                        (c["doc_id"], c["doc_title"], c["section"], c["customer"], c["content"], vec(v)))
    return chunks


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def search(question: str, customer: str | None = None, k: int = 4):
    """Cosine search over general policy plus (only) the given customer's agreements."""
    q = vec(embed([question], "RETRIEVAL_QUERY")[0])
    with connect() as con:
        rows = con.execute(
            "SELECT doc_id, doc_title, section, customer, content, 1 - (embedding <=> %s::vector) AS score "
            "FROM ar.kb_chunk WHERE customer IS NULL OR customer = %s "
            "ORDER BY embedding <=> %s::vector LIMIT %s", (q, customer, q, k)).fetchall()
    return [{"source_id": f"{r[0]}#{slug(r[2])}", "document": r[1], "section": r[2], "customer": r[3],
             "score": round(float(r[5]), 3), "text": r[4]} for r in rows]
