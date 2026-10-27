"""Provider boundary for Document Structuring, independent of acquisition.

Adapters return located elements, optional ready chunks, and untouched native
output. Extra layout/table/confidence data belongs in element details/native.
"""
from dataclasses import dataclass, field
import hashlib
import importlib
import json
from pathlib import Path
import shutil
import subprocess
from typing import Protocol


@dataclass
class ProviderResult:
    elements: list
    native: object = None
    warnings: list = field(default_factory=list)
    chunks: list | None = None  # [{text, evidence: [{locator_key, span?}]}]
    metadata: dict = field(default_factory=dict)


class StructureProvider(Protocol):
    version: str

    def structure(self, artifact: dict, options: dict) -> ProviderResult: ...


class InternalProvider:
    version = 'format-adapters-v12'

    def __init__(self, config, native_input_dir=None):
        from knowledge_foundry.parsing import _configuration
        self.config = config
        self.settings = _configuration(config)
        self.native_input_dir = Path(native_input_dir) if native_input_dir else None
        self.converter = None
        self.ocr_checked = False

    def _check_ocr(self):
        if self.ocr_checked or self.settings['ocr']['mode'] == 'off':
            return
        ocr = self.settings['ocr']
        cmd = ocr['tesseract_cmd']
        if not (shutil.which(cmd) or Path(cmd).is_file()):
            raise ValueError(f'Tesseract CLI unavailable: {cmd}')
        command = [cmd, '--list-langs']
        if ocr['tessdata_path']:
            command += ['--tessdata-dir', ocr['tessdata_path']]
        checked = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
        missing = [language for language in ocr['languages']
                   if not language.startswith('iso:') and language not in set(checked.stdout.splitlines()[1:])]
        if checked.returncode or missing:
            raise ValueError('Tesseract language data unavailable: ' + ', '.join(missing))
        self.ocr_checked = True

    def structure(self, artifact, options):
        from knowledge_foundry.parsing import PARSERS, _tool_versions, _source_metadata, _embedded_metadata, _configuration
        effective_settings = _configuration({'structuring': {'options': options}})
        if effective_settings != self.settings:
            self.settings, self.converter, self.ocr_checked = effective_settings, None, False
        from knowledge_foundry.parsers import parse_pdf, build_pdf_converter
        source, document = artifact['source'], artifact['document']
        format_name = source['format']
        path = Path(artifact['artifact_file'])
        parser, name = PARSERS[format_name]
        settings = dict(self.settings[format_name])
        origin, replay = None, None
        if format_name == 'html':
            overrides = settings.pop('source_overrides')
            settings.update(overrides.get(artifact['manifest_id'], {}))
            origin = 'parsing_config' if ('content_selector' in overrides.get(artifact['manifest_id'], {}) or 'content_selector' in options.get('html', {})) else 'parser_default'
        if format_name == 'pdf':
            self._check_ocr()
            settings['ocr'] = self.settings['ocr']
        if parser is parse_pdf:
            native_document = None
            if self.native_input_dir:
                prior = self.native_input_dir / 'documents' / document['id'].removeprefix('doc:')
                saved = json.loads((prior / 'artifact-bundle.json').read_text())
                parsed = json.loads((prior / 'parse.json').read_text())
                data = (prior / 'docling.json').read_bytes()
                if (saved['documents'] != [document] or saved['sources'] != [source]
                        or parsed['settings'] != settings or parsed['parser'] != 'docling'):
                    raise ValueError('saved native input source identity or parser settings mismatch')
                native_document = json.loads(data)
                replay = {'directory': str(prior), 'native_sha256': hashlib.sha256(data).hexdigest(),
                          'source_sha256': document['content_sha256'], 'parser_versions': parsed['parser_versions']}
            elif self.converter is None:
                self.converter = build_pdf_converter(settings)
            elements, native, warnings = parse_pdf(path, source, settings, converter=self.converter,
                **({'native_document': native_document} if replay else {}))
        else:
            elements, native, warnings = parser(path, source, settings)
        metadata = _source_metadata(source, document, artifact['manifest_entry'], settings, origin)
        metadata.update(_embedded_metadata(path, format_name))
        result = ProviderResult(elements, native, warnings, metadata={
            'parser': name, 'settings': settings, 'parser_versions': _tool_versions(format_name),
            'metadata_origins': {k: v for k, v in metadata.items() if v is not None},
            'page_count': len(native.get('pages', {})) if isinstance(native, dict) else None})
        if replay:
            result.metadata['native_replay'] = replay
        return result


class ExportProvider:
    """Source-bound provider response bridge; transport/auth stay in SDK adapters.

    Exports use the provider result contract, so no guessed vendor field mappings
    or remote calls occur here. A provider can return elements, chunks, or both.
    """
    version = 'provider-export-v1'

    def structure(self, artifact, options):
        folder = Path(options['response_dir']).resolve()
        path = (folder / (artifact['manifest_id'] + '.json')).resolve()
        if not path.is_relative_to(folder):
            raise ValueError('provider export path escapes response directory')
        response = json.loads(path.read_text())
        if response.get('source_sha256') != artifact['document']['content_sha256']:
            raise ValueError('provider response is not bound to the ingested original')
        if options.get('format', 'normalized') != 'normalized':
            from knowledge_foundry.provider_exports import adapt_export
            result = adapt_export(options['format'], response['native'], artifact)
            result.metadata['response_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            return result
        if not isinstance(response.get('elements', []), list):
            raise ValueError('provider response elements must be a list')
        return ProviderResult(response.get('elements', []), response.get('native'), response.get('warnings', []),
                              response.get('chunks'), {'parser': options.get('name', 'export'),
                              'response_sha256': hashlib.sha256(path.read_bytes()).hexdigest()})


def resolve_provider(config, native_input_dir=None):
    spec = config.get('structuring', {'provider': 'internal', 'options': {}})
    if not isinstance(spec, dict) or set(spec) - {'provider', 'options', 'source_overrides'}:
        raise ValueError('structuring requires provider, options and optional source_overrides')
    name, options = spec.get('provider', 'internal'), spec.get('options', {})
    overrides = spec.get('source_overrides', {})
    if not isinstance(overrides, dict) or any(not isinstance(v, dict) or set(v) - {'provider', 'options'} for v in overrides.values()):
        raise ValueError('source_overrides must map manifest IDs to provider/options')
    if not isinstance(options, dict):
        raise ValueError('provider options must be an object')
    if name == 'internal':
        provider = InternalProvider(config, native_input_dir)
    elif name == 'export':
        if native_input_dir:
            raise ValueError('native PDF replay is specific to the internal provider')
        provider = ExportProvider()
    else:
        if not isinstance(name, str) or name.count(':') != 1:
            raise ValueError('provider must be internal, export, or module:factory')
        module, factory = name.split(':')
        provider = getattr(importlib.import_module(module), factory)()
        if not callable(getattr(provider, 'structure', None)) or not getattr(provider, 'version', None):
            raise ValueError('provider must implement structure and declare a version')
    return name, options, provider
