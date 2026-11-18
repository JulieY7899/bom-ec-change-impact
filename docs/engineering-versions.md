# 工程版本、BOM 行与订单引用

V1.3-B22 增加工程版本核查。按成品、工厂范围、工程版本和 BOM 行号定位。每个核查项目保留路径和来源，订单引用、行适用性与执行复核状态分别展示。系统输出供工程、计划和采购评审。正式工程结构、订单版本引用和处置状态保持原值。

## 用虚构示例运行

1. 从导入页下载「工程版本与订单引用示例」，导入工作簿。
2. 在变更草案选「按工程版本和 BOM 行核查」，选 `FG-T1 / PLANT-T / ENG-V2` 和 `V2-L2`。
3. 选择替换组件，填入新组件 `PART-NEW` 和生效日 2026-12-01，加入变更后运行。
4. 核对结果仅包含目标行 `V2-L2` 的使用路径和 `SO-22 / 00010` 的版本引用。原 `V2-L1` 仍保留。引用 V1 的订单单独列为排除。
5. 在核查报告页下载 Markdown，核对目标行、日期、订单与来源。

## 导入约定

每个版本工作表提供 `engineering_record` 列。支持下列类型，记录可分到多个工作表或 XLSX 文件，整批完成时验证跨表关联。旧格式仍可用于按物料核查。

| 类型 | 主要字段 |
| --- | --- |
| `VERSION` | `product`、`engineering_version`。可提供 `plant`。工厂范围空白只匹配空白。 |
| `BOM_ROW` | 版本完整键、`row_id`、`parent`、`component`。用量为 `quantity`、`unit`，生效边界为 `valid_from`、`valid_to`。下层行可用 `parent_row_id` 指定上层位置。 |
| `ORDER_REFERENCE` | 版本完整键、`order_kind`、`order_id`。销售引用 `SALES` 需 `order_item`，生产引用 `PRODUCTION` 按单号，独立引用 `ORDER_BOM` 支持编号或编号＋行号。版本号可空以登记待核对象。 |
| `ORDER_ROW` | 版本完整键、订单键、`row_id`、`row_application`。取 `APPLIES`、`EXCLUDED` 或 `UNKNOWN`。 |

在订单引用表可提供 `order_status`、`order_relevant_date`、`purchased`、`received`。状态支持开放、已释放、执行中、完工待交付、已关闭、已取消、已拒绝及未知。采购和到货使用是、否、未知。编号请按文本填写。版本和 BOM 行号保留前导零。证据定位保留来源编号、表序号与行号。

- 日期包含起止当天。目标和上层路径须在拟生效日有效。缺失的起止边界视为开放。重复的上层位置须通过行号指明，歧义、断链和环路留待核查。
- 版本引用与订单行配置均有各自证据。适用性缺失或冲突时，保留确切版本命中并列为待核。其余订单按配置、执行状态和需求或执行日期给出复核清单。
- 已采购、已到货或采购证据矛盾的待核订单由工程、采购、计划共同确认。B19 物料级采购证据有独立口径。
- 已有行支持替换、删除、用量调整。理论差量检查目标行新旧用量及上层总成计数用量，证据不足时提示待核。新增与改变生效日期仍可使用普通核查。

版本目录通过 `GET /api/engineering-versions` 读取。可在现有 `POST /api/analyze` 或 `/api/analyze-batch` 的项目中设 `analysis_mode=engineering`，提交 `product`、`plant`、`engineering_version`、`target_bom_row`、生效日和变更字段。核查标签 `bom_version` 继续承载既有 ECN 汇总。结果中 `engineering_impact` 保存每项命中与来源，并按订单身份去重汇总。

范围为本机样例的版本和引用核查。版本生命周期、MBOM、ERP/PLM 写回及审批执行仍由正式业务流程承担。
