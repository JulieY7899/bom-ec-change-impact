"""Small, dependency-free engineering library. Each posting is one sparse vector coordinate."""
from __future__ import annotations

import math
import os
import re
import sqlite3
import unicodedata
from collections import Counter
from contextlib import closing, contextmanager
from hashlib import sha256

from .retrieval_evidence import evidence_requirement

SECTIONS = {"standards": "工程规范", "changes": "变更案例", "validation": "验证与判定", "unfiled": "待归类"}
METHOD = "local-sparse-cosine-v2"
MAX_DOCUMENTS = 30
CHUNK_SIZE = 300
OVERLAP = 40
CONFIDENT_SEMANTIC_SCORE = 0.60
MIN_SEMANTIC_TAIL_SCORE = 0.65
MAX_SEMANTIC_TAIL_GAP = 0.08
MIN_KEYWORD_SCORE_RATIO = 0.70
TOKEN = re.compile(r"[a-z0-9]+(?:[-_.][a-z0-9]+)*|[\u3400-\u9fff]+")
SEARCH_TOPICS = (
    (re.compile(r"(?=.*(?:装配|组装|装上|装进|安装|零件|部件|干涉)).*(?:卡住|卡滞|装不上|装不进|装配干涉)"),
     "装配间隙 安装面 复测尺寸"),
    (re.compile(r"^(?!.*累计净到货)(?=.*(?:收货|到货|入库|实收|收了.{0,5}[件个套])).*(?:退货|退回|退给供应商|冲销)"),
     "累计净到货量 收货 退货 有效冲销"),
    (re.compile(r"(?=.*(?:上.{0,3}产线|上机生产|投产|开工)).*(?:拆|返工|领料|投料|工序|隔离)"),
     "在制工单 实际领料 投料 当前工序 返工 隔离"),
    (re.compile(r"(?=.*(?:客户|销售订单|定制订单)).*(?:两行|多行|每行|逐行配置|订单行.{0,6}配置)"),
     "销售订单 订单行 使用不同配置 订单行配置"),
    (re.compile(r"(?=.*(?:适用性|适用.{0,6}排除|订单行|销售订单)).*(?:冲突|矛盾|打架)"),
     "同一订单行 适用和排除证据冲突 保留证据 待核"),
    (re.compile(r"(?=.*旧(?:料|件|零件|部件))(?=.*(?:耗完|用完|耗尽|用尽|用光|用到零)).*(?:切换|改用|换新|换料|新料|新件)"),
     "消耗旧料 剩余需求 切换触发条件 新料"),
)
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


def _expand_query(query):
    normalized = unicodedata.normalize("NFKC", query)
    return " ".join([query, *(terms for phrase, terms in SEARCH_TOPICS if phrase.search(normalized))])


def source_id(kind, digest):
    return ("PPT" if kind in {"ppt", "pptx"} else "TXT") + "-" + digest[:16].upper()


def _reference(digest, kind, zone, title, unit, part, text):
    # A re-reviewed fragment must be selected again if its content or scope changes.
    revision = sha256(repr((kind, zone, title, text)).encode()).hexdigest()
    return {"reference": f"{digest}:{unit}:{part}:{revision}", "source_id": source_id(kind, digest),
            "kind": kind, "section": zone, "section_label": SECTIONS[zone], "title": title,
            "slide": unit, "chunk": part, "text": text}


def resolve_references(repository, references):
    if not references:
        return []
    matches = []
    with _store(repository) as db:
        for reference in references:
            digest, unit, part, _ = reference.split(":")
            row = db.execute("""
                SELECT d.digest,d.kind,d.section,d.title,c.unit,c.part,c.text
                FROM chunks c JOIN documents d ON d.digest=c.digest
                WHERE c.digest=? AND c.unit=? AND c.part=?
            """, (digest, int(unit), int(part))).fetchone()
            if row:
                hit = _reference(*row)
                if hit["reference"] == reference:
                    matches.append(hit)
    return matches


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


def _trim_weak_candidates(ranked, semantic, lexical):
    if not semantic or semantic[0]["score"] < CONFIDENT_SEMANTIC_SCORE:
        return ranked
    # Secondary hits need close semantic similarity or strong independent keyword support.
    best = semantic[0]
    semantic_cutoff = max(MIN_SEMANTIC_TAIL_SCORE, best["score"] - MAX_SEMANTIC_TAIL_GAP)
    supported = {ranked[0]["reference"], best["reference"]}
    supported.update(hit["reference"] for hit in semantic if hit["score"] >= semantic_cutoff)
    if lexical:
        keyword_cutoff = lexical[0]["score"] * MIN_KEYWORD_SCORE_RATIO
        supported.update(hit["reference"] for hit in lexical if hit["score"] >= keyword_cutoff)
    return [hit for hit in ranked if hit["reference"] in supported]


def retrieve(repository, query, section="", *, context_query=False):
    if not isinstance(section, str) or (section and section not in SECTIONS):
        raise ValueError("检索分区无效")
    if not isinstance(query, str) or len(query) > 240:
        raise ValueError("搜索问题最多 240 字")
    terms = vector(query)
    identifiers = {
        term for term in terms
        if term.isascii() and any(c.isdigit() for c in term)
        and (any(c.isalpha() for c in term) or len(term) >= 4)
    }
    exact_only = bool(identifiers) and not context_query
    required_evidence = None if context_query else evidence_requirement(query)
    search_query = query if context_query or exact_only else _expand_query(query)
    expanded = search_query != query
    if expanded:
        terms = vector(search_query)
    matches = []
    with _store(repository) as db:
        index = getattr(repository, "semantic_index", None)
        if index is not None:
            db.execute("BEGIN IMMEDIATE")
        params = [section] if section else []
        filters = ["d.section=?"] if section else []
        scope = "WHERE " + " AND ".join(filters) if filters else ""
        populated = db.execute("SELECT 1 FROM documents d " + scope + " LIMIT 1", params).fetchone()
        if required_evidence:
            db.create_function("qualifies_as_evidence", 1, required_evidence, deterministic=True)
            filters.append("qualifies_as_evidence(c.text)")
        where = "WHERE " + " AND ".join(filters) if filters else ""
        if terms and populated:
            values = ",".join("(?,?,?)" for _ in terms)
            # A complete material code is discriminating even in a long block of prose.
            bindings = [value for term, weight in terms.items() for value in (term, weight, int(term in identifiers))]
            threshold = "identifiers>0" if exact_only else "identifiers>0 OR score>=0.10"
            sql = f"""WITH q(term,w,identifier) AS (VALUES {values})
                SELECT d.digest,d.kind,d.section,d.title,c.unit,c.part,c.text,SUM(q.w*p.weight) AS score,
                       SUM(q.identifier) AS identifiers
                FROM q JOIN postings p ON p.term=q.term JOIN chunks c ON c.id=p.chunk
                JOIN documents d ON d.digest=c.digest {where}
                GROUP BY c.id HAVING {threshold}
                ORDER BY identifiers DESC,score DESC,d.digest,c.unit,c.part LIMIT 3"""
            for digest, kind, zone, title, unit, part, text, score, count in db.execute(sql, bindings + params):
                matches.append({**_reference(digest, kind, zone, title, unit, part, text),
                                "score": round(score, 4), "matched_identifiers": count})
            if index is not None and not exact_only:
                semantic = index.sync_and_search(db, search_query, section, required_evidence=required_evidence)
                exact = [m for m in matches if m["matched_identifiers"]]
                semantic_hits = [
                    {**_reference(*row), "score": round(score, 4), "matched_identifiers": 0}
                    for row, score in semantic
                ]
                ranked = exact + semantic_hits
                if expanded:
                    # After vocabulary expansion, boost candidates supported by both methods.
                    lexical = {hit["reference"] for hit in matches}
                    ranked.sort(key=lambda hit: hit["reference"] not in lexical)
                seen = set()
                combined = []
                for hit in ranked + matches:
                    key = hit["source_id"], hit["slide"], hit["chunk"]
                    if key not in seen:
                        combined.append(hit)
                        seen.add(key)
                if not context_query:
                    combined = _trim_weak_candidates(combined, semantic_hits, matches)
                matches = combined[:3]
    return {"status": "retrieved" if matches else "no_match" if populated else "empty",
            "method": method(repository), "matches": matches}
