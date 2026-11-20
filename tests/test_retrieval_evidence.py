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


class ProcedureTextTests(unittest.TestCase):
    def test_require_an_issued_and_applicable_procedure_with_operative_text(self):
        qualifies = evidence_requirement("RL-230 的跨班搬运是否必须使用双层托盘？查规程的正式正文。")
        self.assertTrue(qualifies(
            "物料跨班搬运规程 RL-230 已发布，全厂均适用。领取双层托盘后必须戴一次性手套复核标识，完成后方可交运。"
        ))
        for unsupported in (
            "物料跨班搬运规程拟稿 RL-230，建议用双层托盘。正在征求意见，无生效日期。",
            "一次性作业规程 RL-230 同意本批使用双层托盘。跨班转运完毕须回收托盘。",
            "作业目录挂有跨班搬运规程 RL-230，双层托盘要求正文未附。",
            "常温存放规程 RL-230 已发布，规定在库物料放双层托盘。",
            "跨班搬运规程 RL-235 已发布，必须使用双层托盘。",
        ):
            with self.subTest(unsupported=unsupported):
                self.assertFalse(qualifies(unsupported))


class ApprovalEffectivityTests(unittest.TestCase):
    def test_effectivity_can_follow_the_signed_record_in_a_separate_sentence(self):
        qualifies = evidence_requirement("在 2040 年 5 月 28 日当天仍有效的 PC-690 锁舌更换批准是哪份？")
        self.assertTrue(qualifies("PC-690 的更换批准 BX-427 已签发。有效期为 2040-05-01 至 2040-05-30。"))
        self.assertFalse(qualifies("PC-690 的更换批准 BX-428 已签发。自 2040-06-01 起有效。"))

    def test_withdrawal_is_scoped_to_the_record_object_and_effective_day(self):
        from bom_change_assistant.retrieval_evidence import withdrawn_authorization_filter

        withdraws = withdrawn_authorization_filter("BX-427 仍批准 PC-690 投用吗？")
        self.assertTrue(withdraws("2040-05-10 的撤回通知将 PC-690 从 BX-427 批准范围中删除。"))
        self.assertFalse(withdraws("2040-05-10 的撤回通知将 PC-691 从 BX-427 批准范围中删除。"))
        before = withdrawn_authorization_filter("2040-05-09 BX-427 仍批准 PC-690 投用吗？")
        self.assertFalse(before("2040-05-10 的撤回通知将 PC-690 从 BX-427 批准范围中删除。"))

    def test_approval_effective_date_is_distinct_from_issue_date(self):
        evidence = (
            "放行批件 AX-480 批准滑座 SD-29 在常规线换版。"
            "签发日为2036年8月3日，自2036年8月7日零时生效。"
        )
        on_time = evidence_requirement("2036年8月7日滑座 SD-29 常规生产换版的有效批件 AX-480 在哪？")
        too_early = evidence_requirement("2036年8月6日滑座 SD-29 常规生产换版的有效批件 AX-480 在哪？")
        self.assertTrue(on_time(evidence))
        self.assertFalse(too_early(evidence))
        self.assertFalse(on_time(evidence.replace("SD-29", "SD-30")))

    def test_future_withdrawal_and_partial_withdrawal_are_scoped(self):
        from bom_change_assistant.retrieval_evidence import withdrawn_authorization_filter

        notice = "调整单于2036年8月3日签发，AX-480 的 SD-29 从2036年8月7日零时撤回。"
        before = withdrawn_authorization_filter("2036年8月6日按 AX-480 常规生产 SD-29 的有效批准？")
        after = withdrawn_authorization_filter("2036年8月7日按 AX-480 常规生产 SD-29 的有效批准？")
        self.assertFalse(before(notice))
        self.assertTrue(after(notice))
        adjustment = "二〇三六年8月7日撤回甲组批次放行，乙组批次继续有效。"
        removed = withdrawn_authorization_filter("二〇三六年8月7日甲组批次常规制造的有效放行？")
        retained = withdrawn_authorization_filter("二〇三六年8月7日乙组批次常规制造的有效放行？")
        self.assertTrue(removed(adjustment))
        self.assertFalse(retained(adjustment))

        notice = ("2036年8月7日授权范围调整单撤回滑座 SD-29 批次 LOT-227 的正式放行，"
                  "SD-29 的 LOT-238 继续有效。")
        after_removal = evidence_requirement("2036年8月7日 SD-29 的 LOT-227 范围调整后还能常规制造吗？")
        after_retention = evidence_requirement("2036年8月7日 SD-29 的 LOT-238 范围调整后还能常规制造吗？")
        self.assertFalse(after_removal(notice))
        self.assertTrue(after_retention(notice))

    def test_production_record_is_distinct_from_a_trial(self):
        qualifies = evidence_requirement("BX-428 已批准 PC-691 投入连续流水线量产的记录在哪？")
        self.assertTrue(qualifies("PC-691 的量产放行单 BX-428 已正式签发。"))
        self.assertFalse(qualifies("BX-428 批准 PC-691 在台架完成量产样品试装。"))


class FormalBoundContextTests(unittest.TestCase):
    def test_explicit_leakage_condition_requires_the_upper_limit_at_that_temperature(self):
        qualifies = evidence_requirement("检验件 TK-844 在 13 ℃ 下的泄漏率上限是多少毫升每分钟？")
        self.assertTrue(qualifies("现行验收规范规定 TK-844 在 13 ℃ 时最大允许泄漏率为 0.8 mL/min。"))
        self.assertFalse(qualifies("现行验收规范规定 TK-844 在 30 ℃ 时最大允许泄漏率为 0.8 mL/min。"))
        self.assertFalse(qualifies("现行验收规范规定 TK-844 在 13 ℃ 时最大允许压差为 0.8 kPa。"))
        self.assertFalse(qualifies("TK-844 在 13 ℃ 时实测最大泄漏率为 0.8 mL/min。"))

    def test_both_bounds_accept_independent_operative_clauses(self):
        qualifies = evidence_requirement("LC-318 冷启动单次响应的上下限各是多少？")
        self.assertTrue(qualifies("现行条款 LC-318 规定冷启动单次响应不得低于 45 ms。"))
        self.assertTrue(qualifies("LC-318 另列冷启动单次响应的上限：每次须在 180 ms 内响应。"))
        self.assertFalse(qualifies("LC-318 另列暖机后单次响应的上限：每次须在 180 ms 内响应。"))
        self.assertFalse(qualifies("LC-318 另列冷启动单次响应的上限：每次不得低于 180 ms。"))


class ProcedureApplicabilityTests(unittest.TestCase):
    def test_numbered_continuation_and_controlled_steps_are_evidence(self):
        qualifies = evidence_requirement("GH-618 的规程正文，换卷结束后要谁拔出定位叉？")
        self.assertTrue(qualifies("GH-618 的步骤续条规定，换卷结束后先由领班拔出定位叉。"))
        self.assertFalse(qualifies("其他规程 GH-619 规定，换卷后由维护岗拔出定位叉。"))
        self.assertFalse(qualifies("已发布的换卷规程 GH-618 规定，换卷时先检查定位叉方向。"))
        controlled = evidence_requirement("JR-311 正式指导书正文规定把套帽交给哪个岗位？")
        self.assertTrue(controlled("套帽回运指导书 JR-311 的受控版本覆盖全部装配线。拆下后放入回运盒，再交仓管岗。"))

    def test_procedure_requires_the_requested_override_and_general_scope(self):
        qualifies = evidence_requirement("找允许换卷后操作岗替代复核岗复查定位叉的通用规程。")
        self.assertFalse(qualifies("已发布的换卷规程规定，换卷结束后必须由复核岗检查定位叉。"))
        self.assertFalse(qualifies("换卷规程例外：单次处置允许某批操作岗替代复核岗检查定位叉。"))
        self.assertFalse(qualifies("现行换卷规程禁止操作岗替代复核岗检查定位叉。"))
        self.assertTrue(qualifies("现行换卷规程允许各生产班操作岗替代复核岗检查定位叉。"))
