"""Prepare deterministic checks, then finalize source-reviewed Document Structuring."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]

from knowledge_foundry.document_structuring_review import main  # noqa: E402

if __name__ == '__main__':
    raise SystemExit(main())
