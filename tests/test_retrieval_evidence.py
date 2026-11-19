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
