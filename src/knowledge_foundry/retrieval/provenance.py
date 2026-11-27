"""Compatibility import for knowledge_foundry.provenance.graph_lookup."""
import sys

from knowledge_foundry.provenance import graph_lookup as implementation

sys.modules[__name__] = implementation
