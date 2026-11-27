"""Validate v1 dataset structure, provenance, engine certificates and contrasts offline.

Passing is preparation evidence, not independent semantic acceptance.
"""
import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import re
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))
from knowledge_foundry.assistant.datasets import DatasetRegistry, accepted_query_path, digest, rows
from scripts.generate_reasoning_assistant_evaluation import (
    AUTHORING, DATASET, FILES, FAMILIES, CATEGORIES, VERSION, read_rows, canonical_hash,
    fact_signature, build, save)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def normalize(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', text).casefold() if c.isalnum())


def checked_path(directory, name):
    path = (Path(directory) / name).resolve()
    require(path.parent == Path(directory).resolve(), 'artifact path must be a local filename')
    return path


def load_submission(directory=DATASET):
    """Only questions + manifest bytes are parsed; reference content never enters submissions."""
    directory = Path(directory)
    manifest = json.loads((directory / 'dataset-manifest-v1.json').read_text())
    require(manifest['schema_version'] == 'assistant-evaluation-1' and manifest['dataset_version'] == VERSION,
            'unsupported evaluation version')
    require(manifest['roles'] == FILES and set(manifest['files']) == set(FILES.values()), 'artifact roles differ')
    for name, sha in manifest['source_hashes'].items():
        path = (ROOT / name).resolve()
        require(path.is_relative_to(ROOT), 'source path must remain inside the repository')
        require(digest(path) == sha, 'pinned source changed: ' + name)
    for name, info in manifest['files'].items():
        require(digest(checked_path(directory, name)) == info['sha256'], 'frozen dataset hash differs: ' + name)
    questions = read_rows(checked_path(directory, manifest['roles']['questions']))
    require(len(questions) == manifest['coverage']['prompts'] == manifest['files'][FILES['questions']]['records'],
            'question count differs')
    require(len({q['question_id'] for q in questions}) == len(questions), 'duplicate question ID')
    fields = {'question_id','pair_id','question_type','family_id','category','coverage_category',
              'base_case_id','routing_kind','language','text'}
    for q in questions:
        require(set(q) == fields and isinstance(q['text'], str) and 0 < len(q['text']) <= 6000,
                'invalid question schema/size')
        require(q['question_type'] in ('new','variant','routing') and q['family_id'] in FAMILIES
                and q['language'] in ('en','fr'), 'invalid question dimensions')
        require(q['question_id'] == q['pair_id'] + '-' + q['language'], 'question identity differs')
        require(q['category'] in CATEGORIES if q['question_type'] != 'routing' else q['category'] is None,
                'invalid reasoning category')
    require(len({q['pair_id'] for q in questions}) == manifest['coverage']['bilingual_pairs'], 'pair count differs')
    groups = {}
    for q in questions:
        groups.setdefault(q['pair_id'], []).append(q)
    for pair in groups.values():
        require(len(pair) == 2 and {q['language'] for q in pair} == {'en','fr'}, 'missing translation')
        require(all(pair[0][k] == pair[1][k] for k in fields - {'question_id','language','text'}),
                'bilingual metadata differs')
    require(all(n == 1 for n in Counter((q['pair_id'],q['language']) for q in questions).values()), 'duplicate translation')
    require(Counter(q['language'] for q in questions) == {'en':len(questions)//2,'fr':len(questions)//2}, 'language counts differ')
    return questions, manifest


def answers(result):
    return {json.dumps(a, sort_keys=True) for a in result['answers']}


def check_contrast(base, variant, base_result, variant_result):
    require(variant['base_case_id'] == base['case_id'] and variant['question_type'] == 'variant', 'broken variant link')
    require(all(base[k] == variant[k] for k in ('policy_case_id','context','question','query','family_id','coverage_category')),
            'variant changed policy, question or query')
    change = variant['change']
    expected = deepcopy(base['facts'])
    if change['operation'] == 'add':
        require(change['fact']['id'] not in {f['id'] for f in expected}, 'variant added duplicate fact ID')
        require(change['fact']['atom'] not in [f['atom'] for f in expected], 'variant added duplicate atom')
        expected.append(change['fact'])
    elif change['operation'] == 'remove':
        require(sum(f['id'] == change['fact_id'] for f in expected) == 1, 'variant removed absent fact')
        expected = [f for f in expected if f['id'] != change['fact_id']]
    else:
        raise ValueError('unsupported change operation')
    require(expected == variant['facts'], 'variant is not exactly the declared one-fact change')
    relation = change['expected_relation']
    left, right = answers(base_result), answers(variant_result)
    conditions = {'answers_expand': left < right, 'answers_contract': right < left,
                  'answer_unchanged': left == right and base_result['status'] == variant_result['status'],
                  'unknown_to_entailed': base_result['status'] == 'unknown' and variant_result['status'] == 'entailed',
                  'conflict_to_entailed': base_result['status'] == 'conflict' and variant_result['status'] == 'entailed'}
    require(relation in conditions and conditions[relation], 'engine contrast disagrees with authored relationship')


def target_depths(result):
    depths = {}
    for step in result['usable_certificate']:
        key = json.dumps(step['atom'], sort_keys=True)
        depths[key] = 0 if 'fact_id' in step else 1 + max(depths[json.dumps(a, sort_keys=True)] for a in step['premises'])
    return [depths[json.dumps(a, sort_keys=True)] for a in result['results'][0]['supported_atoms']]


def validate(directory=DATASET):
    directory = Path(directory)
    checks, failures = [], []
    def check(name, action):
        try:
            action()
            checks.append({'name':name,'passed':True})
        except (ValueError, KeyError, TypeError, OSError, AssertionError) as exc:
            failures.append(name + ': ' + str(exc))
            checks.append({'name':name,'passed':False})
    try:
        questions, manifest = load_submission(directory)
        cases = read_rows(AUTHORING)
        registry = DatasetRegistry()
        records = {role:read_rows(directory / filename) for role,filename in FILES.items()}
    except (ValueError, KeyError, TypeError, OSError) as exc:
        return {'passed':False,'prompts':0,'engine_checked_references':0,'failures':[str(exc)],'checks':[],
                'reference_acceptance':False,'external_calls':0}
    by_case = {c['case_id']:c for c in cases}
    def coverage():
        require(len(cases) == len(by_case) == 144 and len(questions) == 288, 'expected 144 cases / 288 prompts')
        require(Counter(c['question_type'] for c in cases) == {'new':48,'variant':48,'routing':48}, 'type coverage differs')
        matrix = {(f,c):1 for f in FAMILIES for c in CATEGORIES}
        require(Counter((c['family_id'],c['category']) for c in cases if c['question_type']=='new') == matrix,
                'new-case family/category coverage differs')
        require(Counter((c['family_id'],c['coverage_category']) for c in cases if c['question_type']=='variant') == matrix,
                'variant base-cell coverage differs')
        require(Counter((c['family_id'],c['routing_kind']) for c in cases if c['question_type']=='routing') ==
                {(f,k):2 for f in FAMILIES for k in ('ambiguous','unsupported','documents')}, 'routing coverage differs')
        require(len({normalize(q['text']) for q in questions}) == len(questions), 'duplicate prompts')
        for case in cases:
            for lang in ('en','fr'):
                if 'facts' in case:
                    statements = [f['text'][lang] for f in case['facts']]
                    require(len(statements) == len(set(statements)), 'duplicate fact statement: ' + case['case_id'])
        for q in questions:
            require('universally quantified' not in q['text'] and 'universellement quantifiées' not in q['text']
                    and 'RAE1-' not in q['text'] and not re.search(r'\b(?:entailed|unknown|conflict)\b',q['text']),
                    'shared semantics, IDs or status labels in submitted text')
    check('coverage_and_prompt_isolation', coverage)
    def pins():
        for path, sha in manifest['source_hashes'].items():
            require(digest(ROOT/path) == sha, 'source changed: ' + path)
    check('source_hashes',pins)
    def reproduce():
        fresh, links = build(cases,registry)
        require(fresh == records, 'generated records differ from retained authoring inputs')
        require(links == manifest['cases'], 'case links/provenance differ')
        for role, values in records.items():
            require(len(values) == manifest['files'][FILES[role]]['records'], 'record count differs: '+role)
    check('deterministic_reproduction', reproduce)
    results = {r['question_id']:r for r in records['results']}
    encodings = {(r['case_id'],r['language']):r for r in records['encodings']}
    queries = {(r['case_id'],r['language']):r for r in records['queries']}
    config = registry.packages['synthetic-v1']['engine_config']
    def proofs():
        require(len(results)==len(encodings)==len(queries)==192, 'reference cardinality differs')
        for (cid,lang),encoding in encodings.items():
            result=results[cid+'-'+lang]
            checked=registry.engine.check_result(encoding,queries[(cid,lang)],config,result['result'])
            require(checked==result['checker'] and checked['valid'], 'invalid proof')
            cat=by_case[cid]['category']; outcome=result['result']['results'][0]
            depths=target_depths(result['result'])
            if cat=='DL': require(depths and set(depths)=={0}, 'DL target requires deduction')
            if cat=='OH': require(depths and set(depths)=={1}, 'OH target is not exactly one hop')
            if cat=='MH': require(depths and min(depths)>=2, 'MH target lacks multi-hop proof')
            if cat=='MP': require(outcome['status']=='unknown' and outcome['missing'], 'MP lacks missing-premise evidence')
            if cat=='CF': require(outcome['status']=='conflict' and outcome['conflicted_targets'], 'CF lacks conflict')
            if cat=='EX': require(outcome['status']=='entailed' and depths and min(depths)>0
                                 and encoding['incompatibilities'], 'EX lacks guarded exception rules')
    check('engine_certificates_and_actual_categories',proofs)
    def bilingual():
        for case in cases:
            if case['question_type']=='routing':continue
            en=results[case['case_id']+'-en']['result'];fr=results[case['case_id']+'-fr']['result']
            # Question IDs differ, answer meaning and diagnostic/proof atoms must not.
            for key in ('status','answers','supported_atoms','conflicted_targets','missing'):
                require(en['results'][0][key]==fr['results'][0][key], 'bilingual reference differs')
            require(en['usable_certificate']==fr['usable_certificate'], 'bilingual certificate differs')
    check('bilingual_symbolic_equivalence',bilingual)
    def contrasts():
        for c in cases:
            if c['question_type']=='variant':
                base=by_case[c['base_case_id']]
                for lang in ('en','fr'):
                    check_contrast(base,c,results[base['case_id']+'-'+lang]['result']['results'][0],
                                   results[c['case_id']+'-'+lang]['result']['results'][0])
    check('one_fact_changes_and_answer_relationships', contrasts)
    def novelty():
        signatures = set()
        for package_id, package in registry.packages.items():
            path = accepted_query_path(package)
            require(path.relative_to(ROOT).as_posix() in manifest['source_hashes'],
                    'evaluation manifest omits accepted queries: ' + package_id)
            baseline = rows(path)
            pilot_encodings = {(row['case_id'], row['language']): row['encoding']
                               for row in registry.records.values() if row['package_id'] == package_id}
            require(baseline.keys() == pilot_encodings.keys(), 'baseline query selection differs: ' + package_id)
            for identity, encoding in pilot_encodings.items():
                signatures.update(fact_signature(encoding['facts'], query)
                                  for query in baseline[identity]['queries'])
        dev=json.loads((ROOT/'tests/fixtures/reasoning-assistant-runtime-v1.json').read_text())
        dev_texts={normalize(r['text']) for r in dev}
        for c in cases:
            if c['question_type']!='new':continue
            require(c['novelty_rationale'] and fact_signature(c['facts'],c['query']) not in signatures,
                    'case is only a pilot renaming/query-shape replay')
            require(len(c['facts'])==len({json.dumps(f['atom'],sort_keys=True) for f in c['facts']}), 'duplicate fact atoms')
        require(not dev_texts.intersection(normalize(q['text']) for q in questions), 'development prompt overlap')
    check('conservative_pilot_renaming_and_development_overlap_screen',novelty)
    def documents():
        cache={}
        for c in cases:
            for evidence in c.get('document_evidence',[]):
                original=ROOT/evidence['original_path'];extracted=ROOT/evidence['extracted_path']
                require(digest(original)==evidence['original_sha256'], 'original source changed')
                if extracted not in cache:cache[extracted]=read_rows(extracted)
                chunk=cache[extracted][evidence['line']-1]
                require(canonical_hash(chunk)==evidence['canonical_sha256'], 'source chunk changed')
                require(evidence['original_passage'] in json.dumps(chunk,ensure_ascii=False).replace('\\n','\n'),
                        'source passage does not match pinned extraction')
    check('document_source_integrity',documents)
    return {'passed':not failures,'scope':'preparation-only','prompts':len(questions),
            'engine_checked_references':len(results),'checks':checks,'failures':failures,
            'reference_acceptance':False,'independent_semantic_review':'not-started',
            'external_calls':0,'deferred':['Natural-language encoding and policy-context fidelity.',
                'Semantic novelty, realistic phrasing and bilingual translation fidelity.',
                'Independent document source interpretation and published snapshot availability.',
                'Real-model assistant retrieval, routing, formalization and response quality.']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,default=DATASET)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.output and args.output.exists():
        parser.error('output must be fresh')
    report=validate(args.dataset)
    if args.output:save(args.output,report)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return int(not report['passed'])


if __name__=='__main__':
    raise SystemExit(main())
