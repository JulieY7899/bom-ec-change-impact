"""On-device Chinese embeddings for approved engineering excerpts."""
from __future__ import annotations

from contextlib import closing
from hashlib import sha256
from pathlib import Path
import re
from threading import Lock
from uuid import UUID

MODEL_REPO = "Xenova/bge-small-zh-v1.5"
REVISION = "75c43b069aac4d136ba6bc1122f995fedcfd2781"
FILES = {
    "config.json": "d4193ead3a810fd694fa8a31d7fc72fbaebc0668b603e398734bf2f6538ff42f",
    "special_tokens_map.json": "b6d346be366a7d1d48332dbc9fdf3bf8960b5d879522b7799ddba59e76237ee3",
    "tokenizer.json": "48cea5d44424912a6fd1ea647bf4fe50b55ab8b1e5879c3275f80e339e8fae26",
    "tokenizer_config.json": "e6f3b96db926a37d4039995fbf5ad17de158dfb8f6343d607e4dbaad18d75f5a",
    "onnx/model_quantized.onnx": "15b717c382bcb518ba457b93ea6850ede7f4f1cd8937454aa06972366cd19bcc",
}
METHOD = "qdrant-local-bge-small-zh-v1.5-int8"
COLLECTION = "approved_zh_v2_" + REVISION[:12]
MIN_SCORE = 0.50
BATCH_SIZE = 2
MODEL_NAME = "bom/bge-small-zh-int8"
_registration_lock = Lock()


def verified(path, expected):
    with path.open("rb") as stream:
        digest = sha256()
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest() == expected


# Codes are indexed verbatim in SQLite. Opaque identifiers add noise to Chinese embeddings.
CODE = re.compile(r"(?<![A-Za-z0-9_.-])(?=[A-Za-z0-9_.-]*[0-9])(?:[A-Za-z][A-Za-z0-9_.-]*|[0-9][A-Za-z0-9]*[-_][A-Za-z0-9_.-]+)(?![A-Za-z0-9_.-])")


def semantic_text(text):
    return CODE.sub(" ", text).strip(" 。；，、") or text


class SemanticIndex:
    def __init__(self, path, model_dir):
        self.path = Path(path)
        self.model_dir = Path(model_dir)
        self.encoder = None
        for name, expected in FILES.items():
            file = self.model_dir / name
            if not file.is_file() or not verified(file, expected):
                raise ValueError("工程检索模型缺失或校验失败，请重新准备本机模型。")

    def encode(self, texts):
        from fastembed import TextEmbedding
        from fastembed.common.model_description import ModelSource, PoolingType

        if self.encoder is None:
            with _registration_lock:
                if not any(m["model"] == MODEL_NAME for m in TextEmbedding.list_supported_models()):
                    TextEmbedding.add_custom_model(MODEL_NAME, PoolingType.CLS, True,
                                                   ModelSource(hf=MODEL_REPO), 512, "onnx/model_quantized.onnx")
            self.encoder = TextEmbedding(model_name=MODEL_NAME, specific_model_path=str(self.model_dir),
                                        providers=["CPUExecutionProvider"], threads=1, enable_cpu_mem_arena=False,
                                        cache_dir=str(self.path / "runtime"))
        return self.encoder.embed(texts, batch_size=BATCH_SIZE)

    def sync_and_search(self, db, query="", section="", *, required_evidence=None):
        """Caller holds the authoritative SQLite write lock through reconciliation and readback."""
        from qdrant_client import QdrantClient, models

        self.path.mkdir(mode=0o700, parents=True, exist_ok=True)
        rows = db.execute("""SELECT d.digest,d.kind,d.section,d.title,c.unit,c.part,c.text
                             FROM chunks c JOIN documents d ON c.digest=d.digest""")
        approved = {}
        for row in rows:
            digest, _, zone, _, unit, part, text = row
            key = f"{digest}\0{zone}\0{unit}\0{part}\0{text}".encode()
            approved[str(UUID(bytes=sha256(key).digest()[:16]))] = row
        with closing(QdrantClient(path=str(self.path))) as client:
            if not client.collection_exists(COLLECTION):
                client.create_collection(COLLECTION, vectors_config=models.VectorParams(
                    size=512, distance=models.Distance.COSINE))
            present = set()
            offset = None
            while True:
                points, offset = client.scroll(COLLECTION, limit=256, offset=offset,
                                                with_payload=False, with_vectors=False)
                present.update(p.id for p in points)
                if offset is None:
                    break
            missing = sorted(approved.keys() - present)
            for start in range(0, len(missing), BATCH_SIZE):
                batch = missing[start:start + BATCH_SIZE]
                vectors = self.encode([semantic_text(approved[key][6]) for key in batch])
                points = [models.PointStruct(id=key, vector=v.tolist(), payload={"section": approved[key][2]})
                          for key, v in zip(batch, vectors, strict=True)]
                client.upsert(COLLECTION, points, wait=True)
            obsolete = present - approved.keys()
            if obsolete:
                client.delete(COLLECTION, models.PointIdsList(points=sorted(obsolete)), wait=True)
            if not query or not approved:
                return []
            conditions = []
            if section:
                conditions.append(models.FieldCondition(key="section", match=models.MatchValue(value=section)))
            if required_evidence:
                eligible = [key for key, row in approved.items()
                            if (not section or section == row[2]) and required_evidence(row[6])]
                if not eligible:
                    return []
                conditions.append(models.HasIdCondition(has_id=eligible))
            scope = models.Filter(must=conditions) if conditions else None
            vector = next(iter(self.encode(["为这个句子生成表示以用于检索相关文章：" + semantic_text(query)])))
            hits = client.query_points(COLLECTION, query=vector.tolist(), query_filter=scope,
                                       score_threshold=MIN_SCORE, limit=3, with_payload=False).points
            return [(approved[hit.id], hit.score) for hit in hits if hit.id in approved]
