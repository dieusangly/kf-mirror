"""Compatibility import for knowledge_foundry.retrieval.adapters.engineering."""
import sys

from knowledge_foundry.retrieval.adapters import engineering as implementation

sys.modules[__name__] = implementation
