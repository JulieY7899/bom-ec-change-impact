#!/usr/bin/env python3
"""Measure business decisions and engineering retrieval on labeled fixtures."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import Counter
from hashlib import sha256
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "examples" / "evaluation"
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from bom_change_assistant import BUILD_ID, engineering, knowledge, knowledge_store, workflow
from bom_change_assistant.analysis_service import AnalysisService
from bom_change_assistant.importers import WorkbookImporter
from bom_change_assistant.repository import Repository
from bom_change_assistant.semantic_index import REVISION, SemanticIndex
from tests.test_engineering import sheet
from tests.test_procurement import workbook

TOP_K = 3


def read_fixture(name):
    data = (DATA / name).read_bytes()
    return json.loads(data), sha256(data).hexdigest()


def install_bom(directory, change, case):
    directory.mkdir()
    path = directory / "synthetic.xlsx"
    scope = {field: change[field] for field in ("product", "plant", "engineering_version")}
    row = {"row_id": change["target_bom_row"], "parent": change["product"],
           "component": change["old_component"], "quantity": 1, "unit": "EA"}
    order = {"order_kind": "SALES", "order_item": "00010"}
    defaults = {
        "VERSION": scope,
        "BOM_ROW": {**scope, **row},
        "ORDER_REFERENCE": {**scope, **order, "order_status": "开放",
                            "order_relevant_date": change["effective_date"], "purchased": "否", "received": "否"},
        "ORDER_ROW": {**scope, **order, "row_id": row["row_id"]},
    }
    records = [
        {"engineering_record": "VERSION"},
        {"engineering_record": "BOM_ROW", **case.get("target_row", {})},
        *case["records"],
    ]
    workbook(path, [sheet([{**defaults[record["engineering_record"]], **record} for record in records])])
    repository = Repository(directory / "snapshot.sqlite3")
    repository.start_snapshot()
    WorkbookImporter(repository).import_file(path)
    repository.finish_snapshot(require_bom=True)
    return repository


def order_key(order):
    return f'{order["order_id"]}/{order["order_item"]}'


def evaluate_bom(root, dataset):
    results = []
    for case in dataset["cases"]:
        repository = install_bom(root / case["id"], dataset["change"], case)
        item, = AnalysisService(repository).analyze_changes([dataset["change"]])["items"]
        check, = item["result"]["engineering_impact"]["checks"]
        matches = check["version_reference_matches"]
        excluded = check["excluded_orders"]
        pending = check["pending_references"]
        actual = {
            "path": [row["row_id"] for row in check["path"]],
            "path_issue": check["path_issue"],
            "orders": {
                **{order_key(order): order["review_status"] for order in matches},
                **{order_key(order): "排除其他版本" for order in excluded},
                **{order_key(order): "版本引用待核" for order in pending},
            },
            "candidates": sorted(order_key(order) for order in matches if order["line_candidate"]),
        }
        expected = case["expected"]
        failures = [field for field in actual if actual[field] != expected.get(field, "")]
        rows = {order_key(order): order for order in matches + excluded + pending}
        if len(rows) != len(matches) + len(excluded) + len(pending):
            failures.append("duplicate_order_rows")
        for key, reason in expected["reasons"].items():
            record = rows.get(key, {})
            reasons = record.get("review_reasons", [record.get("reason")])
            if reason not in reasons or reason not in item["report"]:
                failures.append(f"reason:{key}")
        for key, owners in expected.get("owners", {}).items():
            if rows.get(key, {}).get("review_owners") != owners:
                failures.append(f"owners:{key}")
        if check["automatic_version_migration"] or check["approved_action"] is not None:
            failures.append("formal_action")
        if any(not record.get("evidence") for record in [check["target"], *rows.values()]):
            failures.append("missing_evidence")
        if any(f'{engineering.reference_label(order)} · {order["review_status"]}' not in item["report"]
               for order in matches):
            failures.append("report_order_rows")
        results.append({
            "id": case["id"], "rule": case["rule"], "expected": expected, "actual": actual,
            "order_counts": dict(Counter(actual["orders"].values())),
            "failures": failures, "passed": not failures,
        })
    return results


def index_documents(repository, dataset):
    sources, previews = {}, {}
    for doc in dataset["documents"]:
        preview = knowledge.preview_text(repository, "\n\n".join(doc["paragraphs"]).encode())
        indexed = knowledge.index(repository, {**preview, "section": doc["section"], "title": doc["title"],
                                                "approved": True})
        previews[doc["id"]] = preview
        sources[indexed["source_id"]] = doc
    if len(sources) != len(dataset["documents"]):
        raise ValueError("Retrieval corpus uses duplicate documents")
    return sources, previews


def hit_label(hit, sources):
    doc = sources.get(hit["source_id"])
    return f'{doc["id"]}:{hit["slide"]}' if doc else f'UNKNOWN:{hit["source_id"]}:{hit["slide"]}'


def correct_citation(hit, finding, sources, report):
    doc = sources.get(hit["source_id"])
    index = hit["slide"] - 1
    if not doc or not 0 <= index < len(doc["paragraphs"]):
        return False
    original = doc["paragraphs"][index]
    citation = (f'{knowledge_store.SECTIONS[doc["section"]]} · {doc["title"]} · {hit["source_id"]}'
                f' · 第 {index + 1} 段 · 片段 1')
    return (hit["kind"] == "text" and hit["chunk"] == 1 and hit["section"] == doc["section"]
            and hit["title"] == doc["title"] and hit["text"] == original
            and finding.get("citation") == citation and finding.get("excerpt") == original
            and citation in report and original in report)


def evaluate_query(repository, run, sources, case):
    context, run_id = run["context"], run["run_id"]
    response = workflow.engineering_references(repository, context, case["query"], case["section"])
    hits = response["matches"]
    if len(hits) > TOP_K or bool(hits) != (response["status"] == "retrieved"):
        raise RuntimeError(f'{case["id"]}: invalid retrieval response')
    labels = [hit_label(hit, sources) for hit in hits]
    expected = set(case["relevant"])
    relevant = expected.intersection(labels)
    assessment = workflow.engineering_assessment(
        repository, run_id, context, [hit["reference"] for hit in hits],
    )
    findings = workflow.engineering_assessment(repository, run_id, context)["findings"]
    if findings != assessment["findings"]:
        raise RuntimeError(f'{case["id"]}: saved citations differ from selected citations')
    report = workflow.engineering_report(run["report"], assessment)
    valid = [label for label, hit, finding in zip(labels, hits, findings)
             if correct_citation(hit, finding, sources, report)]
    return {
        **case, "status": response["status"], "returned": labels, "correct_citations": valid,
        "precision_at_3": len(relevant) / TOP_K,
        "recall_at_3": len(relevant) / len(expected) if expected else None,
        "correct_abstention": not hits if not expected else None,
    }


def evaluate_revision(repository, run, dataset, sources, previews):
    revision = dataset["revision"]
    document_id = revision["document"]
    context, run_id = run["context"], run["run_id"]
    expected = {label for case in dataset["cases"] for label in case["relevant"]
                if label.startswith(document_id + ":")}
    before_cases = [case for case in dataset["cases"] if expected.intersection(case["relevant"])]
    previous = {}
    for case in before_cases:
        response = workflow.engineering_references(repository, context, case["query"], case["section"])
        for hit in response["matches"]:
            label = hit_label(hit, sources)
            if label in expected and label in case["relevant"]:
                previous[label] = hit
    if not expected or set(previous) != expected:
        raise RuntimeError(f"Cannot exercise citation revisions; missing {sorted(expected - set(previous))}")
    old_refs = {hit["reference"] for hit in previous.values()}
    old_text = {hit["text"] for hit in previous.values()}
    workflow.engineering_assessment(repository, run_id, context, sorted(old_refs))
    doc = next(doc for doc in sources.values() if doc["id"] == document_id)
    knowledge.index(repository, {
        **previews[document_id], "title": doc["title"], "section": revision["section"], "approved": True,
        "excerpts": [{"slide": 1, "text": revision["replacement"]}],
    })
    assessment = workflow.engineering_assessment(repository, run_id, context)
    report = workflow.engineering_report(run["report"], assessment)
    revised = workflow.engineering_references(repository, context, revision["revised_query"], revision["section"])
    withdrawn = workflow.engineering_references(repository, context, revision["withdrawn_query"], "")
    old_section = workflow.engineering_references(repository, context, revision["revised_query"], doc["section"])
    observed = [hit for response in (revised, withdrawn, old_section) for hit in response["matches"]]
    observed.extend(knowledge_store.resolve_references(repository, sorted(old_refs)))
    stale = {old["reference"] for old in previous.values()
             if any(hit["reference"] == old["reference"] or hit["text"] == old["text"] for hit in observed)}
    rejected = False
    try:
        workflow.engineering_assessment(repository, run_id, context, sorted(old_refs))
    except ValueError:
        rejected = True
    revised_hits = [hit for hit in revised["matches"]
                    if hit_label(hit, sources) == f"{document_id}:1" and hit["text"] == revision["replacement"]]
    return {
        "checked_old_references": len(old_refs),
        "returned_stale_references": len(stale),
        "stale_reference_rate": len(stale) / len(old_refs),
        "saved_report_withholds_old_text": not assessment["findings"] and not any(text in report for text in old_text),
        "reselection_notice": assessment["notice"],
        "reselecting_old_references_rejected": rejected,
        "revised_fragment_found": bool(revised_hits),
        "removed_from_old_section": all(hit_label(hit, sources).split(":")[0] != document_id
                                        for hit in old_section["matches"]),
        "withdrawn_fragment_absent": all(hit_label(hit, sources) != f"{document_id}:2"
                                         for hit in withdrawn["matches"]),
    }


def evaluate_retrieval(repository, change, dataset):
    prepared = workflow.prepare(repository, {"role": "engineering", "draft": change})
    if not prepared["ready"]:
        raise RuntimeError(f'Synthetic workflow preflight failed: {prepared["context"]["issues"]}')
    context = workflow.confirm(repository, {
        "confirmed": True, "context": prepared["context"], "confirmation_token": prepared["confirmation_token"],
    })
    item, = AnalysisService(repository).analyze_changes([change])["items"]
    run = workflow.finish(repository, context, item, account_managed=True)
    sources, previews = index_documents(repository, dataset)
    labels = {f'{doc["id"]}:{number}' for doc in sources.values()
              for number in range(1, len(doc["paragraphs"]) + 1)}
    for case in dataset["cases"]:
        if set(case["relevant"]) - labels:
            raise ValueError(f'{case["id"]}: labels must refer to an approved corpus paragraph')
    cases = [evaluate_query(repository, run, sources, case) for case in dataset["cases"]]
    answerable = [case for case in cases if case["relevant"]]
    negatives = [case for case in cases if not case["relevant"]]
    citations = sum(len(case["returned"]) for case in cases)
    applicable = sum(len(set(case["relevant"]).intersection(case["returned"])) for case in cases)
    correct_citations = sum(len(case["correct_citations"]) for case in cases)
    abstained = sum(case["correct_abstention"] for case in negatives)
    return {
        "method": knowledge_store.method(repository),
        "answerable_queries": len(answerable),
        "no_evidence_queries": len(negatives),
        "precision_at_3": mean(case["precision_at_3"] for case in answerable),
        "precision_at_3_ceiling": mean(min(len(case["relevant"]), TOP_K) / TOP_K for case in answerable),
        "recall_at_3": mean(case["recall_at_3"] for case in answerable),
        "candidate_precision": applicable / citations if citations else None,
        "applicable_candidates": applicable,
        "citation_accuracy": correct_citations / citations if citations else None,
        "correct_citations": correct_citations,
        "citations_checked": citations,
        "abstention_rate": abstained / len(negatives),
        "correct_abstentions": abstained,
        "revision": evaluate_revision(repository, run, dataset, sources, previews) if "revision" in dataset else None,
        "cases": cases,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, help="Use the installed, verified BGE model instead of keyword search")
    parser.add_argument("--retrieval-dataset", type=Path, default=DATA / "retrieval.json",
                        help="Labeled retrieval fixture (defaults to the original 36 queries)")
    parser.add_argument("--check", action="store_true",
                        help="Require every labeled reference and abstention to match")
    parser.add_argument("--min-candidate-precision", type=float,
                        help="Fail if the applicable fraction of returned candidates is below this value")
    parser.add_argument("--report", type=Path, help="Write full case results as JSON")
    args = parser.parse_args()
    if args.min_candidate_precision is not None and not 0 <= args.min_candidate_precision <= 1:
        parser.error("--min-candidate-precision must be between 0 and 1")
    bom_data, bom_digest = read_fixture("bom.json")
    retrieval_data, retrieval_digest = read_fixture(args.retrieval_dataset.resolve())
    with tempfile.TemporaryDirectory(prefix="bom-evaluation-") as directory:
        root = Path(directory)
        bom = evaluate_bom(root, bom_data)
        repository = install_bom(root / "retrieval", bom_data["change"], bom_data["cases"][0])
        if args.model is not None:
            repository.semantic_index = SemanticIndex(root / "retrieval" / "approved.qdrant", args.model)
        retrieval = evaluate_retrieval(repository, bom_data["change"], retrieval_data)
    report = {"build": BUILD_ID, "dataset_sha256": {"bom.json": bom_digest, args.retrieval_dataset.name: retrieval_digest},
              "embedding_revision": REVISION if args.model is not None else None,
              "bom": bom, "retrieval": retrieval}
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    failed = [case for case in bom if not case["passed"]]
    print(f"BOM {len(bom) - len(failed)}/{len(bom)} cases passed")
    for case in failed:
        print(f'  {case["id"]}: {", ".join(case["failures"])}')
    print(f'Retrieval {retrieval["method"]} ({len(retrieval["cases"])} queries)')
    print(f'  Precision@3 {retrieval["precision_at_3"]:.1%} | Recall@3 {retrieval["recall_at_3"]:.1%}')
    print(f'  Applicable candidates {retrieval["applicable_candidates"]}/{retrieval["citations_checked"]}')
    print(f'  Citation accuracy {retrieval["correct_citations"]}/{retrieval["citations_checked"]}')
    print(f'  Correct abstention {retrieval["correct_abstentions"]}/{retrieval["no_evidence_queries"]}')
    revision = retrieval["revision"]
    if revision is not None:
        print(f'  Stale references {revision["returned_stale_references"]}/{revision["checked_old_references"]}')
    false_positives = [case["id"] for case in retrieval["cases"] if case["correct_abstention"] is False]
    if false_positives:
        print(f'  No-evidence queries with results: {", ".join(false_positives)}')
    misses = [case["id"] for case in retrieval["cases"]
              if set(case["relevant"]) - set(case["returned"])]
    if misses:
        print(f'  Queries missing labeled evidence: {", ".join(misses)}')
    precision_failure = (args.min_candidate_precision is not None
                         and (retrieval["candidate_precision"] or 0) < args.min_candidate_precision)
    if precision_failure:
        print(f'  Candidate precision below required floor {args.min_candidate_precision:g}')
    retrieval_failure = args.check and (misses or false_positives)
    citation_failure = not retrieval["citations_checked"] or retrieval["citation_accuracy"] != 1
    revision_failure = revision is not None and (
                        revision["returned_stale_references"] or not revision["saved_report_withholds_old_text"]
                        or not revision["reselection_notice"] or not revision["reselecting_old_references_rejected"]
                        or not revision["revised_fragment_found"] or not revision["removed_from_old_section"]
                        or not revision["withdrawn_fragment_absent"])
    if revision_failure:
        print(f"  Revision check failed: {revision}")
    return int(bool(failed or citation_failure or revision_failure or precision_failure or retrieval_failure))


if __name__ == "__main__":
    raise SystemExit(main())
