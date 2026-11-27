"""Checkout launcher for knowledge_foundry.applications.contributors.catalog."""
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'src'))

from knowledge_foundry.applications.contributors import catalog as implementation

sys.modules[__name__] = implementation
