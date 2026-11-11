#!/usr/bin/env python3
# =============================================================================
# @file         secure_vectorizer.py
# @author       Volkan
# @license      AGPL3 + ESOL 2.1 / All Rights Reserved
# @description  Local secure skill indexing engine (SQLite FTS5 / BM25). 
#               Don't even think about stealing this IP. Hands off.
# =============================================================================
#!/usr/bin/env python3
"""
secure_vectorizer.py - lokale Skill-Datenbank fuer Markdown-Dateien.

* A file that only uses the Python standard library (SQLite FTS5 / BM25).
* Inkrementell: nur geaenderte Dateien werden neu eingelesen.
* Tags and descriptions from the front matter of the .md files (see below).
* Optional: you can use something called semantic vectors (just install fastembed and numpy using pip) to get a hybrid search.
* It always reports in when it starts, so you can see that the script is running.

All skill.md-files need for more performance! 
example
---
name: PDF read
description: get Text and Tabells from PDF-files.
tags: pdf, dokument, extraktion
---


Im Hub:
from secure_vectorizer import LocalSkillVectorEngine
engine = LocalSkillVectorEngine(target_dir=r"C:\\pfad\\zu\\skills")
engine.build()
engine.catalog()                                   # Kurzliste fuer die KI
for hit in engine.search("agent", top_k=3, tag="python"):
print(hit["file"], hit["heading"], hit["score"], hit["tags"])

"""
from __future__ import annotations

import sys as _sys
if __name__ == "__main__":
    print("[BOOT] v3 - Datei:", __file__, flush=True)


import argparse
import hashlib
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

DB_NAME = "skills.db"
SCHEMA_VERSION = "3"      # bei Aenderung wird einmalig neu indexiert
MAX_CHARS = 1200          # max. Groesse eines Chunks
EMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
HEADING = re.compile(r"^(#{1,4})\s+(.*)$")
TOKEN = re.compile(r"\w+", re.UNICODE)
FRONTMATTER = re.compile(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", re.S)


def log(msg: str) -> None:
    print(msg, flush=True)


# --------------------------------------------------------------------------
# Frontmatter
# --------------------------------------------------------------------------
def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1]
    return s.strip()


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Liest einfache 'key: value'-Zeilen (und '- item'-Listen) am Dateianfang."""
    m = FRONTMATTER.match(text)
    if not m:
        return {}, text
    meta: dict = {}
    key = None
    for line in m.group(1).splitlines():
        km = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if km:
            key = km.group(1).lower()
            meta[key] = km.group(2).strip()
        elif key and re.match(r"^\s*-\s+", line):
            item = re.sub(r"^\s*-\s+", "", line)
            if not isinstance(meta[key], list):
                meta[key] = [] if not meta[key] else [meta[key]]
            meta[key].append(item)
    return meta, text[m.end():]


def norm_tags(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            value = value[1:-1]
        value = value.split(",")
    tags = []
    for t in value:
        t = _unquote(str(t)).lower()
        if t and t not in tags:
            tags.append(t)
    return tags


# --------------------------------------------------------------------------
# Chunking
# --------------------------------------------------------------------------
def chunk_markdown(text: str, fallback_title: str) -> list[tuple[str, str]]:
    """Teilt Markdown an Ueberschriften; lange Abschnitte an Absaetzen."""
    sections: list[tuple[str, list[str]]] = []
    title, buf = fallback_title, []
    in_code = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
        m = None if in_code else HEADING.match(line)
        if m:
            if any(b.strip() for b in buf):
                sections.append((title, buf))
            title, buf = m.group(2).strip() or fallback_title, []
        else:
            buf.append(line)
    if any(b.strip() for b in buf):
        sections.append((title, buf))

    out: list[tuple[str, str]] = []
    for title, lines in sections:
        body = "\n".join(lines).strip()
        if len(body) <= MAX_CHARS:
            out.append((title, body))
            continue
        cur = ""
        for para in re.split(r"\n\s*\n", body):
            if cur and len(cur) + len(para) > MAX_CHARS:
                out.append((title, cur.strip()))
                cur = ""
            cur += para + "\n\n"
            while len(cur) > MAX_CHARS * 2:       # extrem lange Bloecke
                out.append((title, cur[:MAX_CHARS]))
                cur = cur[MAX_CHARS:]
        if cur.strip():
            out.append((title, cur.strip()))
    return out


def first_line(chunks: list[tuple[str, str]], limit: int = 160) -> str:
    for _, body in chunks:
        for line in body.splitlines():
            line = line.strip()
            if line and not line.startswith(("```", "|", "#")):
                return line[:limit]
    return ""


# --------------------------------------------------------------------------
# Optionale Embeddings
# --------------------------------------------------------------------------
class Embedder:
    def __init__(self) -> None:
        from fastembed import TextEmbedding  # type: ignore
        self.model = TextEmbedding(EMBED_MODEL)

    def encode(self, texts: list[str]):
        import numpy as np
        vecs = np.array(list(self.model.embed(texts)), dtype="float32")
        vecs /= (np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9)
        return vecs


def try_embedder() -> Embedder | None:
    try:
        return Embedder()
    except Exception as e:  # fastembed/numpy fehlt o.ae.
        log(f"[INFO] Embeddings aus (nur Keyword/BM25): {type(e).__name__}")
        return None


# --------------------------------------------------------------------------
# Datenbank
# --------------------------------------------------------------------------
class SkillDB:
    def __init__(self, skills_dir: str = ".", db_path: str | None = None):
        self.root = Path(skills_dir).resolve()
        self.db_path = Path(db_path) if db_path else self.root / DB_NAME
        self.db = sqlite3.connect(self.db_path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS files(
                path TEXT PRIMARY KEY, hash TEXT, mtime REAL);
            CREATE TABLE IF NOT EXISTS chunks(
                id INTEGER PRIMARY KEY, path TEXT, heading TEXT,
                text TEXT, vec BLOB);
            CREATE INDEX IF NOT EXISTS chunks_path ON chunks(path);
            CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
                heading, text, tokenize='unicode61 remove_diacritics 2');
            CREATE TABLE IF NOT EXISTS tags(path TEXT, tag TEXT);
            CREATE INDEX IF NOT EXISTS tags_tag ON tags(tag);
            CREATE INDEX IF NOT EXISTS tags_path ON tags(path);
            CREATE TABLE IF NOT EXISTS meta(
                path TEXT PRIMARY KEY, name TEXT, description TEXT);
            CREATE TABLE IF NOT EXISTS info(key TEXT PRIMARY KEY, value TEXT);
            """
        )
        row = self.db.execute("SELECT value FROM info WHERE key='version'").fetchone()
        if not row or row[0] != SCHEMA_VERSION:
            # aeltere DB: alle Dateien einmalig neu einlesen (Tags etc. nachziehen)
            self.db.execute("DELETE FROM files")
            self.db.execute("INSERT OR REPLACE INTO info VALUES('version', ?)",
                            (SCHEMA_VERSION,))
            self.db.commit()

    # ---- Index -----------------------------------------------------------
    def index(self, embed: bool = False, prune: bool = True) -> dict:
        t0 = time.time()
        files = sorted(
            p for p in self.root.rglob("*.md")
            if p.name.lower() != "readme.md"
            and not any(part.startswith(".") for part in p.relative_to(self.root).parts)
        )
        log(f"[SCAN] {self.root}  ->  {len(files)} .md-Dateien")
        embedder = try_embedder() if embed else None
        stats = {"new": 0, "updated": 0, "unchanged": 0, "removed": 0, "chunks": 0}
        seen = set()

        for p in files:
            rel = p.relative_to(self.root).as_posix()
            seen.add(rel)
            raw = p.read_bytes()
            h = hashlib.sha1(raw).hexdigest()
            row = self.db.execute("SELECT hash FROM files WHERE path=?", (rel,)).fetchone()
            if row and row[0] == h:
                stats["unchanged"] += 1
                continue
            had_chunks = self.db.execute(
                "SELECT 1 FROM chunks WHERE path=? LIMIT 1", (rel,)).fetchone()
            text = raw.decode("utf-8-sig", errors="replace")
            fm, body_text = parse_frontmatter(text)
            tags = norm_tags(fm.get("tags") or fm.get("keywords"))
            name = _unquote(str(fm.get("name") or "")) or p.stem
            fm_desc = fm.get("description")
            fm_desc = _unquote(" ".join(fm_desc) if isinstance(fm_desc, list) else str(fm_desc or ""))
            self._drop(rel)
            chunks = chunk_markdown(body_text, p.stem)
            desc = fm_desc or first_line(chunks)
            vecs = embedder.encode([f"{t}\n{b}" for t, b in chunks]) if embedder and chunks else None
            extra = " ".join(tags + ([fm_desc] if fm_desc else []))
            for i, (title, body) in enumerate(chunks):
                blob = vecs[i].tobytes() if vecs is not None else None
                cur = self.db.execute(
                    "INSERT INTO chunks(path, heading, text, vec) VALUES(?,?,?,?)",
                    (rel, title, body, blob))
                # Tags + Beschreibung zaehlen mit hohem Gewicht (Spalte 'heading')
                self.db.execute(
                    "INSERT INTO fts(rowid, heading, text) VALUES(?,?,?)",
                    (cur.lastrowid, f"{title} {extra}".strip(), body))
            self.db.executemany("INSERT INTO tags(path, tag) VALUES(?,?)",
                                [(rel, t) for t in tags])
            self.db.execute("INSERT OR REPLACE INTO meta(path, name, description) VALUES(?,?,?)",
                            (rel, name, desc))
            self.db.execute(
                "INSERT OR REPLACE INTO files(path, hash, mtime) VALUES(?,?,?)",
                (rel, h, p.stat().st_mtime))
            stats["updated" if (row or had_chunks) else "new"] += 1
            stats["chunks"] += len(chunks)
            tag_info = f"  tags: {', '.join(tags)}" if tags else ""
            log(f"[INDEXED] {rel}  ({len(chunks)} Chunks){tag_info}")

        if prune:
            known = {r[0] for r in self.db.execute("SELECT path FROM files")}
            known |= {r[0] for r in self.db.execute("SELECT DISTINCT path FROM chunks")}
            for rel in sorted(known - seen):
                self._drop(rel)
                self.db.execute("DELETE FROM files WHERE path=?", (rel,))
                stats["removed"] += 1
                log(f"[REMOVED] {rel}")
        self.db.commit()
        log(f"[DONE] {stats}  in {time.time() - t0:.2f}s  ->  {self.db_path}")
        return stats

    def _drop(self, rel: str) -> None:
        ids = [r[0] for r in self.db.execute("SELECT id FROM chunks WHERE path=?", (rel,))]
        for i in ids:
            self.db.execute("DELETE FROM fts WHERE rowid=?", (i,))
        self.db.execute("DELETE FROM chunks WHERE path=?", (rel,))
        self.db.execute("DELETE FROM tags WHERE path=?", (rel,))
        self.db.execute("DELETE FROM meta WHERE path=?", (rel,))

    # ---- Suche -----------------------------------------------------------
    @staticmethod
    def _fts_query(q: str) -> str:
        toks = TOKEN.findall(q.lower())
        return " OR ".join(f'"{t}"*' for t in toks) if toks else ""

    def _tags_of(self, path: str) -> list[str]:
        return [r[0] for r in self.db.execute(
            "SELECT tag FROM tags WHERE path=? ORDER BY tag", (path,))]

    def search(self, query: str, k: int = 5, semantic: bool = True,
               tag: str | None = None) -> list[dict]:
        tag = tag.strip().lower() if tag else None
        tag_filter = " AND {col} IN (SELECT path FROM tags WHERE tag=?)"

        bm25: dict[int, int] = {}
        fq = self._fts_query(query)
        if fq:
            sql = "SELECT rowid FROM fts WHERE fts MATCH ?"
            params: list = [fq]
            if tag:
                sql += " AND rowid IN (SELECT c.id FROM chunks c JOIN tags t " \
                       "ON t.path=c.path WHERE t.tag=?)"
                params.append(tag)
            sql += " ORDER BY bm25(fts, 5.0, 1.0) LIMIT ?"
            params.append(k * 4)
            rows = self.db.execute(sql, params).fetchall()
            bm25 = {rid: rank for rank, (rid,) in enumerate(rows)}
        elif tag:   # nur Tag, kein Suchtext: Chunks der getaggten Skills
            rows = self.db.execute(
                "SELECT id FROM chunks WHERE path IN (SELECT path FROM tags WHERE tag=?) "
                "ORDER BY id LIMIT ?", (tag, k * 4)).fetchall()
            bm25 = {rid: rank for rank, (rid,) in enumerate(rows)}

        sem: dict[int, int] = {}
        if semantic and fq and self.db.execute(
                "SELECT 1 FROM chunks WHERE vec IS NOT NULL LIMIT 1").fetchone():
            emb = try_embedder()
            if emb is not None:
                import numpy as np
                sql = "SELECT id, vec FROM chunks WHERE vec IS NOT NULL"
                params = []
                if tag:
                    sql += tag_filter.format(col="path")
                    params.append(tag)
                ids, mats = [], []
                for rid, blob in self.db.execute(sql, params):
                    ids.append(rid)
                    mats.append(np.frombuffer(blob, dtype="float32"))
                if mats:
                    scores = np.stack(mats) @ emb.encode([query])[0]
                    order = np.argsort(-scores)[: k * 4]
                    sem = {ids[i]: r for r, i in enumerate(order)}

        # Reciprocal Rank Fusion
        fused: dict[int, float] = {}
        for ranks in (bm25, sem):
            for rid, rank in ranks.items():
                fused[rid] = fused.get(rid, 0.0) + 1.0 / (60 + rank)
        top = sorted(fused.items(), key=lambda x: -x[1])[:k]

        hits = []
        for rid, score in top:
            path, heading, text = self.db.execute(
                "SELECT path, heading, text FROM chunks WHERE id=?", (rid,)).fetchone()
            hits.append({"path": path, "heading": heading, "text": text,
                         "tags": self._tags_of(path),
                         "score": round(score * 60, 4)})
        return hits

    # ---- Uebersicht ------------------------------------------------------
    def catalog(self, tag: str | None = None) -> list[dict]:
        """Kurzliste aller Skills: Name, Beschreibung, Tags (spart Tokens)."""
        sql = ("SELECT f.path, COALESCE(m.name, f.path), COALESCE(m.description, ''), "
               "COUNT(c.id) FROM files f "
               "LEFT JOIN meta m ON m.path=f.path "
               "LEFT JOIN chunks c ON c.path=f.path ")
        params: list = []
        if tag:
            sql += "WHERE f.path IN (SELECT path FROM tags WHERE tag=?) "
            params.append(tag.strip().lower())
        sql += "GROUP BY f.path ORDER BY f.path"
        return [{"path": p, "name": n, "description": d, "chunks": c,
                 "tags": self._tags_of(p)}
                for p, n, d, c in self.db.execute(sql, params).fetchall()]

    def all_tags(self) -> list[tuple[str, int]]:
        return self.db.execute(
            "SELECT tag, COUNT(DISTINCT path) FROM tags GROUP BY tag "
            "ORDER BY COUNT(DISTINCT path) DESC, tag").fetchall()

    def list_files(self) -> list[tuple[str, int]]:
        return self.db.execute(
            "SELECT f.path, COUNT(c.id) FROM files f LEFT JOIN chunks c "
            "ON c.path=f.path GROUP BY f.path ORDER BY f.path").fetchall()


# --------------------------------------------------------------------------
# Kompatible Schnittstelle zum Hub (gleicher Name wie vorher)
# --------------------------------------------------------------------------
class LocalSkillVectorEngine:
    """Drop-in fuer die alte Klasse: build() und search(query, top_k)."""

    def __init__(self, target_dir: str = ".", embed: bool = False):
        self.target_dir = Path(target_dir).resolve()
        self.embed = embed
        self._db = SkillDB(str(self.target_dir))
        self.index_path = self._db.db_path

    def build(self) -> None:
        if not self.target_dir.exists():
            log(f"[ERROR] Target directory does not exist: {self.target_dir}")
            return
        self._db.index(embed=self.embed)

    def search(self, query: str, top_k: int = 3, tag: str | None = None) -> list[dict]:
        if not self._db.list_files():
            log("[INFO] Index not found. Building now...")
            self.build()
        hits = self._db.search(query, k=top_k, tag=tag)
        # alte Schluessel (file/content) + neue (path/heading/text/tags/score)
        return [{"file": h["path"], "content": h["text"], **h} for h in hits]

    def catalog(self, tag: str | None = None) -> list[dict]:
        if not self._db.list_files():
            self.build()
        return self._db.catalog(tag)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    log("[START] secure_vectorizer laeuft (Python %s)" % sys.version.split()[0])
    ap = argparse.ArgumentParser(description="Lokale Skill-Suche")
    ap.add_argument("cmd", nargs="?", default="index",
                    choices=["index", "search", "list", "stats", "catalog", "tags"])
    ap.add_argument("query", nargs="?", default="")
    ap.add_argument("--skills", default=os.environ.get("SKILLS_DIR", "."),
                    help="Ordner mit den .md-Skills (Standard: aktueller Ordner)")
    ap.add_argument("--db", default=None, help="Pfad der Datenbank (optional)")
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--tag", default=None, help="nur Skills mit diesem Tag")
    ap.add_argument("--embed", action="store_true",
                    help="semantische Vektoren berechnen (braucht fastembed)")
    a = ap.parse_args(argv)

    db = SkillDB(a.skills, a.db)
    if a.cmd == "index":
        db.index(embed=a.embed)
    elif a.cmd == "search":
        if not a.query and not a.tag:
            log("[ERROR] Suchbegriff fehlt: python secure_vectorizer.py search \"dein text\"")
            return 2
        if not db.list_files():
            log("[INFO] Noch kein Index - baue ihn jetzt...")
            db.index(embed=a.embed)
        hits = db.search(a.query, a.k, tag=a.tag)
        if not hits:
            log("[RESULT] keine Treffer")
        for i, h in enumerate(hits, 1):
            snippet = " ".join(h["text"].split())[:200]
            tg = f"  [{', '.join(h['tags'])}]" if h["tags"] else ""
            log(f"\n{i}. {h['path']}  >  {h['heading']}   (score {h['score']}){tg}\n   {snippet}")
    elif a.cmd == "catalog":
        if not db.list_files():
            db.index(embed=a.embed)
        for e in db.catalog(a.tag):
            tg = f" [{', '.join(e['tags'])}]" if e["tags"] else ""
            log(f"- {e['name']}{tg}  ({e['path']}, {e['chunks']} Chunks)\n    {e['description']}")
    elif a.cmd == "tags":
        rows = db.all_tags()
        if not rows:
            log("[INFO] Noch keine Tags. Frontmatter 'tags: a, b' in die .md-Dateien schreiben.")
        for tag, n in rows:
            log(f"{n:4d}  {tag}")
    elif a.cmd == "list":
        for path, n in db.list_files():
            log(f"{n:4d} Chunks  {path}")
    elif a.cmd == "stats":
        files = db.list_files()
        log(f"Dateien: {len(files)}   Chunks: {sum(n for _, n in files)}   DB: {db.db_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
