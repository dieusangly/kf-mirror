"""Active corpus scope and explicitly accepted, source-specific limitations."""
import json
from pathlib import Path

from knowledge_foundry.ingestion import ingest


def readiness(pipeline_path, inventory=None):
    pipeline_path = Path(pipeline_path)
    pipeline = json.loads(pipeline_path.read_text())
    root = (pipeline_path.parent / pipeline['root']).resolve()
    manifest = json.loads((root / pipeline['manifest_file']).read_text())
    entries = {e['manifest_id']: e for e in manifest['sources']}
    active = [e['manifest_id'] for e in manifest['sources']]
    selected = pipeline['manifest_ids']
    problems, limitations = [], []
    if set(active) != set(selected) or len(selected) != len(set(selected)) or len(active) != len(set(active)):
        problems.append('active scope and pipeline selection differ')
    missing = sorted(set(selected) - entries.keys())
    if missing:
        problems.append('missing or unselected required originals: ' + ', '.join(missing))
    try:
        inventory = inventory if inventory is not None else ingest(pipeline_path)
        problems.extend(f"{e['manifest_id']}: {e.get('error', e)}" for e in inventory['outcomes'] if e['status'] != 'ingested')
    except (ValueError, OSError, KeyError) as exc:
        problems.append(str(exc))
    source_ids = {entries[mid]['source_id'] for mid in selected if mid in entries}
    for mid in selected:
        entry = entries.get(mid, {})
        for dependency in entry.get('requires_source_ids', []):
            if dependency in source_ids:
                continue
            decision = next((d for d in entry.get('accepted_missing_dependencies', [])
                             if d.get('source_id') == dependency
                             and d.get('decision') == 'historical_body_only'
                             and d.get('authority') == 'explicit_user_instruction'), None)
            # A scope decision cannot silently turn an incomplete source into current guidance.
            if (decision and entry.get('historical') is True
                    and entry.get('completeness') == 'incomplete_annex_pairing'):
                limitations.append({'manifest_id': mid, **decision})
            else:
                problems.append(f'{mid}: unresolved required source {dependency}')
        variant = entry.get('language_variant_of')
        if variant:
            other = next((e for e in entries.values() if e['source_id'] == variant), {})
            if (variant not in source_ids or other.get('language_variant_of') != entry.get('source_id')
                    or other.get('work_id') != entry.get('work_id') or other.get('language') == entry.get('language')):
                problems.append(f'{mid}: invalid or unselected language counterpart')
    return {'ready': not problems, 'registered_files': sum(bool(e.get('sha256') and e.get('retrieved_on')) for e in entries.values()), 'selected_files': len(selected),
            'active_files': len(active), 'missing_active_manifest_ids': missing,
            'problems': problems, 'accepted_limitations': limitations}
