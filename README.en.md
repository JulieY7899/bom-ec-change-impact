# BOM Engineering Change Impact

[![Verify synthetic examples](https://github.com/JulieY7899/bom-ec-change-impact/actions/workflows/verify.yml/badge.svg?branch=main)](https://github.com/JulieY7899/bom-ec-change-impact/actions/workflows/verify.yml)

**V1.3-B22 (0.4.0)** checks proposed changes against a specific engineering revision and BOM position within a product and plant scope. Explicit order references establish revision matches. Configuration, order status and timing evidence determine which cases require review. The browser and Markdown download use the same findings. [Engineering revision fields and example](docs/engineering-versions.md)

V1.2-B19 procurement commitments, receipts, returns and reversals remain available. [Procurement import guide](docs/procurement.md)

In **03 Impact Review**, an engineering-department account using the Engineering role can open a completed engineering review and search the local library. Choose a redacted PPT, PPTX, Markdown or TXT file, review the excerpts, select a section and index the approved text. Enable Qdrant Local for on-device Chinese semantic search. Selected excerpts supplement the rule results with engineering checks for human review. In **04 Review Report**, only signed-in engineering-department accounts using the Engineering role can view or download the report with that assessment. The shared report contains the original rule results. [Impact Review guide (Chinese)](docs/workflow.md#03-影响核查中的工程辅助分析) · [Review Report guide (Chinese)](docs/workflow.md#04-核查报告) · [Local semantic search setup (Chinese)](docs/engineering-semantic.md)

Nine review roles cover engineering, R&D, purchasing, material planning, warehouse and logistics, manufacturing, quality, sales, and service. Purchasing and material planning share order and receipt evidence. Each keeps its own review decisions. [Department roles and acceptance criteria (Chinese)](docs/department-roles.md)


**Local decision support for engineering change reviews**

[中文](README.md) | English

When a component changes, which products and orders need review? What evidence supports the result, and what still needs confirmation by engineering, procurement and planning?

This prototype uses imported multilevel BOMs, orders and proposed changes to trace dependencies, identify candidate impacts and produce a report with source evidence and open questions. Rules and graph traversal perform the analysis.

**Status: independent prototype demonstrated with synthetic data. External AI calls are disabled in this build.** The application does not approve changes or write to ERP, PLM or MES.

![Result diagram generated from the actual output of a synthetic case](docs/assets/demo-overview.svg)

*This diagram is generated from the saved analysis output; it is not a UI screenshot.*

[Demo case](examples/README.md) · [Historical verification](docs/evaluation.md) · [Business rules](docs/business-rules.md) · [Design decisions](docs/decisions.md)

## A change from input to evidence

The built-in dataset uses `COMP-001` in two products, `FG-A` and `FG-B`. The example proposes replacing it with `COMP-NEW`, effective `2026-08-01`.

| Review question | Example output |
| --- | --- |
| Where is the component used? | Multilevel paths to both products, with source rows. |
| Which orders require review? | Two sales order records and one production order record within the current manufacturing scope. These are candidate impacts. |
| Is the new component ready? | Checks against imported master-data and sourcing evidence, with outstanding evidence requests. |
| Can material usage switch immediately? | Missing supply data remains an explicit gap. The tool does not issue purchasing or production instructions. |
| Can the result be checked? | Paths, order evidence, source IDs and worksheet rows allow review of the supporting inputs. |

These observations describe the repository's synthetic example. They are not production inventory, procurement commitments or measured business benefits. See the [demo case](examples/README.md) for the inputs and reproduction steps.

## What the prototype implements

| Capability | Current scope |
| --- | --- |
| Excel import and BOM traversal | Bidirectional traversal within the current imported snapshot, retaining source evidence. |
| Order association | Candidate selection using materials, sales order items and status. A shared sales order header alone no longer includes unrelated production orders. |
| Quantity changes | Theoretical component delta per finished-product unit. Ambiguous alternatives or incomplete usage evidence require review. |
| Effectivity changes | Comparison of date-filtered valid paths within one snapshot. |
| Planning and manufacturing review | Rule-based prompts, data gaps and recorded manual dispositions, subject to business confirmation. |
| Batch summaries and reports | Results grouped within the current review batch, with local evidence reports. |
| EBOM revisions and Order BOM migration | Business principles documented; the full revision model and migration execution are not implemented. |
| Enterprise integration, approval and write-back | Not implemented. |
| Model-generated explanation | Disabled in this build; no model-performance claims. |

## Design rationale

The application separates calculated candidate impacts from business decisions. It preserves open questions when configuration, revision or execution-state evidence is missing. Reports support a subsequent cross-functional review outside the application.

The key decisions are documented in [design decisions](docs/decisions.md), including deterministic calculations, conservative handling of incomplete inputs and the distinction between date filtering and revision migration.

## Author and implementation

**[JulieY7899](https://github.com/JulieY7899)** defines the business problem and modeling principles, drawing on manufacturing and supply chain experience. The design focuses on the responsibilities of engineering definitions, order configurations and factory execution, with cross-functional confirmation for engineering changes.

Code, tests and documentation are developed with AI assistance. Implemented features, business design principles and future work are identified separately. This repository's demonstrations and test results do not establish enterprise deployment or production benefits.

## Run locally

Requires **Python 3.11 or later**. The core application uses the Python standard library and has no third-party runtime dependencies. From the project root:

```bash
python3 app.py
```

Direct execution opens `http://127.0.0.1:8765` by default. On macOS, open `启动BOM助手.command` in Finder; the launcher selects an available port and saves the actual address in local `data/server.url`. This build fixes address-file creation on Mac. See the [verification record](docs/evaluation.md).

Use a separate project directory for your first demo, then select “载入虚拟演示数据” in the Chinese-language UI. **Loading demo data replaces the selected database's current imported snapshot and analysis results.** To use a separate database:

```bash
python3 app.py --database data/portfolio-demo.sqlite3
```

An empty Excel import template is available in the application. Use synthetic data for public demonstrations and repository submissions. Start the app with `--accounts <local-account-database>` to enable sign-in. [Independent demo setup (Chinese)](docs/workflow.md#本机配置示例)

## Verify

From the project root:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 scripts/run_example.py --check
node --check src/bom_change_assistant/web_assets/app.js
node scripts/check_frontend_metrics.cjs
python3 scripts/check_public_files.py
```

CI runs on Linux with Python 3.11 / 3.12 / 3.14 and macOS with Python 3.14. The badge links to the actual GitHub Actions results.

Coverage includes procurement reconciliation, multilevel traversal, order association, effectivity boundaries, quantity completeness, review-batch isolation, reports and local HTTP behavior. See [GitHub Actions](https://github.com/JulieY7899/bom-ec-change-impact/actions/workflows/verify.yml) for current run results.

## Recent fixes

- **Production order status.** The importer matches complete SAP status codes. `PCNF` and `REL PCNF` count as in progress for impact and risk review, even when the order quantity is missing.
- **Local import reliability.** Request bodies are read outside the shared lock, with length validation and read timeouts. The active snapshot remains available during an upload, and failed or late uploads cannot replace it.
- **Snapshot backups.** After a successful import, each database keeps its three latest managed backups. Existing legacy backups are preserved.
- **Analysis performance.** The analysis loads BOM edges reachable from the selected material. Each batch shares one set of procurement evidence, and a single-item group reuses the item's procurement analysis.

**Earlier snapshots require reimport.** They contain the classified status only. To correct PCNF misclassification, reimport the complete batch from the original Excel exports, then rerun the analysis and generate a new report.

### PCNF reimport verification

- **Anonymized sample.** All four production orders containing `PCNF` also carried `DLV`, and each delivered quantity equaled the order quantity. On reimport, all four were classified as completed and excluded from the in-progress count.
- **Controlled comparison.** Two test orders were added within the same BOM review scope, one with `PCNF` alone and one with `REL PCNF` alone. Both were classified as released/in progress, including the order with a blank quantity. The linked production order count and the in-progress count in the risk rationale both rose from 0 to 2. The web results agreed with both Markdown downloads.

## Data handling and limitations

- External model calls are blocked, including when `OPENAI_API_KEY` is present. The server is restricted to loopback addresses and checks Host and Origin headers.
- Local SQLite storage retains some business identifiers and is not encrypted at rest by the application. Databases, operational reports, logs and credentials are outside the public portfolio.
- An imported snapshot is not an independent EBOM revision. Date comparisons do not determine Order BOM migration, net material requirements or inventory consumption.
- Multi-file imports are staged as a batch. A successful batch backs up the previous snapshot before activation. A failed or canceled import leaves the active snapshot and saved analyses available.
- This is a single-machine prototype without a production authorization model, enterprise integration or an approval workflow. Business benefits and adoption have not been validated in this prototype.

The next priorities are an explicit engineering revision and order-reference model, plus additional business boundary cases. Each extension should start with an expected behavior that can be verified.
