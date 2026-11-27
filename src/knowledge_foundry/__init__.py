"""Knowledge Foundry pipeline package and lazy imports for its pre-A2 module names."""
from importlib import import_module as _import_module
from importlib.machinery import ModuleSpec as _ModuleSpec
from pathlib import Path as _Path
import sys as _sys


_LEGACY_MODULES = {
    'chunking': 'knowledge_engineering.structuring.chunking',
    'chunking_audit': 'knowledge_engineering.review.chunking_audit',
    'claim_context': 'knowledge_engineering.semantic.claim_context',
    'claim_generation_and_classification': 'knowledge_engineering.claims.claim_generation_and_classification',
    'claim_source_context': 'knowledge_engineering.claims.claim_source_context',
    'classification_reuse': 'knowledge_engineering.claims.classification_reuse',
    'concurrency': 'shared.concurrency',
    'corpus': 'knowledge_engineering.corpus',
    'document_structure': 'knowledge_engineering.structuring.document_structure',
    'document_structuring': 'knowledge_engineering.structuring.document_structuring',
    'embeddings': 'shared.embeddings',
    'evidence': 'provenance.evidence',
    'generation_context': 'knowledge_engineering.claims.generation_context',
    'ingestion': 'knowledge_engineering.ingestion',
    'openai_tools': 'shared.openai_tools',
    'parsers': 'knowledge_engineering.structuring.parsers',
    'parsing': 'knowledge_engineering.structuring.parsing',
    'pdf_source': 'knowledge_engineering.structuring.pdf_source',
    'pdf_structure': 'knowledge_engineering.structuring.pdf_structure',
    'pdf_tables': 'knowledge_engineering.structuring.pdf_tables',
    'provider_exports': 'knowledge_engineering.structuring.provider_exports',
    'semantic_clusters': 'knowledge_engineering.semantic.semantic_clusters',
    'structural_chunks': 'knowledge_engineering.structuring.structural_chunks',
    'structural_links': 'knowledge_engineering.structuring.structural_links',
    'structure_providers': 'knowledge_engineering.structuring.structure_providers',
    'summaries': 'knowledge_engineering.summaries.summaries',
    'unstructured_provider': 'knowledge_engineering.structuring.unstructured_provider',
    'workflow': 'workflows.document_pipeline',
    'workflow_reporting': 'workflows.reporting',
}

# Search real modules first. This private path entry also exposes the aliases to
# pkgutil without loading a parser, provider, or any other implementation.
_LEGACY_PATH = str(_Path(__file__).with_name('_compat'))


class _LegacyImporter:
    def __init__(self, path):
        if path != _LEGACY_PATH:
            raise ImportError(path)

    def iter_modules(self, prefix=''):
        for name in _LEGACY_MODULES:
            yield prefix + name, False

    def find_spec(self, fullname, target=None):
        parent, _, name = fullname.rpartition('.')
        if parent == __name__ and name in _LEGACY_MODULES:
            relative_file = _LEGACY_MODULES[name].replace('.', '/') + '.py'
            origin = str(_Path(__file__).parent / relative_file)
            return _ModuleSpec(fullname, self, origin=origin)
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        alias = module.__name__
        target = _LEGACY_MODULES[alias.rpartition('.')[2]]
        _sys.modules[alias] = _import_module('.' + target, __name__)

    def get_code(self, fullname):
        """Run the original command when Python executes an alias with -m."""
        if fullname.rpartition('.')[2] in {'ingestion', 'parsing'}:
            source = f'from {fullname} import main\nraise SystemExit(main())'
        else:
            source = f'import {fullname}'
        return compile(source, f'<{fullname}>', 'exec')


# Package reloads and importlib.invalidate_caches() must preserve discovery.
if not any(getattr(hook, '__module__', None) == __name__ for hook in _sys.path_hooks):
    _sys.path_hooks.append(_LegacyImporter)
__path__.append(_LEGACY_PATH)
