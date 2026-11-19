import tempfile
import unittest
from pathlib import Path

from bom_change_assistant import knowledge_store as kb
from bom_change_assistant.repository import Repository


class LibraryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Repository(Path(temp.name) / "bom.sqlite3")

    def test_legacy_is_copied_once_and_reindex_moves_only_approved_chunks(self):
        digest = "a" * 64
        with self.repo.connect() as db:
            db.execute("INSERT INTO knowledge_excerpts VALUES (?,?,?,?)", (digest, 2, "旧版指引", "{}"))
        self.assertEqual(kb.retrieve(self.repo, "旧版指引", "unfiled")["matches"][0]["slide"], 2)
        result = kb.replace_document(self.repo, digest, "pptx", "changes", "案例", {5: "换料后复核版本。"})
        self.assertEqual((result["documents"], result["chunks"]), (1, 1))
        self.assertEqual(kb.retrieve(self.repo, "旧版指引", "unfiled")["status"], "empty")
        hits = kb.retrieve(Repository(self.repo.database_path), "替代", "changes")["matches"]
        self.assertEqual((hits[0]["title"], hits[0]["slide"], hits[0]["chunk"]), ("案例", 5, 1))
        with self.repo.connect() as db:
            self.assertEqual(db.execute("SELECT excerpt FROM knowledge_excerpts").fetchone()[0], "旧版指引")

    def test_long_units_scope_code_queries_and_ordering(self):
        long = "检验记录。" * 67 + "在 REF-4321 部件上检查安装面。"
        result = kb.replace_document(self.repo, "b"*64, "text", "validation", "", {3: long})
        self.assertGreater(result["indexed_chunks"], 1)
        self.assertTrue(all(len(c) <= kb.CHUNK_SIZE for c in kb.chunks(long)))
        kb.replace_document(self.repo, "c"*64, "pptx", "standards", "", {1: "REF-4321 材料号细则"})
        hits = kb.retrieve(self.repo, "REF-4321", "validation")["matches"]
        self.assertEqual((hits[0]["slide"], hits[0]["kind"]), (3, "text"))
        self.assertGreater(hits[0]["chunk"], 1)
        self.assertIn("安装面", hits[0]["text"])
        self.assertEqual(kb.retrieve(self.repo, "天文轨道", "validation")["status"], "no_match")
        related = kb.retrieve(self.repo, "REF-4321")["matches"]
        self.assertEqual({h["section"] for h in related}, {"standards", "validation"})

    def test_exact_code_survives_low_cosine_in_dense_text(self):
        text = " ".join(f"ITEM-{n:04}" for n in range(28)) + " REF-4321"
        kb.replace_document(self.repo, "f"*64, "text", "changes", "", {1: text})
        result = kb.retrieve(self.repo, "please check REF-4321 in the latest assembly revision")
        self.assertEqual(result["matches"][0]["matched_identifiers"], 1)
        self.assertLess(result["matches"][0]["score"], 0.1)
        self.assertEqual(kb.retrieve(self.repo, "REF-432")["matches"], [])

    def test_explicit_identifier_filters_near_codes_and_topic_only_matches(self):
        near = "PART-4050 的尺寸验证专用检具须按图纸核对。"
        topical = "尺寸验证专用检具应记录校准日期。"
        kb.replace_document(self.repo, "a" * 64, "text", "validation", "", {1: near, 2: topical})
        query = "PART-405 尺寸验证专用检具"
        explicit = kb.retrieve(self.repo, query, "validation")
        self.assertEqual((explicit["status"], explicit["matches"]), ("no_match", []))

        for hits in (
            kb.retrieve(self.repo, "尺寸验证专用检具", "validation")["matches"],
            kb.retrieve(self.repo, query, "validation", context_query=True)["matches"],
        ):
            self.assertEqual({hit["text"] for hit in hits}, {near, topical})

        exact = "PART-405 的尺寸验证专用检具须核对安装面。"
        kb.replace_document(self.repo, "b" * 64, "text", "validation", "", {1: exact})
        explicit = kb.retrieve(self.repo, query.lower(), "validation")
        self.assertEqual([hit["text"] for hit in explicit["matches"]], [exact])

    def test_stock_depletion_question_finds_cutover_conditions(self):
        excerpt = "允许消耗旧料的情形须复核剩余需求和新料供应，再确认切换触发条件。"
        kb.replace_document(self.repo, "a" * 64, "text", "standards", "", {1: excerpt})
        result = kb.retrieve(self.repo, "等旧零件用光才换新，什么时候能换？", "standards")
        self.assertEqual([hit["text"] for hit in result["matches"]], [excerpt])

    def test_explicit_business_questions_keep_only_applicable_evidence(self):
        row = "销售订单应按稳定 BOM 行标识逐行核对选配。"
        conflict = "同一订单行同时被纳入和剔除时须保留证据，并标为待核。"
        window = "生效窗口包括首日与末日。"
        early = "订单需求日期先于变更生效日期时，计划核对实际执行和库存占用。"
        stock = "寄售只有取得使用权才能并入可用库存。"
        kb.replace_document(self.repo, "a" * 64, "text", "standards", "", {
            1: row, 2: conflict, 3: window, 4: early, 5: stock,
            6: "销售订单和各物料汇总库存状态。待检或寄售仓位单独复核。",
            7: "按采购订单行核对配置编码和采购承诺。",
            8: "订单需求日期等于变更生效日期时，确认当日的交期和工序。",
        })
        checks = (
            ("销售订单按固定行号逐行核对配置，另有配置矛盾时怎么办？", [row, conflict]),
            ("订单需求日期先于变更生效日期时怎么核对？生效窗口包含首日和末日吗？", [window, early]),
            ("寄售的仓位余额能算可用库存吗？", [stock]),
        )
        for query, expected in checks:
            with self.subTest(query=query):
                result = kb.retrieve(self.repo, query, "standards")
                self.assertCountEqual([hit["text"] for hit in result["matches"]], expected)

    def test_text_review_token_and_partition_validation(self):
        from bom_change_assistant import knowledge
        parsed = knowledge.preview_text(self.repo, "图纸校核\n\n材料依据".encode())
        self.assertEqual(parsed["slides"], [1, 2])
        payload = {**parsed, "approved": True, "section": "standards", "excerpts": [parsed["excerpts"][1]]}
        with self.assertRaises(ValueError):
            knowledge.index(self.repo, {**payload, "kind": "pptx"})
        knowledge.index(self.repo, payload)
        with self.assertRaises(ValueError):
            knowledge.index(self.repo, {**payload, "section": "unknown"})
        self.assertEqual(kb.retrieve(self.repo, "图纸校核")["status"], "no_match")
        self.assertEqual(kb.retrieve(self.repo, "材料依据")["matches"][0]["slide"], 2)
