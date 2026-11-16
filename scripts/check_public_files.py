#!/usr/bin/env python3
"""Reject runtime data and credential-shaped strings in the Git index."""
from pathlib import Path
import re
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_DATA = {'data/.gitkeep', 'data/README.md', 'reports/.gitkeep'}
TEMPLATE = 'outputs/bom-import-template/BOM工程变更数据导入模板.xlsx'
FORBIDDEN_PREFIXES = ('work/', 'output/', '.playwright-cli/', '.venv/')
GENERATED = {'examples/sample-report.md', 'examples/sample-result.json',
             'examples/verification.json', 'docs/profile-draft.md'}
PATTERNS = [
    re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})'),
    re.compile(rb'sk-(?:proj-)?[A-Za-z0-9_-]{24,}'),
    re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    re.compile(rb'/(?:Users|home)/[A-Za-z0-9_.-]+/'),
]


def main() -> int:
    entries = subprocess.check_output(
        ['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    issues = []
    for name in filter(None, entries):
        path = Path(name)
        if (name.startswith(FORBIDDEN_PREFIXES) or name in GENERATED
                or path.name == '.DS_Store' or path.name.startswith('.env')
                or re.search(r'\.(?:sqlite3|sqlite|db)(?:-|$)', name)
                or path.suffix in {'.log', '.zip', '.csv', '.tsv'}
                or (name.startswith(('data/', 'reports/')) and name not in ALLOWED_DATA)
                or (name.startswith('outputs/') and name != TEMPLATE)
                or (path.suffix in {'.xlsx', '.xls'} and name != TEMPLATE)):
            issues.append(f'{name}: runtime or unreviewed data file')
            continue
        blob = subprocess.check_output(['git', 'show', ':' + name], cwd=ROOT)
        if any(pattern.search(blob) for pattern in PATTERNS):
            issues.append(f'{name}: credential-shaped text or private local path')
        if name == TEMPLATE:
            import io
            with zipfile.ZipFile(io.BytesIO(blob)) as workbook:
                if any(pattern.search(workbook.read(member))
                       for member in workbook.namelist() for pattern in PATTERNS):
                    issues.append(f'{name}: suspicious workbook content')
    if issues:
        print('\n'.join(issues), file=sys.stderr)
        return 1
    print(f'Public-file checks passed for {len(entries) - 1} tracked files.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
