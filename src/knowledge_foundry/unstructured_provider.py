"""Local open-source Unstructured PDF partitioning; never uploads originals.

Dependencies are included in requirements.txt.
HTML sources can use the internal provider via source_overrides.
"""
from importlib.metadata import version
import os

from knowledge_foundry.provider_exports import adapt_export


class Provider:
    version = 'unstructured-local-v2'

    def structure(self, artifact, options):
        if artifact['source']['format'] != 'pdf':
            raise ValueError('local Unstructured provider requires PDF; configure source_overrides for other formats')
        allowed = {'strategy', 'languages', 'infer_table_structure', 'hi_res_model_name'}
        if set(options) - allowed:
            raise ValueError('unknown local Unstructured options: ' + ', '.join(sorted(set(options) - allowed)))
        strategy = options.get('strategy', 'fast')
        if strategy not in {'fast', 'hi_res', 'ocr_only'}:
            raise ValueError('select an explicit local Unstructured strategy: fast, hi_res, ocr_only')
        languages = options.get('languages', ['fra', 'eng'])
        if not isinstance(languages, list) or not languages or any(not isinstance(v, str) or not v for v in languages):
            raise ValueError('languages must be a nonempty list of language codes')
        infer_tables = options.get('infer_table_structure', strategy == 'hi_res')
        if type(infer_tables) is not bool or (infer_tables and strategy != 'hi_res'):
            raise ValueError('infer_table_structure requires hi_res')
        if 'hi_res_model_name' in options and strategy != 'hi_res':
            raise ValueError('hi_res_model_name requires hi_res')

        # The library has optional runtime telemetry. Disable it before importing
        # partitioning, and accept only a verified local filename (no URL/API args).
        os.environ['SCARF_NO_ANALYTICS'] = 'true'
        os.environ['DO_NOT_TRACK'] = 'true'
        from unstructured.partition.pdf import partition_pdf
        from unstructured.partition.strategies import determine_pdf_or_image_strategy
        import pypdfium2 as pdfium
        with pdfium.PdfDocument(artifact['artifact_file']) as pdf:
            extractable = any(page.get_textpage().get_text_range().strip() for page in pdf)
        effective = determine_pdf_or_image_strategy(
            strategy=strategy, pdf_text_extractable=extractable, infer_table_structure=infer_tables)
        if effective != strategy:
            raise ValueError(f'Unstructured would fall back from {strategy} to {effective}; select a fresh explicit configuration')
        settings = {'strategy': strategy, 'languages': languages, 'infer_table_structure': infer_tables}
        if strategy == 'hi_res':
            settings['hi_res_model_name'] = options.get('hi_res_model_name', 'yolox')
        native = [element.to_dict() for element in partition_pdf(
            filename=artifact['artifact_file'], include_page_breaks=False,
            **settings)]
        result = adapt_export('unstructured', native, artifact)
        represented = {item['location']['page'] for item in result.elements}
        for page in sorted(set(range(1, result.metadata['page_count'] + 1)) - represented):
            result.warnings.append({'code': 'unstructured_page_without_elements', 'page': page,
                                    'message': 'Inspect the original: blank, image-only, or missed content.'})
        result.metadata.update(parser='unstructured-local', settings=settings,
                               parser_versions={name: version(name) for name in ('unstructured', 'pdfminer.six')},
                               execution_mode='local', requested_strategy=strategy, effective_strategy=effective,
                               service_cost_usd=0,
                               limitations=(['fast does not infer PDF tables or extract figures; image-only content requires OCR']
                                            if strategy == 'fast' else []))
        return result
