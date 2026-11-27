"""Checkout launcher for knowledge_foundry.storage.accounts."""
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'src'))

from knowledge_foundry.storage import accounts as implementation

sys.modules[__name__] = implementation
