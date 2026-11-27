"""Checkout launcher for knowledge_foundry.applications.contributors.http."""
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'src'))

from knowledge_foundry.applications.contributors import http as implementation
from knowledge_foundry.applications.contributors.http import Accounts, Contributions, ContributorHandler, ThreadingHTTPServer, load_catalog, main

if __name__ == "__main__":
    implementation.main()

def __getattr__(name):
    return getattr(implementation, name)


if __name__ != '__main__':
    sys.modules[__name__] = implementation
