# 工程组在「03 影响核查」使用本机语义检索

以工程组账号登录并切换到工程角色，完成一次工程核查。在「03 影响核查」展开工程知识库，选择分区，上传 PPT、PPTX、TXT 或 Markdown，预览并勾选已脱敏的段落或页，再确认入库。检索支持指定分区和全部分区，最多返回三个片段，并标明材料、分区和来源位置。下载报告沿用确定性核查结果。

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

明确输入可识别的完整料号时优先使用精确词项检索；相似编号无法代替目标编号。中文问题用 BGE-small-zh-v1.5 量化模型检索分区内的余弦近邻，同时保留原有词项匹配。引用表示相似材料，工程人员仍须核对适用条件。

```sh
BOM_TEST_MODEL=../private/model .venv/bin/python scripts/verify.py
```

[Qdrant 官方客户端 Skill](https://github.com/qdrant/skills/tree/main/skills/qdrant-clients-sdk) · [Qdrant Python 本地模式](https://github.com/qdrant/qdrant-client#local-mode) · [原始中文模型](https://huggingface.co/BAAI/bge-small-zh-v1.5) · [固定版本 ONNX 文件](https://huggingface.co/Xenova/bge-small-zh-v1.5/tree/75c43b069aac4d136ba6bc1122f995fedcfd2781)
