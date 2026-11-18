"""Download only the pinned public model files into a user-selected local folder."""
import argparse
import os
import sys
import tempfile
from pathlib import Path
from shutil import copyfileobj
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from bom_change_assistant.semantic_index import FILES, MODEL_REPO, REVISION, verified


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    directory = parser.parse_args().directory.resolve()
    for name, checksum in FILES.items():
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and verified(target, checksum):
            continue
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temp:
            tmp = Path(temp.name)
        try:
            with urlopen(f"https://huggingface.co/{MODEL_REPO}/resolve/{REVISION}/{name}", timeout=90) as r:
                with tmp.open("wb") as out:
                    copyfileobj(r, out)
            if not verified(tmp, checksum):
                raise ValueError(f"模型文件校验失败 {name}")
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)
    print("本机模型已通过校验", directory)


if __name__ == "__main__":
    main()
