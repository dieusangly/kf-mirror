"""Compatibility entry point for knowledge_foundry.knowledge_engineering.review.claim_review."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from knowledge_foundry.knowledge_engineering.review import claim_review as implementation

if __name__ == "__main__":
    raise SystemExit(implementation.main())

sys.modules[__name__] = implementation
