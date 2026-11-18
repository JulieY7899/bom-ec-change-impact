"""Local PowerPoint excerpt review, indexing and retrieval."""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import math
import posixpath
import re
from collections import Counter
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

MAX_UPLOAD = 12 * 1024 * 1024
MAX_SLIDES = 80
MAX_XML = 512 * 1024
MAX_EXCERPT = 1200
MAX_DOCUMENTS = 30
ALGORITHM = "local-lexical-vector-v1"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
TOKEN = re.compile(r"[0-9a-z]+|[\u3400-\u9fff]+")
DOC_ID = re.compile(r"[0-9a-f]{64}")

SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_excerpts (
    deck_digest TEXT NOT NULL,
    slide_number INTEGER NOT NULL,
    excerpt TEXT NOT NULL,
    vector_json TEXT NOT NULL,
    PRIMARY KEY (deck_digest, slide_number)
);
"""


def sign(repository, purpose, payload):
    message = json.dumps([purpose, payload], ensure_ascii=False, sort_keys=True).encode()
    return hmac.new(repository.privacy_key().secret, message, hashlib.sha256).hexdigest()


def signed(repository, purpose, payload, token):
    return isinstance(token, str) and token.isascii() and hmac.compare_digest(
        sign(repository, purpose, payload), token
    )


def _xml(archive, name):
    info = archive.getinfo(name)
    if info.file_size > MAX_XML:
        raise ValueError("PPTX 内有过大的结构或页面，请拆成较小的演示文稿")
    data = archive.read(info).decode("utf-8-sig")
    if "<!" in data:
        if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", data, re.IGNORECASE):
            raise ValueError("PPTX XML 包含不支持的声明")
    return ET.fromstring(data)


def _slide_paths(archive):
    structure = _xml(archive, "ppt/presentation.xml")
    relations = _xml(archive, "ppt/_rels/presentation.xml.rels")
    paths = {}
    for link in relations.findall(f"{{{RELS}}}Relationship"):
        if link.get("Type") != f"{R}/slide" or link.get("TargetMode") == "External":
            continue
        target = link.get("Target") or ""
        path = posixpath.normpath(target.lstrip("/") if target.startswith("/") else f"ppt/{target}")
        if re.fullmatch(r"ppt/slides/slide[0-9]+\.xml", path):
            paths[link.get("Id")] = path
    counted = structure.findall(f"./{{{P}}}sldIdLst/{{{P}}}sldId")
    if not counted or len(counted) > MAX_SLIDES:
        raise ValueError(f"请选择含 1 到 {MAX_SLIDES} 页的 PPTX")
    return [paths[item.get(f"{{{R}}}id")] for item in counted]


def preview(repository, body):
    if not 0 < len(body) <= MAX_UPLOAD:
        raise ValueError("请选择 12 MB 以内的 PPTX")
    try:
        with ZipFile(io.BytesIO(body)) as archive:
            paths = _slide_paths(archive)
            slides = []
            for number, name in enumerate(paths, 1):
                root = _xml(archive, name)
                paragraphs = (
                    "".join(node.text or "" for node in paragraph.iter(f"{{{A}}}t"))
                    for paragraph in root.iter(f"{{{A}}}p")
                )
                # Omit hyperlink addresses, notes, images and other embedded objects.
                text = "\n".join(line.strip() for line in paragraphs if line.strip())
                if text:
                    slides.append({"slide": number, "text": text[:MAX_EXCERPT],
                                   "truncated": len(text) > MAX_EXCERPT})
    except (BadZipFile, KeyError, ET.ParseError, UnicodeError, RuntimeError, NotImplementedError) as exc:
        raise ValueError("无法读取此 PPTX，请使用标准 PowerPoint 演示文稿") from exc
    if not slides:
        raise ValueError("没有可提取的文本页。请先把图片或扫描页转成文本")
    source = {"digest": hashlib.sha256(body).hexdigest(),
              "slides": [entry["slide"] for entry in slides], "page_count": len(paths)}
    return {**source, "excerpts": slides, "review_token": sign(repository, "ppt-preview", source)}


def library(repository):
    with repository.connect() as connection:
        counts = connection.execute("""
            SELECT COUNT(DISTINCT deck_digest), COUNT(*) FROM knowledge_excerpts
        """).fetchone()
    return {"documents": counts[0], "excerpts": counts[1], "method": ALGORITHM}


def revision(repository):
    with repository.connect() as connection:
        row = connection.execute("SELECT value FROM meta WHERE key='knowledge_revision'").fetchone()
    return row[0] if row else ""


def index(repository, payload):
    source = {field: payload.get(field) for field in ("digest", "slides", "page_count")}
    if not signed(repository, "ppt-preview", source, payload.get("review_token")):
        raise ValueError("PPTX 预览凭证失效，请重新提取")
    digest = source["digest"]
    if not DOC_ID.fullmatch(digest) or payload.get("approved") is not True:
        raise ValueError("请逐页确认摘录已脱敏再加入知识库")
    picked = payload.get("excerpts")
    if not isinstance(picked, list) or not 0 < len(picked) <= MAX_SLIDES:
        raise ValueError("请至少勾选一页已审核摘录")
    rows = {}
    for item in picked:
        if not isinstance(item, dict):
            raise ValueError("摘录格式无效")
        slide, text = item.get("slide"), item.get("text")
        if type(slide) is not int or slide not in source["slides"] or slide in rows:
            raise ValueError("摘录页码重复或超出原演示文稿")
        if not isinstance(text, str) or not 0 < len(text.strip()) <= MAX_EXCERPT:
            raise ValueError(f"每页摘录须包含 1 到 {MAX_EXCERPT} 个字符")
        rows[slide] = text.strip()
    with repository.connect() as connection:
        known = connection.execute(
            "SELECT 1 FROM knowledge_excerpts WHERE deck_digest=? LIMIT 1", (digest,)
        ).fetchone()
        if not known and connection.execute(
            "SELECT COUNT(DISTINCT deck_digest) FROM knowledge_excerpts"
        ).fetchone()[0] >= MAX_DOCUMENTS:
            raise ValueError(f"当前本机知识库最多容纳 {MAX_DOCUMENTS} 份演示文稿")
        connection.execute("DELETE FROM knowledge_excerpts WHERE deck_digest=?", (digest,))
        connection.executemany(
            "INSERT INTO knowledge_excerpts VALUES (?, ?, ?, ?)",
            ((digest, slide, text, json.dumps(_vector(text))) for slide, text in sorted(rows.items())),
        )
        included = connection.execute("""
            SELECT deck_digest, slide_number, excerpt FROM knowledge_excerpts
            ORDER BY deck_digest, slide_number
        """)
        checksum = hashlib.sha256()
        for entry in included:
            checksum.update(json.dumps(list(entry)).encode())
        connection.execute("""
            INSERT INTO meta(key, value) VALUES('knowledge_revision', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """, (checksum.hexdigest(),))
    return {**library(repository), "source_id": _source_id(digest), "indexed": len(rows)}


def _source_id(digest):
    return f"PPT-{digest[:16].upper()}"


def _vector(text):
    features = Counter()
    for word in TOKEN.findall(text.casefold()):
        if word.isascii():
            features[word] += 1
        else:
            features.update(word[i:i + 2] for i in range(len(word) - 1))
    weights = {key: 1 + math.log(value) for key, value in features.items()}
    norm = math.sqrt(sum(value * value for value in weights.values()))
    return {key: value / norm for key, value in weights.items()} if norm else {}


def retrieve(repository, query):
    vector = _vector(query)
    with repository.connect() as connection:
        rows = connection.execute("""
            SELECT deck_digest, slide_number, excerpt, vector_json FROM knowledge_excerpts
        """).fetchall()
    hits = []
    for digest, number, text, indexed in rows:
        candidate = json.loads(indexed)
        score = sum(weight * candidate.get(key, 0) for key, weight in vector.items())
        if score >= 0.10:
            hits.append({"source_id": _source_id(digest), "slide": number,
                         "text": text, "score": round(score, 4)})
    hits.sort(key=lambda hit: (-hit["score"], hit["source_id"], hit["slide"]))
    state = "retrieved" if hits else "no_match" if rows else "empty"
    return {"status": state, "method": ALGORITHM, "matches": hits[:3]}
