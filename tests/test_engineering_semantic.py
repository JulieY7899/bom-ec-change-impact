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
        refs = kb.retrieve(self.repo, query, "standards", context_query=True)
        self.assertEqual(refs["method"], METHOD)
        self.assertEqual((refs["matches"][0]["text"], refs["matches"][0]["slide"]), (source, 2))
        self.assertEqual(kb.retrieve(self.repo, "天气预报和足球比分")["status"], "no_match")
        self.assertEqual(kb.retrieve(self.repo, "comp-998 装配复测")["matches"][0]["slide"], 1)
        self.assertFalse(kb.retrieve(self.repo, "COMP-997 装配复测")["matches"])
        self.assertFalse(kb.retrieve(self.repo, "COMP-998", "standards")["matches"])

    def test_dense_contract_search_requires_requested_values(self):
        query = "未履行采购承诺的撤单赔偿金额和合同编号"
        self.add("a", "standards", {1: "按订单行核查未履行采购承诺，撤单赔偿金额及合同编号需向采购确认。"})
        self.assertEqual(kb.retrieve(self.repo, query, "standards")["status"], "no_match")
        valid = "未履行采购承诺对应合同编号 CT-053，撤单赔偿金额为 800 元。"
        self.add("b", "standards", {1: valid})
        result = kb.retrieve(self.repo, query, "standards")
        self.assertEqual(result["method"], METHOD)
        self.assertEqual([hit["text"] for hit in result["matches"]], [valid])

    def test_dense_search_does_not_fill_remaining_slots_with_unrelated_candidates(self):
        valid = "仓库按物料凭证逐笔核对累计净收货。入库为正，退供应商为负；成对冲销按凭证抵销，记录流水覆盖期。"
        self.add("a", "standards", {
            1: valid,
            2: "厂内余额须拆分自由使用、待检与冻结。寄售须确认是否取得领用权，取得之前不并入可用库存。",
            3: "对客户订单逐行保留选配标识，按实际引用的设计版次追到装机清单。需求日期只用于交付检查。",
        })
        query = "供应商送来的货被拉走了一批，查哪些凭证才能核对累计净收货？"
        with patch.object(kb, "_trim_weak_candidates", side_effect=lambda ranked, *_: ranked):
            self.assertGreater(len(kb.retrieve(self.repo, query, "standards")["matches"]), 1)
        self.assertEqual([hit["text"] for hit in kb.retrieve(self.repo, query, "standards")["matches"]], [valid])

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
