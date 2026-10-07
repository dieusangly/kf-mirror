"""Run local parsing directly from a repository checkout."""

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from knowledge_foundry.parsing import main


if __name__ == "__main__":
    raise SystemExit(main())
