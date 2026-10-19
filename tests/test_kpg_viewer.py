"""Exercise browser-independent graph layout and provenance selection in Node."""
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), 'Node is required for viewer logic checks')
class ViewerLogicTests(unittest.TestCase):
    def check_js(self, script):
        bootstrap = """
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = {window: {}};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const KPG = context.window.KPG;
"""
        result = subprocess.run(['node', '-e', bootstrap + script, str(ROOT / 'scripts/kpg_viewer/core.js')],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_summary_columns_follow_levels_and_carry_forward_inputs(self):
        self.check_js("""
const node = (id, group, level) => ({id, group, label: id, record: {level}});
const nodes = [node('leaf-b', 'summary', 1), node('top', 'summary', 3),
  node('group-3', 'summary_cluster', 3), node('mid', 'summary', 2),
  node('group-2', 'summary_cluster', 2), node('leaf-a', 'summary', 1),
  node('semantic', 'semantic_cluster'), node('structural', 'structural_chunk')];
const columns = KPG.layoutColumns(nodes, true);
assert.equal(columns.map(c => c.key).join('|'),
  'summary:3|summary_cluster:3|summary:2|summary_cluster:2|summary:1|semantic_cluster|structural_chunk');
assert.equal(columns.find(c => c.key === 'summary:1').nodes.map(n => n.id).join('|'), 'leaf-a|leaf-b');
assert.equal(new Set(columns.flatMap(c => c.nodes.map(n => n.id))).size, nodes.length);
// A carried level-one input can feed level three without a level-two node.
const carried = KPG.layoutColumns(nodes.filter(n => n.record.level !== 2), true);
assert.equal(carried.map(c => c.key).join('|'),
  'summary:3|summary_cluster:3|summary:1|semantic_cluster|structural_chunk');
// The paged summary list keeps one summary column.
assert.equal(KPG.layoutColumns(nodes, false).map(c => c.key).join('|'),
  'summary|summary_cluster|semantic_cluster|structural_chunk');
""")

    def test_classified_claim_hides_duplicate_candidate_but_keeps_source_path(self):
        self.check_js("""
const node = (id, group, record = {}) => ({id, group, label: id, record});
const graph = {nodes: [node('claim', 'claim', {chunk_ids: ['chunk']}),
  node('candidate', 'candidate', {chunk_ids: ['chunk']}), node('topic', 'topic'),
  node('semantic', 'semantic_cluster'), node('structural', 'structural_chunk', {original_chunk_ids: ['chunk']}),
  node('chunk', 'chunk'), node('element', 'element')], edges: [
  {source:'candidate', target:'claim', relation:'classified as'},
  {source:'topic', target:'claim', relation:'groups'},
  {source:'claim', target:'semantic', relation:'generated from'},
  {source:'candidate', target:'semantic', relation:'generated from'},
  {source:'semantic', target:'structural', relation:'groups'},
  {source:'structural', target:'chunk', relation:'composed from'},
  {source:'chunk', target:'element', relation:'supported by'}]};
const index = KPG.createIndex(graph);
const selected = KPG.localNeighborhood(index, 'claim');
assert.equal(selected.has('candidate'), false);
for (const id of ['claim','topic','semantic','structural','chunk','element']) assert(selected.has(id), id);
const candidate = KPG.localNeighborhood(index, 'candidate');
assert(candidate.has('candidate'));
assert(candidate.has('claim'));
assert(candidate.has('semantic'));
""")

    def test_connected_relations_follow_the_selected_record_direction(self):
        self.check_js("""
for (const [relation, inverse] of [['groups','grouped by'], ['classified as','classified from'],
  ['uses','used by'], ['supported by','supports'], ['generated from','generates'], ['summarizes','summarized by']]) {
  const edge = {source:'a', target:'b', relation};
  assert.equal(KPG.relationLabel(edge, 'a'), relation);
  assert.equal(KPG.relationLabel(edge, 'b'), inverse);
}
""")

    def test_cluster_and_document_views_deduplicate_classified_candidates(self):
        self.check_js("""
const node = (id, group, record = {}) => ({id, group, label: id, record});
const graph = {nodes: [node('cluster', 'semantic_cluster'), node('doc', 'document'),
  node('claim', 'claim', {chunk_ids:['chunk'], review_status:'accepted'}),
  node('classified', 'candidate', {chunk_ids:['chunk'], review_status:'draft'}),
  node('unclassified', 'candidate', {chunk_ids:['chunk'], review_status:'draft'}),
  node('chunk', 'chunk', {document_id:'doc'})], edges:[
  {source:'classified', target:'claim', relation:'classified as'},
  ...['claim','classified','unclassified'].map(source => ({source, target:'cluster', relation:'generated from'}))]};
const index = KPG.createIndex(graph);
const state = {mode:'claim', selectedId:'cluster', page:0, filters:{}};
let view = KPG.computeView(index, state);
assert(view.visible.has('claim'));
assert(view.visible.has('unclassified'));
assert(!view.visible.has('classified'));
view = KPG.computeView(index, {...state, mode:'document', document:'doc'});
assert.equal(view.availableClaims,2); // Deduplicate before pagination.
assert(view.visible.has('claim') && view.visible.has('unclassified'));
assert(!view.visible.has('classified'));
view = KPG.computeView(index, {...state, filters:{type:'candidate'}});
assert(view.visible.has('classified') && view.visible.has('unclassified'));
view = KPG.computeView(index, {...state, filters:{review:'draft'}});
assert(view.visible.has('classified')); // Its classified counterpart was filtered out.
view = KPG.computeView(index, {...state, selectedId:'classified'});
assert(view.visible.has('classified')); // Explicitly selected candidates remain inspectable.
view = KPG.computeView(index, {...state, mode:'full'});
assert(view.visible.has('classified')); // Debug view retains all records.
""")
