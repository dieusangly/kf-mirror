"""Checkout launcher for knowledge_foundry.applications.assistant.document_search."""
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'src'))

from knowledge_foundry.applications.assistant import document_search as implementation

sys.modules[__name__] = implementation
