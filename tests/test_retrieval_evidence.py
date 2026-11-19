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


class DecisionScopeTests(unittest.TestCase):
    def test_approval_belongs_to_the_requested_object_and_action(self):
        cases = (
            (
                "DE-731 对线体 LN-42 的换型决定何时开线？",
                "放行决议 DE-731。结论：同意换型。适用线体：LN-42。开线时刻：2031-08-21 07:10。",
                (
                    "放行决议 DE-731。结论：同意换型。适用线体：LN-46。开线时刻：2031-08-21 07:10。"
                    "LN-42 仅用于试装对照。",
                    "放行决议 DE-731。结论：同意换型。适用线体：LN-46。开线时刻：2031-08-21 07:10。"
                    "放行决议 DE-732。结论：同意换型。适用线体：LN-42。开线时刻：2031-08-21 07:10。",
                ),
            ),
            (
                "请提供仍有效的 AP-523 批准成品 KT-24 投用的依据。",
                "AP-523 为 KT-24 的现行投用批件，确定 2031-08-21 投用。",
                ("AP-524 为 KT-24 的现行投用批件，确定 2031-08-21 投用。原批件 AP-523 已撤回。",),
            ),
            (
                "查 AP-678 已批准批次 BG-40 批量换产的审批原文。",
                "AP-678 已批准批次 BG-40 批量换产，生效安排另附。",
                ("批件 AP-678 仅批准批次 BG-40 的台架试装。批量换产暂未批准。",),
            ),
            (
                "为东厂 LN-43 更换定位架的 AP-679 有联审结论吗？",
                "设备换型审批 AP-679 经联审通过，变更内容为更换定位架，适用范围限东厂 LN-43。",
                ("设备换型审批 AP-679 经联审通过，变更内容为更换定位架，适用范围限西厂 LN-43。东厂留作储备。",),
            ),
        )
        for query, positive, negatives in cases:
            with self.subTest(query=query):
                qualifies = evidence_requirement(query)
                self.assertTrue(qualifies(positive), positive)
                for negative in negatives:
                    self.assertFalse(qualifies(negative), negative)

    def test_signed_record_and_operative_appendix_each_support_the_relevant_detail(self):
        qualifies = evidence_requirement("AP-680 何时签发？附页给哪条产线以及切换时点？")
        self.assertTrue(qualifies("审批单 AP-680 已于 2031-08-21 签发，切换时点见附页。"))
        self.assertTrue(qualifies("AP-680 适用性附件列出产线 LN-44，切换时点定为 2031-08-22 08:00。"))
        self.assertFalse(qualifies("归档清单记录 AP-680 审批单和适用性附页的接收状态，签署岗位未随包入库。"))


class QuantitativeLimitTests(unittest.TestCase):
    def test_require_formal_limit_in_the_requested_direction_and_condition(self):
        qualifies = evidence_requirement("按规范 QA-615，扣件 XT-42 在 21 ℃ 的正式脱开力下限是多少 N？")
        self.assertTrue(qualifies("检验规范 QA-615 已发布。扣件 XT-42 在 21°C 的最小脱开力为 62 N。"))
        for unsupported in (
            "检验规范 QA-615 已发布。扣件 XT-42 在 21°C 的最大脱开力为 62 N。",
            "检验规范 QA-615 已发布。扣件 XT-42 在 25°C 的最小脱开力为 62 N。",
            "检验规范 QA-615 已发布。扣件 XT-42 在 21°C 的最小脱开力为 62 mN。",
            "试验记录 QA-615 显示扣件 XT-42 在 21°C 实测最小脱开力为 62 N。",
            "检验规范 QA-615 已生效。扣件 XT-45 在 21°C 的最小脱开力为 62 N。",
            "检验规范 QA-615 待评审。扣件 XT-42 在 21°C 的最小脱开力为 62 N。",
        ):
            with self.subTest(unsupported=unsupported):
                self.assertFalse(qualifies(unsupported))

    def test_average_and_individual_limits_are_distinct(self):
        qualifies = evidence_requirement("冷启动平均响应时间的正式上限是多少毫秒？")
        self.assertTrue(qualifies("已发布条款规定冷启动平均响应时间不得超过 95 ms。"))
        self.assertTrue(qualifies("已发布条款规定冷启动平均响应上限为 95 ms。"))
        self.assertFalse(qualifies("已发布条款规定冷启动单次响应时间不得超过 95 ms。"))
        self.assertFalse(qualifies("已发布条款规定暖机后平均响应时间不得超过 95 ms。"))
