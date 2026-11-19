# 工程组在「03 影响核查」使用本机语义检索

以工程组账号登录并切换到工程角色，完成一次工程核查。在「03 影响核查」展开工程知识库，选择分区，上传 PPT、PPTX、TXT 或 Markdown，预览并勾选已脱敏的段落或页，再确认入库。检索支持指定分区和全部分区，最多返回三个片段，并标明材料、分区和来源位置。工程人员可把适用片段作为规则结果的补充依据，生成待复核推论，并从「04」下载包含该推论的工程报告。

## 在独立副本启动

在独立程序目录中使用 Python 3.12 创建虚拟环境。以下命令在项目根目录运行。先在受保护的私有目录配置工程账号，随后启动服务并完成一次工程核查。

```sh
umask 077
python3.12 -m venv .venv
.venv/bin/python -m pip install '.[engineering-search]'
.venv/bin/python scripts/prepare_engineering_model.py ../private/model
.venv/bin/python scripts/set_account.py --accounts ../private/accounts.sqlite3 eng01 engineering
.venv/bin/python app.py --host 127.0.0.1 --port 18778 \
  --database ../private/bom.sqlite3 --accounts ../private/accounts.sqlite3 \
  --knowledge-model ../private/model
```

旧版 .ppt 通过本机 LibreOffice 的 soffice 临时转换，支持显式指定 `--legacy-ppt-converter /path/to/soffice`。常规安装可自动识别。转换进程使用独立临时目录，预览完成后清理。PPTX 和 Markdown 直接提取本地文本。

只有准备模型需要下载公开权重和分词器。启动时校验固定版本五个文件的 SHA-256，第一次入库或语义查询时按需加载量化模型。单线程、每批两个片段生成向量，禁用 ONNX Runtime 的 CPU 内存池。启用成功后，工程知识库的材料统计旁显示「Qdrant 本机中文语义检索」。省略模型参数时沿用既有本机词项检索。

## 存储与匹配

已审核的正文及页码保存在业务库旁单独的工程摘录 SQLite 文件里。Qdrant Python SDK 的磁盘本地模式将 512 维向量和分区标记写入业务库旁的 `.qdrant/` 目录。它在应用进程内运行。每次导入以及语义检索时按审核内容校对向量集合，复用未变的片段，补建缺失片段并移除失效向量。旧版审核库可以按需建立向量，移区或重新审核后引用当前文本。备份和迁移时应把摘录库同业务快照配套保存。

手动检索包含可识别编号时，默认词项和语义模式都只返回至少精确命中一个查询编号的片段。查询中的工厂、版本和 BOM 行等明确条件也会参与证据检查。手动提出不带编号的常见业务问题时，检索可补入对应的工程词项，例如装配间隙、累计净到货量、在制工单或销售订单行配置。启用 BGE-small-zh-v1.5 量化模型时，这类扩展问题优先展示两种检索都命中的片段。补入的词只用于查找和排序，预览、引用和报告使用已审核原文。留空查询框时按核查上下文自动检索。工程人员须核对候选片段的适用范围，再选入报告。

显式 BGE 查询已有高分命中时，额外候选需有接近首选的语义分数或较强的独立词项支持，因此可少于三个返回。仅凭语义命中的最高分片段仍可作为候选。明确询问以重力加速度倍数表达的振动上限时，两种模式均要求在同一句找到相应指标和带单位的判定值。

两种模式都会检查显式提问所需的证据。在途量与未到货量去重需在所选分区找到同时覆盖两种数量及防重方法的材料。指定发票抵减需找到发票编号、适用的采购余额及实际抵减金额。已批准的切换日期和签字需找到明确的批准状态、切换日期，以及已填写的签字人或可定位的签字记录。证据来自当前审核片段，工程人员选用时仍需核对来源和适用范围。

关于旧件用完再换的问题，可补入旧料消耗和切换条件相关词项。对于明确询问的开放订单评审、缺失版本补证、逐行选配、适用性冲突、生效窗口、早于生效日的需求、库存属性计入等问题，检索按所问条件筛查候选。复合问题的不同订单和日期条件可由不同适用片段分别回答。

```sh
BOM_TEST_MODEL=../private/model .venv/bin/python scripts/verify.py
```

[Qdrant 官方客户端 Skill](https://github.com/qdrant/skills/tree/main/skills/qdrant-clients-sdk) · [Qdrant Python 本地模式](https://github.com/qdrant/qdrant-client#local-mode) · [原始中文模型](https://huggingface.co/BAAI/bge-small-zh-v1.5) · [固定版本 ONNX 文件](https://huggingface.co/Xenova/bge-small-zh-v1.5/tree/75c43b069aac4d136ba6bc1122f995fedcfd2781)
