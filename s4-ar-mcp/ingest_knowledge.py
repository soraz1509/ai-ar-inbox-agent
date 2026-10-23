"""Load knowledge/*.md into the pgvector knowledge base (full refresh)."""
from pathlib import Path

from kb import ingest

chunks = ingest(Path(__file__).parent / "knowledge")
for c in chunks:
    print(f"{c['doc_id']:<28} {(c['customer'] or 'general'):<10} {c['section']}")
print(f"{len(chunks)} chunks embedded and stored.")
