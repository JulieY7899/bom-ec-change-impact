"""Keep the released retrieval results from regressing in the existing CI suite."""
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATASETS = ("retrieval.json", "retrieval-independent.json")


class EvaluationAcceptanceTests(unittest.TestCase):
    def run_evaluations(self, model=None):
        for dataset in DATASETS:
            with self.subTest(dataset=dataset):
                command = [sys.executable, str(ROOT / "scripts/evaluate.py"), "--check",
                           "--min-candidate-precision", "1", "--retrieval-dataset",
                           str(ROOT / "examples/evaluation" / dataset)]
                if model:
                    command.extend(["--model", str(Path(model).resolve())])
                result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_keyword(self):
        self.run_evaluations()

    @unittest.skipUnless(os.environ.get("BOM_TEST_MODEL"), "set BOM_TEST_MODEL to the verified local model directory")
    def test_bge(self):
        self.run_evaluations(os.environ["BOM_TEST_MODEL"])
