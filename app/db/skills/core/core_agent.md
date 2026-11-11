# skills.db`s  - How to read and use it (for AI agents)

`skills.db` is a plain SQLite file built by `secure_vectorizer.py` from local
`.md` skill files. **It is not encrypted or obfuscated.** All text is stored in
clear form. Nothing needs to be decrypted. `README.md` files are skipped by
the indexer, so this file is never returned as a skill.

## Recommended agent workflow (saves tokens)

1. Call `catalog()` - a short list of every skill (name, description, tags).
   Do NOT load full skill files up front.
2. Pick the relevant skill(s) or tag(s).
3. Call `search(query, top_k=3, tag=...)` and use only the returned chunks.
4. Never inject whole files into the context; the chunks are the unit.

## Search modes

1. **Keyword mode (default)** - no vectors. Ranking = BM25 via SQLite FTS5.
2. **Hybrid mode (only if indexed with `--embed`)** - float32 embedding vectors
   are stored in `chunks.vec` and fused with BM25.

Check which one you have:
    SELECT COUNT(*) FROM chunks WHERE vec IS NOT NULL;   -- 0 = keyword mode only

## Skill file format (optional frontmatter)

A skill is a `.md` file. It may start with a frontmatter block:

    ---
    name: PDF lesen
    description: Text und Tabellen aus PDF-Dateien holen
    tags: pdf, dokument, extraktion
    ---
    # PDF lesen
    ...

- `tags` accepts `a, b`, `[a, b]` or a `- item` list. Tags are lowercased.
- `keywords:` is accepted as an alias for `tags:`.
- Without frontmatter: name = file name, description = first text line.
- The frontmatter is NOT part of the chunk text.

## Schema (schema version "3")

    files (path TEXT PRIMARY KEY, hash TEXT, mtime REAL)   -- hash = sha1 of file bytes
    chunks(id INTEGER PRIMARY KEY, path TEXT, heading TEXT, text TEXT, vec BLOB)
    fts   (heading, text)    -- FTS5 virtual table, rowid == chunks.id
                             -- tokenizer: unicode61 remove_diacritics 2
    tags  (path TEXT, tag TEXT)                            -- one row per file and tag
    meta  (path TEXT PRIMARY KEY, name TEXT, description TEXT)
    info  (key TEXT PRIMARY KEY, value TEXT)               -- key 'version' = schema version

Important: the FTS column `heading` contains
`chunk title + tags + frontmatter description` (so tags weigh as much as
titles in ranking), while `chunks.heading` holds only the plain chunk title.

If the schema version differs, all files are re-indexed once automatically.

## Chunking

Markdown is split at headings (levels 1-4, ignored inside ``` code fences).
Sections longer than 1200 characters are split at blank lines.
`chunks.heading` = section title (file name stem if there is none).

## Keyword ranking (BM25)

Query text -> lowercase -> tokens `\w+` -> each token becomes a prefix term,
joined with OR:   "agent"* OR "tools"*

    SELECT rowid FROM fts
    WHERE fts MATCH '"agent"* OR "tools"*'
      AND rowid IN (SELECT c.id FROM chunks c JOIN tags t ON t.path=c.path
                    WHERE t.tag='python')            -- only when a tag filter is given
    ORDER BY bm25(fts, 5.0, 1.0)      -- column weights: heading 5.0, text 1.0
    LIMIT 4*k;

SQLite's bm25() returns lower (more negative) = better, hence ascending order.
With a tag but an empty query, the chunks of all tagged skills are returned in
file order (no ranking).

## Vectors (only in hybrid mode)

- Model: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
  (via the `fastembed` package).
- Embedded text per chunk: `chunk title + "\n" + chunk text` (no tags).
- Storage: raw little-endian float32 bytes, **L2-normalized**.
  Dimension = len(blob) / 4 (384 for this model).
- Decode:

      import numpy as np
      vec = np.frombuffer(blob, dtype="float32")

- Similarity: all vectors are normalized, so **cosine similarity = dot
  product**:  score = chunk_vec @ query_vec
  The query must be embedded with the SAME model and normalized the same way.
- A tag filter is applied to the vector candidates as well.

## Hybrid fusion (Reciprocal Rank Fusion)

Take the top 4*k candidates from BM25 and from the vector ranking (ranks start
at 0). For every chunk:

    fused = 1/(60 + rank_bm25) + 1/(60 + rank_vector)     # missing list -> term omitted

Sort by `fused` descending, return the top k. The `score` reported by the
tool is `round(fused * 60, 4)`. Scores are only comparable within one query.

## Using it from Python

    from secure_vectorizer import LocalSkillVectorEngine
    engine = LocalSkillVectorEngine(target_dir=r"C:\path\to\skills")
    engine.build()                           # incremental; only changed files are re-read

    engine.catalog()                         # [{path, name, description, tags, chunks}, ...]
    engine.catalog(tag="python")             # only skills with this tag

    hits = engine.search("your question", top_k=3, tag=None)
    # each hit: file, content, path, heading, text, tags, score

## Using it from the command line

    python secure_vectorizer.py                        # index current folder
    python secure_vectorizer.py index --skills <dir>
    python secure_vectorizer.py search "query" -k 5 [--tag python]
    python secure_vectorizer.py catalog [--tag python]
    python secure_vectorizer.py tags                   # all tags with skill counts
    python secure_vectorizer.py list | stats

## Reading with plain SQL (read-only is fine)

    import sqlite3
    db = sqlite3.connect("skills.db")
    for name, desc in db.execute("SELECT name, description FROM meta"):
        print(name, desc)
    for path, heading, text in db.execute("SELECT path, heading, text FROM chunks LIMIT 5"):
        print(path, heading, text[:100])

## Notes for agents

- The DB is rebuildable from the .md files; never edit it by hand.
- Run `build()` / `index` before searching if skill files may have changed.
- Prefer a tag filter when the topic is clear; it narrows results precisely.
