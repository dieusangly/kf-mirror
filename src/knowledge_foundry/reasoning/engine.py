"""Open Ontologies APIs plus scoped provenance and conservative answer gates."""
import json
import math
from pathlib import Path
from urllib.parse import quote

import pyoxigraph as ox
from open_ontologies_lite import OntologyEngine, builtin_rules, check_horn, run_horn
from open_ontologies_lite.shacl import shacl_validate

from knowledge_foundry.reasoning.adapter import (
    CONFLICT, KF, OWL, atom, compile_projection, require,
)
from knowledge_foundry.retrieval.artifacts import file_hash

UPSTREAM_COMMIT = 'c7fd75f9491253c56e4d4f408ceac179de343e48'
PROVENANCE_GRAPH = 'urn:kf:reasoning:provenance'
SHAPES = f'''
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix kf: <{KF}> .
kf:ClaimShape a sh:NodeShape ; sh:targetClass kf:ReviewedClaim, kf:UnreviewedClaim ;
  sh:property [ sh:path kf:claimId ; sh:minCount 1 ; sh:maxCount 1 ] ;
  sh:property [ sh:path kf:claimHash ; sh:minCount 1 ; sh:maxCount 1 ] ;
  sh:property [ sh:path kf:buildId ; sh:minCount 1 ] ;
  sh:property [ sh:path kf:jurisdiction ; sh:minCount 1 ] ;
  sh:property [ sh:path kf:validFrom ; sh:minCount 1 ] ;
  sh:property [ sh:path kf:review ; sh:minCount 1 ] ;
  sh:property [ sh:path kf:evidence ; sh:minCount 1 ; sh:nodeKind sh:IRI ] .
'''


class ReasoningEngine:
    def __init__(self, bundle, mapping, *, jurisdiction, as_of):
        self._initialize_projection(compile_projection(bundle, mapping, jurisdiction=jurisdiction, as_of=as_of))

    def _initialize_projection(self, projection):
        self.projection = projection
        self.asserted = OntologyEngine()
        self.asserted.load(self.projection['turtle'])
        self.asserted.store.load(self.projection['provenance'], format=ox.RdfFormat.TURTLE,
                                 to_graph=ox.NamedNode(PROVENANCE_GRAPH))
        self.closure = None
        self.result = None
        self.report = None

    @classmethod
    def from_files(cls, bundle, mapping, **scope):
        return cls(json.loads(Path(bundle).read_text()), json.loads(Path(mapping).read_text()), **scope)

    def run(self, output_dir, *, checker=None, timeout=30., max_iterations=100):
        """Write a fresh snapshot/certificate. Never overwrite an earlier run."""
        require(type(max_iterations) is int and max_iterations > 0, 'max_iterations must be a positive integer')
        require(type(timeout) in {int, float} and math.isfinite(timeout) and timeout > 0,
                'timeout must be a positive finite number')
        directory = Path(output_dir).resolve()
        directory.mkdir(parents=True, exist_ok=False)
        projection = self.projection
        validation = shacl_validate(projection['provenance'], SHAPES)
        if not projection['active']:
            self.report = {'status': 'insufficient', 'reason': 'no mappings in selected scope',
                           'validation': validation, 'conclusions': []}
            (directory / 'report.json').write_text(json.dumps(self.report, indent=2) + '\n')
            return self.report
        require(validation['conforms'] is True and validation['focus_nodes'] > 0, 'SHACL validation failed')
        # Preserve the upstream built-in bytes/verdict when there are no custom rules.
        rules = list(builtin_rules()) + projection['rules'] if projection['rules'] else None
        self.result = run_horn(self.asserted.store, rules, certificate_dir=directory / 'certificate',
                               max_iterations=max_iterations)
        report = self.result.to_dict()
        report['check'] = check_horn(directory / 'certificate', checker=checker, timeout=timeout)
        self.directory, self.checker, self.timeout = directory, checker, timeout
        self.hashes = {name: file_hash(directory / 'certificate' / name)
                       for name in ('rules.tsv', 'asserted.tsv', 'horn.tsv')}
        self.closure = OntologyEngine()
        self.closure.load(projection['turtle'])
        self.closure.load('\n'.join(' '.join(s.conclusion) + ' .' for s in self.result.steps))
        self.closure.store.load(projection['provenance'], format=ox.RdfFormat.TURTLE,
                                to_graph=ox.NamedNode(PROVENANCE_GRAPH))
        conflicts = self.consistency()
        status = ('contradiction' if conflicts else 'incomplete' if not report['fixpoint_reached'] else
                  'ready' if report['check']['status'] == 'accepted' else 'unchecked')
        self.report = {'status': status, 'upstream_commit': UPSTREAM_COMMIT,
                       **{k: projection[k] for k in ('version', 'bundle_sha256', 'mapping_sha256', 'jurisdiction', 'as_of', 'excluded')},
                       'validation': validation, 'reasoning': report, 'conflicts': conflicts,
                       'certificate_sha256': self.hashes}
        self.asserted.save(str(directory / 'asserted.trig'))
        self.closure.save(str(directory / 'closure.trig'))
        (directory / 'report.json').write_text(json.dumps(self.report, ensure_ascii=False, indent=2) + '\n')
        return self.report

    def verify_certificate(self):
        """Recheck bytes and logical steps. Hashes alone never establish a proof."""
        require(self.result is not None, 'reasoning has not run')
        try:
            matches = all(file_hash(self.directory / 'certificate' / name) == digest
                          for name, digest in self.hashes.items())
        except OSError:
            matches = False
        if not matches:
            return {'status': 'binding_mismatch', 'checked': False, 'verdict': None}
        return check_horn(self.directory / 'certificate', checker=self.checker, timeout=self.timeout)

    def query(self, sparql, *, include_inferred=True):
        """Local read-only SPARQL; network SERVICE queries are deliberately blocked."""
        require('SERVICE' not in sparql.upper() and 'FROM' not in sparql.upper(),
                'federated/external dataset queries are not supported')
        engine = self.closure if include_inferred else self.asserted
        require(engine is not None, 'reasoning has not run')
        return engine.query(sparql)

    def consistency(self):
        """Witness checks, not complete OWL satisfiability or certified refutation."""
        require(self.closure is not None, 'reasoning has not run')
        queries = {
            'disjoint_types': f'SELECT ?entity ?left ?right WHERE {{ ?entity a ?left, ?right . ?left <{OWL}disjointWith> ?right }}',
            'same_and_different': f'SELECT ?left ?right WHERE {{ ?left <{OWL}sameAs> ?right ; <{OWL}differentFrom> ?right }}',
            'obligation_and_prohibition': f'SELECT ?entity ?action WHERE {{ ?entity <{KF}requires> ?action ; <{KF}prohibits> ?action }}',
            'explicit_conflict': f'SELECT ?entity WHERE {{ ?entity a <{CONFLICT}> }}',
        }
        return [{'kind': kind, **row} for kind, query in queries.items()
                for row in self.closure.query(query)['rows']]

    def explain(self, goal):
        """Return all transitive premises/rules/citations for one ground triple."""
        wanted = atom(goal)
        require(self.report is not None, 'reasoning has not run')
        base = {'goal': goal, 'jurisdiction': self.projection['jurisdiction'], 'as_of': self.projection['as_of'],
                'conclusions': [], 'premises': [], 'rules': [], 'citations': []}
        if self.report['status'] in {'contradiction', 'incomplete', 'insufficient'}:
            return {**base, 'status': self.report['status'], 'conflicts': self.report.get('conflicts', [])}
        steps = {s.conclusion: s for s in self.result.steps}
        if wanted not in self.projection['asserted'] and wanted not in steps:
            return {**base, 'status': 'insufficient', 'reason': 'not entailed; absence is not falsity'}
        verification = self.verify_certificate()
        if verification['status'] != 'accepted':
            return {**base, 'status': 'unchecked', 'certificate': verification}
        premises, used_rules, mapping_ids, visited, proof = set(), {}, set(), set(), []
        table = list(builtin_rules()) + self.projection['rules']

        def visit(fact):
            if fact in visited:
                return
            visited.add(fact)
            if fact in self.projection['asserted']:
                premises.add(fact)
                mapping_ids.update(self.projection['owners'][fact])
                return
            step = steps[fact]
            rule = table[step.rule]
            for premise in step.premises:
                visit(premise)
            owner = self.projection['rule_owners'].get(rule.name)
            if owner:
                mapping_ids.add(owner)
            used_rules[step.rule] = {'index': step.rule, 'name': rule.name,
                                     'body': [list(a.fields()) for a in rule.body],
                                     'head': list(rule.head.fields()), 'mapping_id': owner}
            proof.append({'conclusion': list(fact), 'rule_index': step.rule,
                          'premises': [list(p) for p in step.premises]})

        visit(wanted)
        rows = [r for r in self.projection['active'] if r['id'] in mapping_ids]
        # All evidence for each used claim, including qualifications, is required.
        claims = {r['claim_id'] for r in rows}
        keys = set()
        for row in rows:
            # The provenance projection links a mapping to its exact evidence IDs.
            node = 'urn:kf:mapping:' + quote(row['id'], safe='')
            result = self.asserted.query(f'SELECT ?e WHERE {{ GRAPH <{PROVENANCE_GRAPH}> {{ <{node}> <{KF}evidence> ?e }} }}')
            keys.update(r['e'].removeprefix('urn:kf:evidence:') for r in result['rows'])
        return {**base, 'status': 'asserted' if wanted in self.projection['asserted'] else 'inferred',
                'conclusions': [goal], 'premises': [list(p) for p in sorted(premises)],
                'rules': list(used_rules.values()), 'proof': proof, 'claim_ids': sorted(claims),
                'mappings': rows, 'citations': [self.projection['citations'][k] for k in sorted(keys)],
                'certificate': verification}
