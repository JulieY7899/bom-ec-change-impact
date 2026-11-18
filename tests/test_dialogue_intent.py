"""Acceptance cases for local intent recognition using invented identifiers."""
import unittest

from bom_change_assistant.workflow_dialogue import collect


COMPLETE = dict(role="procurement", change_type="replace", old_component="COMP-001",
                new_component="COMP-998", effective_date="2026-12-01")
SCREENSHOT = "采购角度，COMP-001组件今天到不了货，替换成COMP-998有什么影响"


class DialogueIntentTests(unittest.TestCase):
    def test_roles_and_actions(self):
        for message, expected in (
            (SCREENSHOT + "，12月1日补货，2026/12/1生效", COMPLETE),
            ("研发视角，旧件 MAT-A 换成新件 MAT-B，2026/12/1 生效，看父件影响",
             dict(role="rd", change_type="replace", old_component="MAT-A", new_component="MAT-B")),
            ("我负责销售，想看 MAT-A 组件下架对交付的影响，2026/12/1 生效",
             dict(role="sales", change_type="remove", old_component="MAT-A")),
            ("生产现场这边，在 TOP-11 下加装 MAT-B，2026/12/1 起执行",
             dict(role="manufacturing", change_type="add", target_parent="TOP-11", new_component="MAT-B")),
            ("工程部，成品 FG-T1，版本 ENG-V2，BOM 行 V2-L2，MAT-A 单耗由 2 调到 0，2026/12/1 生效",
             dict(role="engineering", analysis_mode="engineering", product="FG-T1", engineering_version="ENG-V2",
                  target_bom_row="V2-L2", change_type="quantity", old_component="MAT-A", old_quantity=2, new_quantity=0)),
            ("工程视角，MAT-A 的生效日从 2026/11/20 改到 2026/12/1",
             dict(role="engineering", change_type="effectivity", old_component="MAT-A", old_effective_date="2026-11-20")),
            ("采购看一下，采购订单 PO-888，成品 FG-100 下的 COMP-001 这颗料断供，用 COMP-998 顶替，2026/12/1 切换",
             COMPLETE),
            ("销售视角，原料号 MAT-A，新料号 MAT-B，2026/12/1 起换料",
             dict(role="sales", change_type="replace", old_component="MAT-A", new_component="MAT-B")),
            ("工程角度，MAT-A 用量由 2 个改为 1.5 个，2026/12/1 生效",
             dict(role="engineering", change_type="quantity", old_component="MAT-A", old_quantity=2, new_quantity=1.5)),
            ("采购角度，把 MAT-A 的用量从 2 改成 0，2026/12/1 生效",
             dict(role="procurement", change_type="quantity", old_component="MAT-A", old_quantity=2, new_quantity=0)),
            ("研发角度，使用 MAT-B 来替换 MAT-A，2026/12/1 生效",
             dict(role="rd", change_type="replace", old_component="MAT-A", new_component="MAT-B")),
            ("制造视角，MAT-A 组件移出 BOM，2026/12/1 生效",
             dict(role="manufacturing", change_type="remove", old_component="MAT-A")),
        ):
            with self.subTest(message=message):
                result = collect([message])
                self.assertEqual(result["missing"], [], result)
                for name, value in {**expected, "effective_date": "2026-12-01"}.items():
                    self.assertEqual(result["fields"].get(name), value, result)

    def test_followups_only_fill_or_revise_the_addressed_fields(self):
        messages = [SCREENSHOT]
        for tail, fields, missing in (
            ([], {k: v for k, v in COMPLETE.items() if k != "effective_date"}, ["effective_date"]),
            (["2026/12/1"], COMPLETE, []),
            (["2026/12/1", "刚才新料报错了，改成 COMP-997"], {**COMPLETE, "new_component": "COMP-997"}, []),
            (["2026/12/1", "交期调整到 2026/12/9"], COMPLETE, []),
            (["2026/12/1", "换销售视角看看"], {**COMPLETE, "role": "sales"}, []),
            (["2026/12/1", "其实原件是 COMP-011，替代件是 COMP-997"],
             {**COMPLETE, "old_component": "COMP-011", "new_component": "COMP-997"}, []),
        ):
            with self.subTest(tail=tail):
                result = collect([*messages, *tail])
                self.assertEqual(result["fields"], fields)
                self.assertEqual(result["missing"], missing)

    def test_uncertainty_and_negation(self):
        start = SCREENSHOT + "，2026/12/1生效"
        for messages, missing, absent in (
            (["采购角度，COMP-001 与 COMP-002 都缺货，替换成 COMP-998，2026/12/1生效"],
             "old_component", "old_component"),
            ([start, "COMP-006 和 COMP-007 都缺货，改用 COMP-996"], "old_component", "old_component"),
            ([start, "COMP-997 或 COMP-996 都在考虑"], "new_component", "new_component"),
            ([start, "不要换了"], "change_type", "change_type"),
            (["制造角度，不要删除 MAT-A，2026/12/1生效"], "change_type", "change_type"),
            (["采购角度，COMP-001 缺货，改用 COMP-998，预计交期调整到 2026/12/9"], "effective_date", "effective_date"),
            (["销售视角，MAT-A 换成 MAT-B，生效暂定 2026/12/1 或 2026/12/2"], "effective_date", "effective_date"),
        ):
            with self.subTest(messages=messages):
                result = collect(messages)
                self.assertNotIn(absent, result["fields"], result)
                self.assertIn(missing, result["missing"], result)

    def test_explicit_relationship_wins_over_an_unrelated_supply_issue(self):
        message = ("采购角度，采购单 PO-201 中 MAT-C 缺货，"
                   "但本次在 2026/12/1 生效的是用 MAT-B 替代 MAT-A")
        result = collect([message])
        self.assertEqual(result["fields"], {**COMPLETE, "old_component": "MAT-A", "new_component": "MAT-B"})
        self.assertEqual(result["missing"], [])

    def test_date_repeated_while_waiting_for_material_does_not_fill_a_material(self):
        result = collect(["研发角度，拟 2026-12-01 替换组件", "2026-12-02"])
        self.assertNotIn("old_component", result["fields"])
        self.assertEqual(result["missing"], ["old_component", "new_component"])
