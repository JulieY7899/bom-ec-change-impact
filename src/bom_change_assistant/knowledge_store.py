"""Small, dependency-free engineering library. Each posting is one sparse vector coordinate."""
from __future__ import annotations

import math
import os
import re
import sqlite3
import unicodedata
from collections import Counter
from contextlib import closing, contextmanager

SECTIONS = {"standards": "工程规范", "changes": "变更案例", "validation": "验证与判定", "unfiled": "待归类"}
METHOD = "local-sparse-cosine-v2"
MAX_DOCUMENTS = 30
CHUNK_SIZE = 300
OVERLAP = 40
TOKEN = re.compile(r"[a-z0-9]+(?:[-_.][a-z0-9]+)*|[\u3400-\u9fff]+")
SCHEMA = (
    "CREATE TABLE documents(digest TEXT PRIMARY KEY, kind TEXT NOT NULL, section TEXT NOT NULL, title TEXT NOT NULL)",
    "CREATE TABLE chunks(id INTEGER PRIMARY KEY, digest TEXT NOT NULL REFERENCES documents ON DELETE CASCADE,"
    " unit INTEGER NOT NULL, part INTEGER NOT NULL, text TEXT NOT NULL, UNIQUE(digest,unit,part))",
    "CREATE TABLE postings(term TEXT NOT NULL, chunk INTEGER NOT NULL REFERENCES chunks ON DELETE CASCADE,"
    " weight REAL NOT NULL, PRIMARY KEY(term,chunk)) WITHOUT ROWID",
    "CREATE INDEX postings_by_chunk ON postings(chunk)",
)


def store_path(repository):
    path = repository.database_path
    return path.with_name(path.name + ".engineering-kb.sqlite3")


def chunks(text):
    """Prefer a sentence or paragraph break, retaining context across chunk boundaries."""
    start = 0
    while start < len(text):
        end = min(start + CHUNK_SIZE, len(text))
        if end < len(text):
            boundaries = list(re.finditer(r"[。！？；\n]|[.!?](?=\s)", text[start + CHUNK_SIZE // 2:end]))
            if boundaries:
                end = start + CHUNK_SIZE // 2 + boundaries[-1].end()
        part = text[start:end].strip()
        if part:
            yield part
        if end == len(text):
            break
        start = end - OVERLAP


def vector(text):
    features = Counter()
    normalized = unicodedata.normalize("NFKC", text).casefold()
    for before, after in (("换料", "替换"), ("替代", "替换"), ("启用日期", "生效日期")):
        normalized = normalized.replace(before, after)
    for word in TOKEN.findall(normalized):
        if word.isascii() or len(word) == 1:
            features[word] += 1
        else:
            features.update(word[i:i + 2] for i in range(len(word) - 1))
    weights = {term: 1 + math.log(n) for term, n in features.items()}
    norm = math.sqrt(sum(w * w for w in weights.values()))
    return {term: w / norm for term, w in weights.items()}


def source_id(kind, digest):
    return ("PPT" if kind in {"ppt", "pptx"} else "TXT") + "-" + digest[:16].upper()


def _insert_units(db, digest, units):
    count = 0
    for unit, text in units:
        for part, excerpt in enumerate(chunks(text), 1):
            row = db.execute("INSERT INTO chunks(digest,unit,part,text) VALUES (?,?,?,?)",
                             (digest, unit, part, excerpt))
            db.executemany("INSERT INTO postings VALUES (?,?,?)",
                           ((term, row.lastrowid, w) for term, w in vector(excerpt).items()))
            count += 1
    return count


def _initialize(db, repository):
    db.execute("BEGIN IMMEDIATE")
    if db.execute("PRAGMA user_version").fetchone()[0] == 1:
        return
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone():
        raise ValueError("知识库文件格式不受支持")
    for statement in SCHEMA:
        db.execute(statement)
    # Copy reviewed legacy text once. The previous BOM database and backups stay readable.
    with repository.connect() as old:
        rows = old.execute("SELECT deck_digest,slide_number,excerpt FROM knowledge_excerpts ORDER BY 1,2")
        for digest, unit, excerpt in rows:
            db.execute("INSERT OR IGNORE INTO documents VALUES (?,'pptx','unfiled','')", (digest,))
            _insert_units(db, digest, [(unit, excerpt)])
    db.execute("PRAGMA user_version=1")


@contextmanager
def _store(repository):
    path = store_path(repository)
    os.close(os.open(path, os.O_CREAT | os.O_RDWR, 0o600))
    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA foreign_keys=ON")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            with db:
                _initialize(db, repository)
        elif version != 1:
            raise ValueError("知识库文件版本不受支持")
        with db:
            yield db


def method(repository):
    from .semantic_index import METHOD as semantic_method
    return semantic_method if getattr(repository, "semantic_index", None) else METHOD


def library(repository):
    with _store(repository) as db:
        grouped = dict(db.execute("SELECT section,COUNT(*) FROM documents GROUP BY section"))
        units = db.execute("SELECT COUNT(*) FROM (SELECT DISTINCT digest,unit FROM chunks)").fetchone()[0]
        inventory = [{"section": zone, "title": title, "source_id": source_id(kind, digest), "chunks": n}
                     for zone, title, kind, digest, n in db.execute("""
                         SELECT d.section,d.title,d.kind,d.digest,COUNT(*) FROM documents d
                         JOIN chunks c ON c.digest=d.digest GROUP BY d.digest ORDER BY d.section,d.title,d.digest
                     """)]
        count = sum(item["chunks"] for item in inventory)
    return {"documents": sum(grouped.values()), "excerpts": units, "chunks": count, "inventory": inventory, "method": method(repository),
            "sections": [{"id": k, "label": v, "documents": grouped.get(k, 0)} for k, v in SECTIONS.items()]}


def replace_document(repository, digest, kind, section, title, units):
    if not isinstance(section, str) or section not in SECTIONS:
        raise ValueError("请选择有效的工程材料分区")
    if not isinstance(title, str) or len(title) > 80:
        raise ValueError("脱敏标题最多 80 字")
    with _store(repository) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM documents WHERE digest=?", (digest,))
        if db.execute("SELECT COUNT(*) FROM documents").fetchone()[0] >= MAX_DOCUMENTS:
            raise ValueError(f"工程知识库最多容纳 {MAX_DOCUMENTS} 份材料")
        db.execute("INSERT INTO documents VALUES (?,?,?,?)", (digest, kind, section, title.strip()))
        count = _insert_units(db, digest, sorted(units.items()))
        index = getattr(repository, "semantic_index", None)
        if index is not None:
            index.sync_and_search(db)
    return {**library(repository), "source_id": source_id(kind, digest), "indexed": len(units), "indexed_chunks": count}


def retrieve(repository, query, section="", *, context_query=False):
    if not isinstance(section, str) or (section and section not in SECTIONS):
        raise ValueError("检索分区无效")
    if not isinstance(query, str) or len(query) > 240:
        raise ValueError("搜索问题最多 240 字")
    terms = vector(query)
    matches = []
    with _store(repository) as db:
        index = getattr(repository, "semantic_index", None)
        if index is not None:
            db.execute("BEGIN IMMEDIATE")
        where = "WHERE d.section=?" if section else ""
        params = [section] if section else []
        populated = db.execute("SELECT 1 FROM documents d " + where + " LIMIT 1", params).fetchone()
        if terms and populated:
            values = ",".join("(?,?,?)" for _ in terms)
            # A complete material code is discriminating even in a long block of prose.
            bindings = []
            has_identifier = False
            for term, weight in terms.items():
                identifier = term.isascii() and any(c.isdigit() for c in term) and (
                    any(c.isalpha() for c in term) or len(term) >= 4)
                has_identifier |= identifier
                bindings.extend((term, weight, int(identifier)))
            exact_only = index is not None and has_identifier and not context_query
            threshold = "identifiers>0" if exact_only else "identifiers>0 OR score>=0.10"
            sql = f"""WITH q(term,w,identifier) AS (VALUES {values})
                SELECT d.digest,d.kind,d.section,d.title,c.unit,c.part,c.text,SUM(q.w*p.weight) AS score,
                       SUM(q.identifier) AS identifiers
                FROM q JOIN postings p ON p.term=q.term JOIN chunks c ON c.id=p.chunk
                JOIN documents d ON d.digest=c.digest {where}
                GROUP BY c.id HAVING {threshold}
                ORDER BY identifiers DESC,score DESC,d.digest,c.unit,c.part LIMIT 3"""
            for digest, kind, zone, title, unit, part, text, score, identifiers in db.execute(sql, bindings + params):
                matches.append({"source_id": source_id(kind, digest), "kind": kind, "section": zone,
                                "section_label": SECTIONS[zone], "title": title, "slide": unit,
                                "chunk": part, "text": text, "score": round(score, 4), "matched_identifiers": identifiers})
            if index is not None and not exact_only:
                semantic = index.sync_and_search(db, query, section)
                exact = [m for m in matches if m["matched_identifiers"]]
                ranked = list(exact)
                for (digest, kind, zone, title, unit, part, text), score in semantic:
                    ranked.append({"source_id": source_id(kind, digest), "kind": kind, "section": zone,
                                   "section_label": SECTIONS[zone], "title": title, "slide": unit,
                                   "chunk": part, "text": text, "score": round(score, 4),
                                   "matched_identifiers": 0})
                seen = set()
                combined = []
                for hit in ranked + matches:
                    key = hit["source_id"], hit["slide"], hit["chunk"]
                    if key not in seen:
                        combined.append(hit)
                        seen.add(key)
                matches = combined[:3]
    return {"status": "retrieved" if matches else "no_match" if populated else "empty",
            "method": method(repository), "matches": matches}
