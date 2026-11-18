import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bom_change_assistant import knowledge_store as kb
from bom_change_assistant.repository import Repository
from bom_change_assistant.semantic_index import METHOD, SemanticIndex


@unittest.skipUnless(os.environ.get("BOM_TEST_MODEL"), "set BOM_TEST_MODEL to the verified local model directory")
class EngineeringSemanticTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "test.sqlite3"
        self.reopen()

    def reopen(self):
        self.repo = Repository(self.path)
        self.repo.semantic_index = SemanticIndex(self.path.with_suffix(".qdrant"), os.environ["BOM_TEST_MODEL"])

    def add(self, key, zone, excerpts):
        return kb.replace_document(self.repo, key * 64, "text", zone, "", excerpts)

    def test_dense_paraphrase_and_exact_codes_are_distinct(self):
        source = "替代件启用前核对装配间隙，并复测安装面的尺寸。KBSWITCH-8079。"
        query = "新零件装上去会不会卡住，要量什么？"
        self.assertFalse(set(kb.vector(source)) & set(kb.vector(query)))
        self.add("a", "standards", {2: source, 5: "来料记录按送货批次汇总，与仓库收货单对账。"})
        self.add("b", "validation", {1: "COMP-998 装配复测。", 3: "COMP-999 交期复核。"})
        self.reopen()
        refs = kb.retrieve(self.repo, query, "standards")
        self.assertEqual(refs["method"], METHOD)
        self.assertEqual((refs["matches"][0]["text"], refs["matches"][0]["slide"]), (source, 2))
        self.assertEqual(kb.retrieve(self.repo, "天气预报和足球比分")["status"], "no_match")
        self.assertEqual(kb.retrieve(self.repo, "comp-998 装配复测")["matches"][0]["slide"], 1)
        self.assertFalse(kb.retrieve(self.repo, "COMP-997 装配复测")["matches"])
        self.assertFalse(kb.retrieve(self.repo, "COMP-998", "standards")["matches"])

    def test_reindex_move_restart_and_stale_vectors_after_interrupted_write(self):
        self.add("a", "changes", {1: "安装后记录绝缘阻值。"})
        index = self.repo.semantic_index
        sync = index.sync_and_search
        def interrupted(db):
            sync(db)
            raise OSError("simulate interruption before approved text commits")
        with patch.object(index, "sync_and_search", side_effect=interrupted), self.assertRaises(OSError):
            self.add("a", "standards", {5: "核对送货单及到货数量。"})
        self.reopen()
        self.assertEqual(kb.retrieve(self.repo, "安装后记录绝缘阻值", "changes")["matches"][0]["slide"], 1)
        self.assertEqual(kb.retrieve(self.repo, "核对送货单", "standards")["status"], "empty")
        self.add("a", "standards", {7: "到货核对箱数和数量。"})
        self.reopen()
        self.assertEqual(kb.retrieve(self.repo, "安装后记录绝缘阻值", "changes")["status"], "empty")
        self.assertEqual(kb.retrieve(self.repo, "到货核对数量", "standards")["matches"][0]["slide"], 7)
