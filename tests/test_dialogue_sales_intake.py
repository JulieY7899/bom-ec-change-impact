"""Business scope and follow-up tests with synthetic order and material IDs."""
import unittest

from bom_change_assistant.workflow_dialogue import collect


ORDER_QUESTION = "销售订单 SO-9001 里有成品 BOM 变更，这单还能下吗？"


class SalesIntakeTests(unittest.TestCase):
    def test_screenshot_style_question_gathers_only_missing_context(self):
        messages = [ORDER_QUESTION]
        for followup, next_field, prompt in (
            (None, "sales_product", "准备下单的成品物料号是什么"),
            ("2026-12-01", "sales_product", "准备下单的成品物料号是什么"),
            ("FG-810", "bom_kind", "标准 BOM，还是客户定制 BOM"),
            ("不确定", "bom_kind", "核对销售订单行的产品配置"),
            ("是", "bom_kind", "核对销售订单行的产品配置"),
            ("客户专用 BOM", "engineering_version", "实际引用的工程版本"),
            ("订单 BOM 版本 OB-22", "engineering_version", "实际引用的工程版本"),
            ("不确定", "engineering_version", "实际引用的工程版本"),
            ("E-V2", "target_bom_row", "目标 BOM 行号"),
        ):
            if followup:
                messages.append(followup)
            with self.subTest(followup=followup):
                result = collect(messages)
                self.assertEqual(result["intake"]["order_id"], "SO-9001")
                self.assertEqual(result["intake"]["status"], "needs_context")
                self.assertEqual(result["missing"][0], next_field)
                self.assertEqual(result["rows"], [])
                self.assertIn(prompt, result["reply"])
        result = collect([*messages, "BOM 行 L-8，原件 OLD-2 换成 NEW-2，2026/12/1 生效"])
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["fields"], dict(role="sales", analysis_mode="engineering", product="FG-810",
                                               engineering_version="E-V2", target_bom_row="L-8", change_type="replace",
                                               old_component="OLD-2", new_component="NEW-2", effective_date="2026-12-01"))
        self.assertIn("下单决定由销售订单流程确认", result["reply"])

    def test_same_turn_and_spoken_standard_sales_questions(self):
        for message in (
            "从销售看，订单号 SO-9001，成品料号 FG-810，标准 BOM 换料了，是否可以接单？",
            "销售这边，下单成品 FG-810 是非定制 BOM，BOM 里有变更，接单行不行？",
            "SO-9001，成品 FG-810 用通用 BOM，工程变更后这单能不能下？",
            "从销售看，成品料号 FG-810 用非客户定制 BOM，BOM 改了，能否下单？",
        ):
            with self.subTest(message=message):
                result = collect([message, "原件 OLD-2 换成 NEW-2，2026/12/1 生效"])
                self.assertEqual(result["intake"]["status"], "impact_review")
                self.assertEqual(result["intake"]["product"], "FG-810")
                self.assertEqual(result["intake"]["bom_kind"], "standard")
                self.assertEqual(result["missing"], [])
                self.assertEqual(result["fields"]["analysis_mode"], "material")
                self.assertNotIn("order_id", result["fields"])

    def test_routes_credit_order_questions_away_from_the_form(self):
        for messages, status in (
            (["销售这边，客户信用冻结，这张订单能录单吗？"], "outside_scope"),
            (["销售视角，原报价过期，能不能先开单？"], "outside_scope"),
            (["销售角度，SO-9001 能下单吗？"], "needs_context"),
            (["销售角度，SO-9001 能下单吗？", "只是价格问题"], "outside_scope"),
            ([ORDER_QUESTION, "不存在 BOM 变更，是信用冻结"], "outside_scope"),
        ):
            with self.subTest(messages=messages):
                result = collect(messages)
                self.assertEqual(result["intake"]["status"], status)
                self.assertFalse(result["fields"])
                self.assertFalse(result["rows"])
                self.assertNotIn("你打算替换", result["reply"])

    def test_revised_order_or_product_clears_previous_version(self):
        messages = [ORDER_QUESTION, "成品 FG-810，客户定制 BOM，订单引用的工程版本 E-V2，BOM 行 L-8"]
        for reply, next_item in (
            ("成品料号改成 FG-910", "engineering_version"),
            ("订单号 SO-9002", "sales_product"),
        ):
            with self.subTest(reply=reply):
                result = collect([*messages, reply])
                self.assertEqual(result["intake"]["status"], "needs_context")
                self.assertEqual(result["missing"][0], next_item)
                self.assertFalse(result["rows"])

    def test_question_about_customization_is_not_confirmation(self):
        result = collect(["销售订单 SO-9001，成品 FG-810 的 BOM 变了，"
                          "还在确认是否客户定制，这单还能下吗？"])
        self.assertEqual(result["missing"], ["bom_kind"])
        self.assertEqual(result["intake"]["bom_kind"], "unknown")

    def test_multiple_products_need_confirmation(self):
        result = collect([ORDER_QUESTION, "订单有成品 FG-810、FG-820，客户定制 BOM"])
        self.assertEqual(result["missing"], ["sales_product", "engineering_version", "target_bom_row"])
        self.assertFalse(result["rows"])
        answered = collect([ORDER_QUESTION, "订单有成品 FG-810、FG-820，客户定制 BOM", "FG-820"])
        self.assertEqual(answered["intake"]["product"], "FG-820")
        self.assertEqual(answered["missing"][0], "engineering_version")

    def test_manufacturing_and_engineering_ask_for_the_actual_scope(self):
        manufacturing = collect(["从生产现场看，先领了 MAT-101，想换成 MAT-102"])
        self.assertEqual(manufacturing["missing"][0], "old_component")
        self.assertEqual(manufacturing["fields"]["new_component"], "MAT-102")
        engineering = collect(["工程部，成品 END-101，工程版本 E-V2，MAT-101 删除，2026-12-03 生效"])
        self.assertEqual(engineering["missing"], ["target_bom_row"])
        self.assertEqual(engineering["fields"]["old_component"], "MAT-101")

    def test_impact_questions_avoid_order_decision_intake(self):
        phrases = (
            ("销售视角，OLD-2 换成 NEW-2，2026/12/1 生效，影响哪些销售订单行的交期？", "sales", "replace"),
            ("研发视角，原件 OLD-2 换成 NEW-2，2026/12/1 生效，看上层父件", "rd", "replace"),
            ("工单已领料，制造这边想看 OLD-2 移出 BOM 会不会影响在制，2026/12/1 生效", "manufacturing", "remove"),
            ("工程部，成品 FG-810，工程版本 E-V2，BOM 行 L-8，OLD-2 的用量从 2 改成 0，2026/12/1 生效", "engineering", "quantity"),
            ("采购角度，供方说 OLD-2 这颗料延迟到货，改用 NEW-2，2026/12/1 生效", "procurement", "replace"),
        )
        for utterance, role, action in phrases:
            with self.subTest(role=role):
                result = collect([utterance])
                self.assertNotIn("intake", result)
                self.assertFalse(result["missing"], result)
                self.assertEqual(result["fields"]["role"], role)
                self.assertEqual(result["fields"]["change_type"], action)
                self.assertEqual(result["fields"]["old_component"], "OLD-2")
