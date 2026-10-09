"""Run structural chunking directly from a repository checkout."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from knowledge_foundry.chunking import main


if __name__ == "__main__":
    raise SystemExit(main())
