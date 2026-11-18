from __future__ import annotations

import argparse
import json
import re
import secrets
import sqlite3
import tempfile
import threading
import webbrowser
from contextlib import ExitStack, closing
from dataclasses import dataclass
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.parse import parse_qs, urlparse
from zipfile import BadZipFile

from . import BUILD_ID, __version__, engineering, knowledge, workflow, workflow_dialogue
from .config import (
    DATABASE_PATH,
    HOST,
    MAX_UPLOAD_BYTES,
    PORT,
)
from .demo import load_demo_snapshot
from .analysis_service import AnalysisService
from .impact_engine import ImpactError
from .importers import WorkbookImporter
from .repository import Repository
from .summaries import markdown_report

def _local_request_allowed(host: str, origin: str = "") -> bool:
    """Defend localhost UI against cross-site browser POSTs and DNS rebinding."""
    try:
        target = urlparse("http://" + host)
        if target.hostname not in {"localhost", "127.0.0.1", "::1"}:
            return False
        if target.username or target.password:
            return False
        target_port = target.port or 80
        if origin:
            source = urlparse(origin)
            if (source.scheme != "http" or source.hostname != target.hostname or
                    source.username or source.password or
                    (source.port or 80) != target_port):
                return False
        return True
    except ValueError:
        return False


ASSET_DIR = Path(__file__).resolve().parent / "web_assets"
TEMPLATE_PATH = (
    Path(__file__).resolve().parents[2]
    / "outputs"
    / "bom-import-template"
    / "BOM工程变更数据导入模板.xlsx"
)
SNAPSHOT_BACKUPS_TO_KEEP = 3


class AppState:
    def __init__(self, database_path: Path):
        self.repository = Repository(database_path)
        self.analysis = AnalysisService(self.repository)
        # Keep analysis and publication consistent without locking around network I/O.
        self.import_lock = threading.RLock()
        self.staging_directory = None
        self.staging_repository = None
        self.staging_importer = None
        self.staging_batch_id = ""
        self.expected_files = None
        self.imported_files = 0

    @property
    def engine(self):
        return self.analysis.engine

    @staticmethod
    def copy_database(source: Path, destination: Path) -> None:
        # SQLite backup includes committed WAL data and publishes atomically.
        with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)

    def begin_import(self, expected_files: int | None = None) -> None:
        if self.staging_repository is not None:
            raise ValueError("已有导入批次进行中，请先完成或取消该批次")
        if expected_files is not None and (type(expected_files) is not int or expected_files < 1):
            raise ValueError("导入文件数必须为正整数")
        self.staging_directory = tempfile.TemporaryDirectory(
            prefix=".bom-import-", dir=self.repository.database_path.parent,
        )
        try:
            stage_path = Path(self.staging_directory.name) / "snapshot.sqlite3"
            self.copy_database(self.repository.database_path, stage_path)
            self.staging_repository = Repository(stage_path)
            self.staging_repository.start_snapshot()
            self.staging_importer = WorkbookImporter(self.staging_repository)
            self.staging_batch_id = secrets.token_urlsafe(16)
            self.expected_files = expected_files
            self.imported_files = 0
        except Exception:
            self.abort_import()
            raise

    def abort_import(self) -> None:
        directory = self.staging_directory
        self.staging_directory = None
        self.staging_repository = None
        self.staging_importer = None
        self.staging_batch_id = ""
        self.expected_files = None
        self.imported_files = 0
        if directory is not None:
            directory.cleanup()

    def finish_import(self) -> dict[str, Any]:
        if self.staging_repository is None or not self.imported_files:
            raise ValueError("没有可完成的导入批次；旧快照保持可用")
        if self.expected_files is not None and self.imported_files != self.expected_files:
            raise ValueError("导入文件数不完整；旧快照保持可用")
        stage = self.staging_repository
        stats = stage.finish_snapshot(require_bom=True)
        # Preserve the previous active database, including its analysis history.
        # This file is local runtime data and must never enter a public commit.
        database = self.repository.database_path
        prefix = "snapshot-backup-" + sha256(database.name.encode()).hexdigest()[:16] + "-"
        with tempfile.NamedTemporaryFile(
            prefix=prefix, suffix=".sqlite3", dir=database.parent, delete=False,
        ) as backup:
            backup_path = Path(backup.name)
        try:
            self.copy_database(database, backup_path)
        except Exception:
            backup_path.unlink(missing_ok=True)
            raise
        self.copy_database(stage.database_path, self.repository.database_path)
        self.abort_import()
        stats["backup_cleanup_pending"] = not self._prune_backups(prefix, backup_path)
        return stats

    @staticmethod
    def _prune_backups(prefix: str, current: Path) -> bool:
        # Old, unscoped backups may belong to a different database. Only
        # manage backups created for this database under the current policy.
        backups = []
        try:
            for path in current.parent.glob(f"{prefix}*.sqlite3"):
                if path != current and path.is_file() and not path.is_symlink():
                    backups.append((path.stat().st_mtime_ns, path))
            backups.sort(reverse=True)
        except OSError:
            return False

        complete = True
        for _, path in backups[SNAPSHOT_BACKUPS_TO_KEEP - 1:]:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                complete = False
        return complete

    def require_no_import(self) -> None:
        if self.staging_repository is not None:
            raise ValueError("导入批次尚未完成；可查看旧报告，请完成或取消后再核查")


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class RequestError(ValueError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Download:
    body: bytes
    name: str
    content_type: str = "text/markdown; charset=utf-8"


JSON_POSTS = frozenset({
    "/api/workflow/dialogue", "/api/workflow/preflight", "/api/workflow/run",
    "/api/engineering-knowledge/index", "/api/snapshot/start", "/api/analyze", "/api/analyze-batch",
})
COMMAND_POSTS = frozenset({"/api/snapshot/finish", "/api/snapshot/abort", "/api/demo"})
IMPORT_POSTS = frozenset({"/api/import/file", "/api/snapshot/finish", "/api/snapshot/abort"})
BODY_TIMEOUT = 30
JSON_LIMIT = 2 * 1024 * 1024


class Handler(BaseHTTPRequestHandler):
    state: AppState
    timeout = BODY_TIMEOUT
    server_version = f"BOMAssistant/{__version__}"

    def log_message(self, *_: Any) -> None:
        # Intentionally disabled: request paths and user inputs must not enter logs.
        return

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str = "application/json; charset=utf-8",
        *,
        attachment: str | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self'; script-src 'self'; "
            "img-src 'self' data:; connect-src 'self'",
        )
        if attachment:
            self.send_header(
                "Content-Disposition", f'attachment; filename="{attachment}"'
            )
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: Any) -> None:
        self._send(status, _json_bytes(payload))

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"ok": False, "error": message})

    def _content_length(self, maximum: int, *, required: bool) -> int:
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding"):
            raise RequestError(400, "不支持分块请求体")
        if not lengths:
            if required:
                raise RequestError(411, "请求缺少 Content-Length")
            return 0
        if len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,20}", lengths[0].strip()):
            raise RequestError(400, "Content-Length 无效")
        length = int(lengths[0])
        if length > maximum:
            raise RequestError(413, "请求内容过大")
        return length

    def _read_body(self, length: int, output) -> None:
        if not length:
            return
        deadline = monotonic() + BODY_TIMEOUT
        old_timeout = self.connection.gettimeout()
        try:
            remaining = length
            while remaining:
                time_left = deadline - monotonic()
                if time_left <= 0:
                    raise TimeoutError
                self.connection.settimeout(time_left)
                chunk = self.rfile.read1(min(1024 * 1024, remaining))
                if not chunk:
                    raise RequestError(400, "请求体不完整")
                output.write(chunk)
                remaining -= len(chunk)
        except TimeoutError as exc:
            raise RequestError(408, "读取请求超时，请重试") from exc
        except ConnectionResetError as exc:
            raise RequestError(400, "请求体不完整") from exc
        finally:
            self.connection.settimeout(old_timeout)

    def _read_json(self, length: int) -> dict[str, Any]:
        body = BytesIO()
        self._read_body(length, body)
        try:
            payload = json.loads(body.getvalue().decode("utf-8")) if length else {}
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise RequestError(400, "请求体须为有效的 JSON 对象") from exc
        if not isinstance(payload, dict):
            raise RequestError(400, "请求体须为 JSON 对象")
        return payload

    def _check_local_request(self) -> bool:
        if not _local_request_allowed(self.headers.get("Host", ""), self.headers.get("Origin", "")):
            self._error(403, "仅允许本机访问")
            return False
        return True

    def do_GET(self) -> None:
        if not self._check_local_request():
            return
        route = urlparse(self.path)
        if route.path == "/api/health":
            self._json(200, {
                "ok": True, "service": "bom-change-assistant",
                "version": __version__, "build": BUILD_ID,
            })
            return
        if not route.path.startswith("/api/"):
            self._serve_asset(route.path)
            return
        try:
            with self.state.import_lock:
                result = self._get_locked(route)
        except RequestError as exc:
            self._error(exc.status, str(exc))
            return
        if isinstance(result, Download):
            self._send(200, result.body, result.content_type, attachment=result.name)
        else:
            self._json(200, {"ok": True, **result})

    def _get_locked(self, route):
        path = route.path
        repository = self.state.repository
        query = parse_qs(route.query)
        if path == "/api/status":
            return {
                **repository.stats(), "privacy_mode": "敏感文字脱敏",
                "ai_configured": False, "ai_model": "disabled",
            }
        if path == "/api/engineering-versions":
            with repository.connect() as connection:
                catalog = engineering.VersionSnapshot(connection).catalog()
            return {"versions": catalog}
        if path == "/api/materials":
            return {"items": repository.material_catalog(query.get("q", [""])[0], 120)}
        if path == "/api/material-effectivity":
            material = query.get("material", [""])[0].strip()
            if not material:
                raise RequestError(400, "请先输入原组件物料号")
            try:
                return repository.material_effectivity(material)
            except ValueError as exc:
                raise RequestError(404, str(exc)) from exc
        if path in {"/api/analysis/last", "/api/analysis/by-id"}:
            if path.endswith("/by-id"):
                latest = repository.analysis_by_id(query.get("analysis_id", [""])[0])
            else:
                latest = repository.last_analysis()
            if not latest:
                raise RequestError(404, "尚未完成影响核查")
            result, _ = latest
            report = markdown_report(result, str(result.get("ai_explanation", "")))
            can_use_knowledge = workflow.engineering_context(repository, result["analysis_id"]) is not None
            return {"result": {**result, "engineering_knowledge_available": can_use_knowledge}, "report": report}
        if path == "/api/change-log":
            return {"items": repository.change_logs()}
        if path == "/api/draft-defaults":
            return repository.draft_defaults()
        if path in {"/api/engineering-knowledge", "/api/engineering-knowledge/references"}:
            context = self._engineering_knowledge_context(route)
            if path.endswith("/references"):
                return workflow.engineering_references(repository, context)
            return knowledge.library(repository)
        if path in {"/api/workflow/run", "/api/workflow/report.md"}:
            run_id = query.get("run_id", [""])[0]
            saved = workflow.get_run(repository, run_id)
            if saved is None:
                if not run_id and path == "/api/workflow/run":
                    return {"run_id": None}
                raise RequestError(404, "当前快照中没有这次工作流记录")
            if path.endswith(".md"):
                return Download(saved["report"].encode("utf-8"), "BOM-workflow-report.md")
            return saved
        if path == "/api/report.md":
            analysis_id = query.get("analysis_id", [""])[0]
            latest = repository.analysis_by_id(analysis_id) if analysis_id else repository.last_analysis()
            if not latest:
                raise RequestError(404, "尚未生成核查报告")
            result, _ = latest
            report = markdown_report(result, str(result.get("ai_explanation", "")))
            return Download(report.encode("utf-8"), "BOM-impact-report.md")
        template_files = {
            "/api/import-template.xlsx": (TEMPLATE_PATH, "BOM-import-template.xlsx"),
            "/api/procurement-template.xlsx": (
                TEMPLATE_PATH.parent.parent / "b19-import-template" / "B19采购与到货导入示例.xlsx",
                "B19-synthetic-import-example.xlsx",
            ),
            "/api/engineering-template.xlsx": (
                TEMPLATE_PATH.parent.parent / "b22-import-template" / "engineering-example.xlsx",
                "B22-engineering-example.xlsx",
            ),
        }
        if path in template_files:
            template, filename = template_files[path]
            try:
                body = template.read_bytes()
            except FileNotFoundError as exc:
                raise RequestError(404, "导入模板尚未生成") from exc
            return Download(body, filename, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        raise RequestError(404, "页面不存在")

    def _serve_asset(self, route_path: str) -> None:
        asset_name = {
            "/": "index.html",
            "/index.html": "index.html",
            "/styles.css": "styles.css",
            "/app.js": "app.js",
            "/workflow.js": "workflow.js",
        }.get(route_path)
        if not asset_name:
            self._error(404, "页面不存在")
            return
        path = ASSET_DIR / asset_name
        content_types = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
        }
        try:
            body = path.read_bytes()
        except FileNotFoundError:
            self._error(404, "静态资源不存在")
            return
        self._send(200, body, content_types.get(path.suffix, "application/octet-stream"))

    def _engineering_knowledge_context(self, route):
        run_id = parse_qs(route.query).get("analysis_id", [""])[0]
        context = workflow.engineering_context(self.state.repository, run_id)
        if context is None:
            raise RequestError(403, "请从已完成的工程角色核查进入工程辅助分析")
        return context

    def do_POST(self) -> None:
        if not self._check_local_request():
            return
        route = urlparse(self.path)
        path = route.path
        if path == "/api/ai":
            self._error(403, "外部 AI 功能已禁用，数据不会发送至模型服务。")
            return
        ppt = path == "/api/engineering-knowledge/preview"
        upload = path == "/api/import/file"
        if not (ppt or upload or path in JSON_POSTS or path in COMMAND_POSTS):
            self._error(404, "接口不存在")
            return

        # An upload belongs to the batch that existed when it began. This keeps
        # a late file or malformed request from affecting a replacement batch.
        batch = None
        try:
            if path in IMPORT_POSTS:
                requested_batch = self.headers.get("X-BOM-Import-Batch")
                with self.state.import_lock:
                    if requested_batch is not None and (
                        not requested_batch or requested_batch != self.state.staging_batch_id
                    ):
                        raise RequestError(409, "导入批次已变更，请重新选择文件")
                    batch = self.state.staging_importer

            with ExitStack() as files:
                length = self._content_length(
                    knowledge.MAX_UPLOAD if ppt else MAX_UPLOAD_BYTES if upload else JSON_LIMIT,
                    required=path not in COMMAND_POSTS,
                )
                upload_path = None
                if ppt or upload:
                    if not length:
                        raise RequestError(400, "没有收到文件内容")
                    if upload:
                        directory = files.enter_context(tempfile.TemporaryDirectory(prefix="bom-upload-"))
                        upload_path = Path(directory) / "import.xlsx"
                        with upload_path.open("wb") as output:
                            self._read_body(length, output)
                        payload = None
                    else:
                        body = BytesIO()
                        self._read_body(length, body)
                        payload = body.getvalue()
                elif path in COMMAND_POSTS:
                    # These commands accept an empty request or an unused JSON object.
                    body = BytesIO()
                    self._read_body(length, body)
                    payload = None
                else:
                    payload = self._read_json(length)

                with self.state.import_lock:
                    try:
                        if path in IMPORT_POSTS and batch is not self.state.staging_importer:
                            raise RequestError(409, "导入批次已变更，请重新选择文件")
                        result = self._post_locked(route, payload, upload_path)
                    except Exception:
                        self._abort_matching_import(path, batch)
                        raise
        except RequestError as exc:
            self.close_connection = True
            if batch is not None:
                with self.state.import_lock:
                    self._abort_matching_import(path, batch)
            self._error(exc.status, str(exc))
            return
        except (ValueError, ImpactError) as exc:
            self._error(400, str(exc))
            return
        except Exception:
            if batch is not None:
                with self.state.import_lock:
                    self._abort_matching_import(path, batch)
            # Do not include raw exception details: they may contain local paths.
            self._error(500, "处理失败；请检查文件格式或数据映射后重试。")
            return
        self._json(200, {"ok": True, **result})

    def _abort_matching_import(self, path: str, batch) -> None:
        if path in {"/api/import/file", "/api/snapshot/finish"} and batch is not None:
            if self.state.staging_importer is batch:
                self.state.abort_import()

    def _post_locked(self, route, payload, upload_path):
        path = route.path
        if path == "/api/workflow/dialogue":
            return workflow_dialogue.collect(payload.get("messages"))
        if path == "/api/workflow/preflight":
            self.state.require_no_import()
            return workflow.prepare(self.state.repository, payload)
        if path == "/api/workflow/run":
            self.state.require_no_import()
            context = workflow.confirm(self.state.repository, payload)
            defaults = self.state.repository.draft_defaults()
            draft = {**context["draft"], "bom_version": defaults["default_bom_version"]}
            item = self.state.analysis.analyze_changes([draft])["items"][0]
            output = workflow.finish(self.state.repository, context, item)
            if payload.get("include_analysis") is True:
                output["analysis"] = {
                    "result": {**item["result"], "engineering_knowledge_available": context["role"] == "engineering"},
                    "report": item["report"],
                }
            return output
        if path in {"/api/engineering-knowledge/preview", "/api/engineering-knowledge/index"}:
            self._engineering_knowledge_context(route)
            if path.endswith("/preview"):
                return knowledge.preview(self.state.repository, payload)
            self.state.require_no_import()
            return knowledge.index(self.state.repository, payload)
        if path == "/api/snapshot/start":
            self.state.begin_import(payload.get("expected_files"))
            return {"batch_id": self.state.staging_batch_id}
        if path == "/api/snapshot/finish":
            return {"stats": self.state.finish_import()}
        if path == "/api/snapshot/abort":
            self.state.abort_import()
            return {}
        if path == "/api/demo":
            self.state.require_no_import()
            self.state.begin_import()
            try:
                load_demo_snapshot(self.state.staging_repository)
                self.state.imported_files = 1
                return {"stats": self.state.finish_import()}
            except Exception:
                self.state.abort_import()
                raise
        if path == "/api/import/file":
            if self.state.staging_importer is None:
                raise ValueError("请先开始新的导入批次；旧快照保持可用")
            try:
                result = self.state.staging_importer.import_file(upload_path)
            except BadZipFile as exc:
                raise ValueError("文件不是可读取的 XLSX；旧快照保持可用") from exc
            self.state.imported_files += 1
            return {"result": result.to_dict()}
        self.state.require_no_import()
        if path == "/api/analyze":
            return self.state.analysis.analyze_changes([payload])["groups"][0]
        changes = payload.get("changes")
        if not isinstance(changes, list) or not changes:
            raise ValueError("本次核查至少需要一个变更项目")
        if len(changes) > 20:
            raise ValueError("单次核查最多支持 20 个变更项目")
        return self.state.analysis.analyze_changes(changes)



def run_server(
    host: str = HOST,
    port: int = PORT,
    database_path: Path = DATABASE_PATH,
    open_browser: bool = True,
) -> None:
    state = AppState(database_path)
    handler = type("BoundHandler", (Handler,), {"state": state})
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("隐私优先版本不允许监听非本机地址")
    server = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}"
    print(f"BOM 工程变更影响分析助手已启动：{url}")
    print("数据仅在本机处理。按 Control-C 停止。")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="BOM 工程变更影响分析助手")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    run_server(
        host=args.host,
        port=args.port,
        database_path=args.database,
        open_browser=not args.no_browser,
    )


if __name__ == "__main__":
    main()
