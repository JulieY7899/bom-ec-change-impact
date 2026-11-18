# 业务规则与实现边界

[返回项目首页](../README.md)

本页区分两类内容：当前代码可以验证的行为，以及用于指导后续实现的业务设计原则。规则适用于本地原型的导入快照；真实工程变更仍需业务人员确认。

## 1. 当前输入与输出

输入包括多层 BOM、销售及生产订单、变更记录，以及可选的物料、寻源、计划备料与工序资料。系统在导入快照中核查拟议变更，输出候选影响、来源证据和待确认项。

“找到关联”表示对象进入核查范围，不表示该订单必须修改，也不证明物料已经采购、到货或投入生产。

| 对象 | 原型中的含义 |
| --- | --- |
| 导入快照 | 当前本地数据库中的一组导入数据。工程版本模块提供结构与引用快照，完整版本生命周期仍由业务系统管理。 |
| 来源编号与来源行 | 证据定位信息，帮助回查导入文件中的相应记录。 |
| `bom_version` 标签 | 变更记录与汇总使用的标签；不代表已验证的独立 EBOM 版本及订单引用关系。 |
| 核查批次 `draft_id` | 一次变更核查的分组标识，用于隔离结果汇总。 |
| 处置记录 | 用户提供的处置代码、范围、责任人与状态；保存记录不等于执行审批。 |

## 2. 已实现规则

| 规则 | 当前行为 | 主要代码与验证位置 |
| --- | --- | --- |
| R1 双向追溯 | 对拟议变更同时执行向上、向下追溯，并保留路径证据。 | [impact_engine.py](../src/bom_change_assistant/impact_engine.py)、[test_impact_engine.py](../tests/test_impact_engine.py) |
| R2 日期筛选 | 使用导入行的有效起止日期筛选关系；无效日期、循环、截断等情况保留提示。 | [impact_engine.py](../src/bom_change_assistant/impact_engine.py) |
| R3 销售订单范围 | 按涉及成品关联销售订单行，排除已关闭、已取消、已拒绝的状态；未知状态保留待确认。 | [impact_engine.py](../src/bom_change_assistant/impact_engine.py) |
| R4 生产订单范围 | 按涉及成品或准确的销售订单行匹配；制造范围包含已释放／执行中，以及订单总量已知且交付尚未完成或交付量待确认的已完成记录。相同销售订单抬头号不单独构成匹配依据。 | [test_safety_business_fixes.py](../tests/test_safety_business_fixes.py)、[test_impact_engine.py](../tests/test_impact_engine.py)、[test_quantity_completeness.py](../tests/test_quantity_completeness.py) |
| R5 用量差量 | 当证据完整且旧用量一致时，以变更行差量乘上层路径用量，计算单位成品理论差量。 | [impact_checks.py](../src/bom_change_assistant/impact_checks.py) |
| R6 用量不完整 | 存在替代组、多个未明确版本的来源、路径缺失、旧用量不符或数值溢出时，保留人工复核；不把部分路径和作为完整合计。 | [test_privacy_integration.py](../tests/test_privacy_integration.py)、[test_safety_business_fixes.py](../tests/test_safety_business_fixes.py) |
| R7 日期前后比较 | 对同一快照分别按旧、新日期筛选有效路径，比较新增、移除与保持的路径；路径身份保留来源证据。 | [impact_checks.py](../src/bom_change_assistant/impact_checks.py)、[test_privacy_integration.py](../tests/test_privacy_integration.py) |
| R8 计划切换建议 | Run-in / Hard Cut 仅用于组件替换。缺失或不完整的供应情景保留待补数据；非替换变更返回不适用。 | [impact_engine.py](../src/bom_change_assistant/impact_engine.py)、[test_safety_business_fixes.py](../tests/test_safety_business_fixes.py) |
| R9 核查批次隔离 | 汇总以 `bom_version` 与 `draft_id` 共同筛选，避免把历史批次分析混入本次结果。 | [repository.py](../src/bom_change_assistant/repository.py)、[test_safety_business_fixes.py](../tests/test_safety_business_fixes.py) |
| R10 外部模型关闭 | 模型解释函数直接返回禁用状态；HTTP AI 接口拒绝调用，设置密钥不会启用。 | [ai.py](../src/bom_change_assistant/ai.py)、[test_privacy_integration.py](../tests/test_privacy_integration.py) |
| R11 未知数量保留未知 | 派生量所需数据缺失、或合计中包含未知量时，保留待补数据；真实零仍为零。已完成但交付量未知的对象继续保留人工核查。 | [test_quantity_completeness.py](../tests/test_quantity_completeness.py) |

表中的测试链接说明验证位置。测试实际通过情况，以[验证记录](evaluation.md)为准。

## 3. 理论用量如何计算

假设每件成品需要 3 件组件总成，该总成中的某零件用量从 2 改为 5，则这条路径的单位成品理论差量为：

`(5 - 2) × 3 = 9`

只有当所有相关路径都具备足够证据时，才输出成品层合计。结果尚未考虑订单配置、独立版本适配、订单需求数量、库存扣减或已执行采购；不能直接作为采购、生产下达数量。

完整性处理是有意采取的保守行为。某些路径能够计算，另一些路径仍有不确定性时，保留待确认事项比输出看似完整的总量更符合这个原型的职责。

## 4. 后续版本设计原则

以下原则来自项目作者的业务方案，当前仓库尚未完整实现对应的数据模型与执行流程。

| 设计原则 | 预期业务行为 | 当前缺口 |
| --- | --- | --- |
| 工程定义、订单配置、工厂执行分层 | EBOM 管工程产品定义；Order BOM 保存订单配置与差异；MBOM 处理工厂本地化及替代料适配。 | 缺少三个层次的独立模型与关联。 |
| 订单引用指定工程版本 | Order BOM 引用指定 EBOM 版本；订单配置差异原则上不反向修改 EBOM。 | 已支持明确的版本引用和指定行适用性，完整订单配置模型仍待实现。 |
| 新版本发布后按条件决定迁移 | 根据 EC 生效条件、订单状态与影响分析决定是否迁移。 | 日期过滤尚不能表示完整 EC 生效性或版本迁移。 |
| 已有采购或到货时共同确认 | 先识别变更影响与已有承诺，再由工程、采购、计划确认处置。 | 没有真实采购／到货集成，也没有跨部门审批执行。 |
| 规则识别、人工决策 | 系统列出受影响对象与证据，相关人员决定实际行动。 | 当前通过本地报告支持复核；企业权限和正式审批待设计。 |

## 5. 演示数据的含义

内置演示数据全部虚构，用于验证结构追溯、订单关联和证据呈现。示例中的数量、日期、状态与主数据证据不代表任何企业的真实业务情况。

演示输入及输出见 [examples](../examples/README.md)。没有在此原型中验证的指标，包括真实节省工时、采购成本降低、生产质量改善和持续用户采纳。

## 6. V1.1 成品进度数量

未完工量按订单量减确认产量；待交付量按订单量减交付量，两者分别下限为零。它们使用相同的需关注订单范围，在批次中对同一生产订单去重，不能相加或作为组件净需求。缺少一个指标的必要数据时，不影响另一个指标的独立计算。页面、Markdown 报告和 API 均分开表达。


## B19：采购承诺与到货证据（V1.2-B19）

支持采购行快照、收货、退货及对原凭证的全额冲销；按直接物料、工厂、库位、单位和截至日核查与分组。缺值、覆盖不足、冲突和汇总/明细重叠不能成为可信完整总量。采购累计净到货不视为可用库存，剩余承诺不自动加入原备料公式；工程、采购、计划共同确认，approved_action=null。原 B19 汇总50/20/30继续支持且明确明细缺口。字段、枚举、示例与完整规则见[采购与到货](procurement.md)。B22 的明确版本引用和行核查见[工程版本规则](engineering-versions.md)。
