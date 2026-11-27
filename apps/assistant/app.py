"""Checkout launcher for knowledge_foundry.applications.assistant.ui."""
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'src'))

from knowledge_foundry.applications.assistant import ui as implementation

if __name__ == "__main__":
    implementation.main()

if __name__ != '__main__':
    sys.modules[__name__] = implementation
