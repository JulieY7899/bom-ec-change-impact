import unittest

from bom_change_assistant.retrieval_evidence import evidence_requirement


class FormalEvidenceTests(unittest.TestCase):
    def test_explicit_formal_questions_require_applicable_issued_records(self):
        cases = (
            (
                "切换审批单 CHG-618 批准成品 KIT-28 几点切换？",
                "切换审批单 CHG-618 已签发，批准对象是成品 KIT-28，切换时间为 2031 年 8 月 9 日 09:30。",
                (
                    "切换审批单 CHG-618 已签发，批准对象是成品 KIT-24，切换时间为 2031 年 8 月 9 日。KIT-28 属于调研范围。",
                    "切换审批单 CHG-618 已签发，批准对象是成品 KIT-24，对照产品 KIT-28。切换时间为 2031 年 8 月 9 日。",
                    "成品 KIT-28 切换已批准，审批单 CHG-618。另一成品切换时间为 2031 年 8 月 9 日。",
                ),
            ),
            (
                "气密记录 QA-52 的泄漏率上限是多少？",
                "QA-52 气密检验的泄漏率不得超过 0.4 mL/min。",
                ("QA-52 的压降实测 4 kPa，泄漏率上限留空。",
                 "QA-52 的最大允许泄漏率为 0.4 mL/min，待批准。"),
            ),
            (
                "官方双签规程正文在哪，清场谁签？",
                "清场规程 SOP-57 要求质量员和线长双签后才可换料。",
                ("这次清场由线长签字，随后按临时计划换料。",),
            ),
            (
                "LOT-42 有效新合格证编号是多少？",
                "批次 LOT-42 已签发合格证 QC-526，有效期至 2031 年 10 月 9 日。",
                ("批次 LOT-42 的合格证 QC-531 已作废，新证待发。",),
            ),
            (
                "请查工单 JOB-732 的客户放行单编号。",
                "返工工单 JOB-732 已签发客户放行单 REL-432，允许成品发给客户。",
                ("工单 JOB-732 授权内部复核，客户放行单未签批。",),
            ),
            (
                "查复验 QA-87 批准量产的签发单号。",
                "QA-87 已签发量产放行单 MP-714，该材料允许转产。",
                ("QA-87 复验完成，量产放行单 MP-718 尚未签发。",),
            ),
        )
        for query, positive, negatives in cases:
            with self.subTest(query=query):
                qualifies = evidence_requirement(query)
                self.assertIsNotNone(qualifies)
                self.assertTrue(qualifies(positive), positive)
                for negative in negatives:
                    self.assertFalse(qualifies(negative), negative)


class ContextEvidenceTests(unittest.TestCase):
    def test_explicit_context_questions_require_matching_dates_balances_and_clauses(self):
        cases = (
            (
                "找 ST-27 在 2031 年 8 月 11 日当天有效的装配记录。",
                "装配位号 ST-27 的旧行在 2031 年 8 月 7 日至 2031 年 8 月 11 日有效。",
                (
                    "装配位号 ST-27 的旧行在 2031 年 8 月 7 日至 2031 年 8 月 10 日有效。",
                    "装配位号 ST-29 的新行从 2031-08-10 开始有效，ST-27 状态待核。",
                    "装配位号 ST-27 从 2031-08-30 开始有效。",
                    "装配位号 ST-27 从 2031-08-07 开始有效，到 2031-08-10 截止。",
                ),
            ),
            (
                "有未到货余额核实为零件的采购证据吗？",
                "采购行 PO-300 核对收货凭证后，未到货余量已确认是 0 件。",
                ("本次尚未匹配采购依据。", "净收 0 件，未到货 40 件。",
                 "示例中未到货余量为 0 件。", "未到货余量为 10 件。"),
            ),
            (
                "解除采购承诺要付多少违约金？要合同条款号和金额。",
                "合同第 8 条明确解除采购承诺的违约金为 300 元。",
                (
                    "合同第 8 条登记采购总额 300 元，解除是否赔偿待批准。",
                    "采购承诺拟解除。合同第 8 条未找到，合同附件待补。",
                    "解除采购承诺违约金为 300 元。另一个采购合同第 8 条留存了记录。",
                ),
            ),
            (
                "采购缺了哪些凭证，要找谁补核？",
                "采购核查补数要求采购订单行和到货凭证，并由采购与仓库分工提供。",
                ("工单实收字段用于制造核查。", "采购行的到货凭证已入库，另有冲销记录。"),
            ),
        )
        for query, positive, negatives in cases:
            with self.subTest(query=query):
                qualifies = evidence_requirement(query)
                self.assertIsNotNone(qualifies)
                self.assertTrue(qualifies(positive), positive)
                for negative in negatives:
                    self.assertFalse(qualifies(negative), negative)
