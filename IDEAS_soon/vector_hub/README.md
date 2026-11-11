# skills.db - How to read it (for AI agents in my HUB)

`skills.db` is a plain SQLite file built by `secure_vectorizer.py` from local
`.md` skill files. **It is not encrypted or obfuscated.** All text is stored
in clear form. Nothing needs to be decrypted.

## Two search modes

1. **Keyword mode (default)** - no vectors. Ranking = BM25 via SQLite FTS5.
2. **Hybrid mode (only if indexed with `--embed`)** - float32 embedding
   vectors are stored in `chunks.vec` and fused with BM25.

Check which one you have:
    SELECT COUNT(*) FROM chunks WHERE vec IS NOT NULL;   -- 0 = keyword mode only

## Schema

    files(path TEXT PRIMARY KEY, hash TEXT, mtime REAL)      -- hash = sha1 of file bytes
    chunks(id INTEGER PRIMARY KEY, path TEXT, heading TEXT, text TEXT, vec BLOB)
    fts   (heading, text)  -- FTS5 virtual table, rowid == chunks.id
                           -- tokenizer: unicode61 remove_diacritics 2

## Chunking

Markdown is split at headings (levels 1-4, ignored inside ``` code fences).
Sections longer than 1200 characters are split at blank lines.
`chunks.heading` = the section title (file name stem if there is none).

## Keyword ranking (BM25)

Query text -> lowercase -> tokens `\w+` -> each token becomes a prefix term,
joined with OR:   "agent"* OR "tools"*

    SELECT rowid FROM fts
    WHERE fts MATCH '"agent"* OR "tools"*'
    ORDER BY bm25(fts, 5.0, 1.0)      -- column weights: heading 5.0, text 1.0
    LIMIT 20;

SQLite's bm25() returns lower (more negative) = better, hence ascending order.

## Vectors (only in hybrid mode)

- Model: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
  (via the `fastembed` package).
- Embedded text per chunk: `heading + "\n" + text`.
- Storage: raw little-endian float32 bytes, **L2-normalized**.
  Dimension = len(blob) / 4 (384 for this model).
- Decode:

      import numpy as np
      vec = np.frombuffer(blob, dtype="float32")

- Similarity: because all vectors are normalized, **cosine similarity =
  dot product**:  score = chunk_vec @ query_vec
  The query must be embedded with the SAME model and normalized the same way.

## Hybrid fusion (Reciprocal Rank Fusion)

Take the top 4*k candidates from BM25 and from the vector ranking (ranks start
at 0). For every chunk:

    fused = 1/(60 + rank_bm25) + 1/(60 + rank_vector)     # missing list -> term omitted

Sort by `fused` descending, return the top k. The `score` reported by the
tool is `round(fused * 60, 4)`.

## Reading it from Python

    from secure_vectorizer import LocalSkillVectorEngine
    engine = LocalSkillVectorEngine(target_dir=r"C:\path\to\skills")
    engine.build()                         # incremental; only changed files are re-read
    hits = engine.search("your question", top_k=3)
    # each hit: file, content, path, heading, text, score

Or directly with SQL (read-only is fine):

    import sqlite3
    db = sqlite3.connect("skills.db")
    for path, heading, text in db.execute("SELECT path, heading, text FROM chunks LIMIT 5"):
        print(path, heading, text[:100])

## Notes for agents

- Only inject the top-k returned chunks into context, not whole files.
- The DB is rebuildable from the .md files; never edit it by hand.
- `score` is only comparable within one query, not across queries.
