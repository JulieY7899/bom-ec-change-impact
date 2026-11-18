"""Local PowerPoint excerpt review, indexing and retrieval."""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import posixpath
import re
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from .knowledge_store import replace_document

MAX_UPLOAD = 12 * 1024 * 1024
MAX_SLIDES = 80
MAX_XML = 512 * 1024
MAX_EXCERPT = 1200
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
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


def preview_text(repository, body):
    if len(body) > 256 * 1024:
        raise ValueError("文本材料最多 256 KB，请分成多份材料")
    try:
        text = body.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeError as exc:
        raise ValueError("文本材料请保存为 UTF-8 编码") from exc
    if "\x00" in text:
        raise ValueError("请选择可读的 UTF-8 文本")
    paragraphs = [x.strip() for x in re.split(r"\n[ \t]*\n", text) if x.strip()]
    if not 1 <= len(paragraphs) <= MAX_SLIDES or any(len(x) > MAX_EXCERPT for x in paragraphs):
        raise ValueError("每份文本支持 1 至 80 段，每段最多 1200 字。请用空行分段")
    source = {"digest": hashlib.sha256(body).hexdigest(), "kind": "text",
              "slides": list(range(1, len(paragraphs) + 1)), "page_count": len(paragraphs)}
    return {**source, "excerpts": [{"slide": n, "text": line, "truncated": False}
                                    for n, line in enumerate(paragraphs, 1)],
            "review_token": sign(repository, "ppt-preview", source)}


def preview_legacy(repository, body, converter=None):
    if not 0 < len(body) <= MAX_UPLOAD or not body.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
        raise ValueError("请选择 12 MB 以内的 PowerPoint 97–2003 演示文稿")
    if converter is None:
        installed = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
        converter = installed if installed.is_file() else shutil.which("soffice")
    if not converter:
        raise ValueError("本机未配置 PPT 转换。请使用 PPTX，或配置本机 LibreOffice")
    with TemporaryDirectory(prefix="bom-legacy-ppt-") as temporary:
        directory = Path(temporary)
        source_file = directory / "material.ppt"
        source_file.write_bytes(body)
        profile = directory / "lo" / "user"
        profile.mkdir(parents=True)
        (profile / "registrymodifications.xcu").write_text(
            '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
            '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
            '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item></oor:items>',
            encoding="utf-8",
        )
        command = [str(converter), "-env:UserInstallation=" + profile.parent.as_uri(),
                   "--headless", "--norestore", "--nodefault", "--infilter=MS PowerPoint 97",
                   "--convert-to", "pptx", "--outdir", str(directory), str(source_file)]
        # The legacy document converter only needs local files.
        sandbox = Path("/usr/bin/sandbox-exec")
        if sandbox.is_file():
            command[:0] = [str(sandbox), "-p", "(version 1) (allow default) (deny network*)"]
        try:
            subprocess.run(command, check=True, timeout=25, cwd=directory, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            output = directory / "material.pptx"
            if output.stat().st_size > MAX_UPLOAD:
                raise ValueError("PPT 转换后的演示文稿超过 12 MB，请拆分后再上传")
            return preview(repository, output.read_bytes(), legacy_body=body)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError("无法在本机转换此 PPT，请另存为 PPTX 后重试") from exc


def preview(repository, body, *, legacy_body=None):
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
    source = {"digest": hashlib.sha256(body if legacy_body is None else legacy_body).hexdigest(),
              "slides": [entry["slide"] for entry in slides], "page_count": len(paths)}
    if legacy_body is not None:
        source["kind"] = "ppt"
    return {**source, "excerpts": slides, "review_token": sign(repository, "ppt-preview", source)}


def index(repository, payload):
    source = {field: payload.get(field) for field in ("digest", "slides", "page_count")}
    if "kind" in payload:
        source["kind"] = payload["kind"]
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
    return replace_document(repository, digest, source.get("kind", "pptx"),
                            payload.get("section", "unfiled"), payload.get("title", ""), rows)
