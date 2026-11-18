"""Department examples derived from the business acceptance matrix."""
import unittest

from bom_change_assistant.workflow_dialogue import collect


# Each row fixes the actor, question being asked and concrete evidence provided.
CASES = (
    ("engineering", "版本与适用范围", "成品 FG-10，工厂 P100，工程版本 V2，BOM 行 R-15，工程这边想查哪些产品工厂适用、配置限制",
     {"product":"FG-10","plant":"P100","engineering_version":"V2","target_bom_row":"R-15"}),
    ("engineering", "切换与临时替代", "工程部，从 MAT-A 换成 MAT-B，2026/12/1 生效，临时替代的切换条件是什么",
     {"old_component":"MAT-A","new_component":"MAT-B","effective_date":"2026-12-01"}),
    ("rd", "组件差异与验证", "研发视角，新件 MAT-B 相对旧件 MAT-A 有哪些差异，验证通过了吗",
     {"new_component":"MAT-B","old_component":"MAT-A"}),
    ("rd", "切换与临时替代", "研发这边，用 MAT-B 顶替 MAT-A，2026-12-01 起，临时替代能用在哪些产品",
     {"new_component":"MAT-B","old_component":"MAT-A","effective_date":"2026-12-01"}),
    ("procurement", "未结采购与处置", "采购角度，采购单 PO-70 行号 00010，未结采购订单能撤单还是继续收货",
     {"purchase_order":"PO-70","order_item":"00010"}),
    ("procurement", "供应商投产与发运", "供应商已投产 40 件、已发货 20 件，采购这边想核对老料的采购责任，旧件 MAT-A",
     {"supplier_produced":"40 件","supplier_shipped":"20 件","old_component":"MAT-A"}),
    ("procurement", "到货与承诺", "我是采购，新料 MAT-B，供应商承诺数量 60 件，承诺到货日 2026/12/3，问新料啥时到",
     {"new_component":"MAT-B","committed_quantity":"60 件","arrival_date":"2026-12-03"}),
    ("planning", "库存与需求平衡", "从物料计划看，工厂 P100，库位 0008，旧料 MAT-A、新料 MAT-B，怎么核对在途与未到货采购量",
     {"plant":"P100","storage_location":"0008","old_component":"MAT-A","new_component":"MAT-B"}),
    ("planning", "齐套与切换", "物控这边，MAT-A 换成 MAT-B，2026/12/1 生效，预计何时齐套、新料还有多少缺口",
     {"old_component":"MAT-A","new_component":"MAT-B","effective_date":"2026-12-01"}),
    ("planning", "库存与需求平衡", "计划部，采购订单 PO-70 行 0010 的在途是否和未交采购量重叠",
     {"purchase_order":"PO-70","order_item":"0010"}),
    ("warehouse", "库存状态与可用量", "仓库这边，物料 MAT-A，工厂 P100，库位 0008，批次 LOT-13 待检，什么时候计入可用量",
     {"material":"MAT-A","plant":"P100","storage_location":"0008","lot":"LOT-13","stock_state":["待检"]}),
    ("warehouse", "隔离退回与转用", "物流视角，批次 L-40 的物料 MAT-A 已隔离，可以退回或转用吗",
     {"lot":"L-40","material":"MAT-A","stock_state":["隔离"]}),
    ("manufacturing", "工单状态与实际用料", "制造工程，工单 MO-101 已领料在制，实际用料 MAT-A，工序 OP-20，查进度",
     {"production_order":"MO-101","material":"MAT-A","operation":"OP-20","work_state":["已领料","在制"]}),
    ("manufacturing", "生产处置与执行点", "从生产现场看，工单 MO-102 已释放未开工，要不要暂停或改料",
     {"production_order":"MO-102","work_state":["已释放未开工"]}),
    ("manufacturing", "工单状态与实际用料", "制造角度，工单 MO-103 已完工未交付，MAT-A 移出 BOM，2026/12/1 生效",
     {"production_order":"MO-103","work_state":["完工未交付"],"old_component":"MAT-A"}),
    ("quality", "新料验证与适用性", "QA 这边，验证状态未通过，新件 MAT-B 新料验证后能否放行",
     {"validation_state":"未通过","new_component":"MAT-B"}),
    ("quality", "库存与在制质量风险", "质检角度，物料 MAT-A，批次 L-40，工单 MO-201 在制，是否有库存和在制风险",
     {"material":"MAT-A","lot":"L-40","production_order":"MO-201"}),
    ("quality", "复验返工与放行", "质量部，批次 L-41 需要隔离、复验、返工还是报废",
     {"lot":"L-41"}),
    ("sales", "订单配置与版本", "订单管理这边，销售订单 SO-22 行 00010，成品 FG-T1，客户定制 BOM，BOM 改了还能下单吗",
     {"sales_order":"SO-22","order_item":"00010","product":"FG-T1","bom_kind":"custom"}),
    ("sales", "交付与客户沟通", "销售角度，订单号 SO-22 行 00010，需求日期 2026/12/9，交付承诺会变吗",
     {"sales_order":"SO-22","order_item":"00010","requested_date":"2026-12-09"}),
    ("service", "已交付产品与备件", "售后视角，设备序列号 SN-701，成品 FG-77，维修备件 MAT-A 哪些受影响",
     {"serial":"SN-701","product":"FG-77","material":"MAT-A"}),
    ("service", "兼容性与旧件保留", "服务这边，存量设备 SN-702 目前装旧件 MAT-A，能否改用新件 MAT-B，旧件要保留维修吗",
     {"serial":"SN-702","old_component":"MAT-A","new_component":"MAT-B"}),
)


class DepartmentIntentTests(unittest.TestCase):
    def test_department_questions(self):
        for role, topic, message, expected in CASES:
            with self.subTest(role=role, message=message):
                answer = collect([message])
                intent = answer["department_intent"]
                self.assertEqual(intent["role"], role)
                self.assertIn(topic, intent["topics"])
                for key, value in expected.items():
                    self.assertEqual(intent["scope"].get(key), value, answer)
                self.assertTrue(intent["evidence"])
                self.assertTrue(intent["decision_owner"])

    def test_numbers_and_dates_do_not_enter_unrelated_workflow_fields(self):
        answer = collect(["采购角度，物料 MAT-A，采购单 PO-55，供应商承诺数量 0 件，承诺到货日 2026/12/3，"
                          "预计什么时候能到货"])
        self.assertEqual(answer["department_intent"]["scope"]["committed_quantity"], "0 件")
        self.assertNotIn("effective_date", answer["fields"])
        self.assertNotIn("purchase_order", answer["fields"])

    def test_planning_scope_transfers_to_existing_filters(self):
        answer = collect(["计划部，P100 工厂，库位 A02，MAT-A 换成 MAT-B，2026/12/1生效，看看新料缺口"])
        self.assertEqual(answer["missing"], [])
        self.assertEqual(answer["fields"]["planning_plant"], "P100")
        self.assertEqual(answer["fields"]["storage_location"], "A02")
        self.assertNotIn("plant", answer["fields"])

    def test_context_only_and_later_confirmed_change(self):
        start = "仓储视角，物料 MAT-A，库位 A01，批次 L-10 待检，什么时候可计入可用量"
        pending = collect([start])
        self.assertEqual(pending["department_intent"]["status"], "evidence_review")
        self.assertFalse(pending["rows"])
        ready = collect([start, "批次改为 L-11，MAT-A 换成 MAT-B，2026/12/1 生效"])
        self.assertEqual(ready["department_intent"]["scope"]["lot"], "L-11")
        self.assertEqual(ready["department_intent"]["status"], "impact_review")
        self.assertEqual(ready["missing"], [])
        self.assertNotIn("lot", ready["fields"])

    def test_no_approval_or_stale_material_from_ambiguous_conversation(self):
        asked = collect(["仓储角度，批次 LOT-5 是否隔离"])
        self.assertNotIn("stock_state", asked["department_intent"]["scope"])
        self.assertFalse(asked["rows"])
        messages = ["采购角度，旧料 MAT-A 缺货，改用新料 MAT-B，2026/12/1 生效",
                    "替代件重新选，改用 MAT-C 或 MAT-D，还没定"]
        updated = collect(messages)
        self.assertIn("new_component", updated["missing"])
        self.assertNotIn("new_component", updated["department_intent"]["scope"])

    def test_explicit_planning_quantities_and_order_line_remain_evidence(self):
        answer = collect(["计划部，采购订单 PO-31/0008，旧料可用库存 0 件、旧料在途 10 件，"
                          "新料未到货采购量 12 件，切换后需求量 20 件"])
        intent = answer["department_intent"]
        self.assertEqual(intent["scope"]["purchase_order"], "PO-31")
        self.assertEqual(intent["scope"]["order_item"], "0008")
        self.assertEqual(intent["quantities"]["old_on_hand"], {"value": 0, "unit": "件"})
        self.assertEqual(intent["quantities"]["new_open_po"], {"value": 12, "unit": "件"})
        self.assertNotIn("old_on_hand", answer["fields"])
        self.assertFalse(answer["rows"])

    def test_numeric_overflow_leaves_quantity_unconfirmed(self):
        answer = collect(["采购角度，未结采购订单 PO-31，供应商已投产 5 件",
                          "供应商已投产 " + "9" * 330 + " 件"])
        intent = answer["department_intent"]
        self.assertNotIn("supplier_produced", intent["scope"])
        self.assertNotIn("supplier_produced", intent["quantities"])

    def test_conflicting_role_asks_whose_view(self):
        answer = collect(["从质量角度，从仓储角度，MAT-A 换成 MAT-B，2026/12/1 生效"])
        self.assertEqual(answer["missing"][0], "role")
