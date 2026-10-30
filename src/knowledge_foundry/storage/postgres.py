"""Transactional snapshot storage and session-scoped conversation persistence."""
import hashlib
import os
import uuid
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from knowledge_foundry.structural_chunks import stable_hash


# Rank distinct query-term coverage, weighted by evidence frequency. Repeating a
# common term throughout a large table must not outrank a precise paragraph.
# Documents and q are supplied by the two callers, with bound parameters only.
LEXICAL_SCORES = """
, words AS (SELECT unnest(tsvector_to_array(q.vector)) AS word FROM q)
, weights AS MATERIALIZED (
    SELECT word, ln(1.0 + (SELECT count(*) + 1 FROM documents) /
        (1.0 + (SELECT count(*) FROM documents d WHERE d.terms @@ quote_literal(word)::tsquery))) AS weight
    FROM words
)
, scores AS MATERIALIZED (
    SELECT d.id,
        coalesce(sum(w.weight) FILTER (WHERE d.terms @@ quote_literal(w.word)::tsquery) /
            nullif(sum(w.weight), 0), 0) AS score,
        ts_rank_cd(d.terms, q.terms, 2) AS density
    FROM documents d CROSS JOIN q CROSS JOIN weights w
    GROUP BY d.id, d.terms, q.terms
)
"""


def vector_literal(vector):
    from knowledge_foundry.semantic_clusters import normalized
    normalized(vector)
    return '[' + ','.join(str(float(v)) for v in vector) + ']'


class PostgresStore:
    def __init__(self, dsn=None):
        self.dsn = dsn or os.environ.get('KF_DATABASE_URL')
        if not self.dsn:
            raise ValueError('KF_DATABASE_URL is required; see doc/aml-cft-pipeline.md')

    @contextmanager
    def connection(self):
        with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5) as conn:
            conn.execute("SET statement_timeout = '30s'")
            yield conn

    def migrate(self):
        with self.connection() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(71890431)')
            conn.execute('CREATE TABLE IF NOT EXISTS kf_migrations (version text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())')
            for path in sorted((Path(__file__).parent / 'migrations').glob('*.sql')):
                checksum = hashlib.sha256(path.read_bytes()).hexdigest()
                saved = conn.execute('SELECT sha256 FROM kf_migrations WHERE version=%s', (path.name,)).fetchone()
                if saved:
                    if saved['sha256'] != checksum:
                        raise ValueError('applied migration changed: ' + path.name)
                    continue
                conn.execute(path.read_text())
                conn.execute('INSERT INTO kf_migrations(version,sha256) VALUES (%s,%s)', (path.name, checksum))

    def import_snapshot(self, corpus, identity, records, evidence, embedding_profile, language='simple'):
        if not corpus.strip() or not records or not any(r['kind'] == 'passage' for r in records):
            raise ValueError('a named corpus and nonempty passage projection are required')
        evidence_ids = {e['id'] for e in evidence}
        if any(not set(r['evidence_ids']) <= evidence_ids for r in records) or any(
                not set(e['context_ids']) <= evidence_ids for e in evidence):
            raise ValueError('projection contains unresolved evidence lineage')
        # Include vector availability/content so an auxiliary embedding pass creates
        # a new immutable snapshot instead of mutating a published index.
        identity = {**identity, 'corpus': corpus, 'language': language,
                    'embedding_profile': embedding_profile,
                    'records_sha256': stable_hash(records), 'evidence_sha256': stable_hash(evidence),
                    'record_count': len(records), 'evidence_count': len(evidence)}
        sid = 'snapshot:' + stable_hash(identity)
        with self.connection() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (sid,))
            existing = conn.execute('SELECT identity FROM kf_snapshots WHERE id=%s', (sid,)).fetchone()
            if existing:
                if existing['identity'] != identity:
                    raise ValueError('snapshot identity collision')
                return {'snapshot_id': sid, 'created': False, 'records': len(records), 'evidence': len(evidence)}
            conn.execute('INSERT INTO kf_snapshots(id,corpus,identity,embedding_profile,status) VALUES (%s,%s,%s,%s,%s)',
                         (sid, corpus, Jsonb(identity), Jsonb(embedding_profile), 'ready'))
            with conn.cursor() as cur:
                cur.executemany('INSERT INTO kf_evidence(snapshot_id,id,payload) VALUES (%s,%s,%s)',
                                [(sid, e['id'], Jsonb(e)) for e in evidence])
                rows = []
                for r in records:
                    vid = None
                    if r.get('vector') is not None:
                        if not embedding_profile or len(r['vector']) != embedding_profile['dimensions']:
                            raise ValueError('vector dimensions differ from snapshot profile')
                        videntity = {'text': r['text'], **embedding_profile}
                        if embedding_profile['provider'] == 'fixture':
                            videntity['fixture_vector_sha256'] = stable_hash(r['vector'])
                        vid = stable_hash(videntity)
                        conn.execute('INSERT INTO kf_vectors(id,identity,embedding) VALUES (%s,%s,%s::vector) ON CONFLICT(id) DO NOTHING',
                                     (vid, Jsonb(videntity), vector_literal(r['vector'])))
                    rows.append((sid, r['id'], r['kind'], r['text'], r['source_ids'], r['document_ids'],
                                 r['evidence_ids'], Jsonb(r['metadata']), vid, language, r['text']))
                cur.executemany('INSERT INTO kf_records(snapshot_id,id,kind,text,source_ids,document_ids,evidence_ids,metadata,vector_id,terms) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,to_tsvector(%s::regconfig,%s))', rows)
            conn.execute('INSERT INTO kf_corpora(corpus) VALUES (%s) ON CONFLICT DO NOTHING', (corpus,))
        return {'snapshot_id': sid, 'created': True, 'records': len(records), 'evidence': len(evidence)}

    def publish(self, corpus, snapshot_id):
        with self.connection() as conn:
            row = conn.execute('SELECT * FROM kf_snapshots WHERE id=%s AND corpus=%s FOR UPDATE', (snapshot_id, corpus)).fetchone()
            if not row:
                raise ValueError('snapshot is absent from this corpus')
            counts = conn.execute('SELECT count(*) AS n FROM kf_records WHERE snapshot_id=%s', (snapshot_id,)).fetchone()
            evidence = conn.execute('SELECT count(*) AS n FROM kf_evidence WHERE snapshot_id=%s', (snapshot_id,)).fetchone()
            if counts['n'] != row['identity']['record_count'] or evidence['n'] != row['identity']['evidence_count']:
                raise ValueError('snapshot is incomplete; publication refused')
            conn.execute("UPDATE kf_snapshots SET status='published',published_at=coalesce(published_at,now()) WHERE id=%s", (snapshot_id,))
            conn.execute('UPDATE kf_corpora SET active_snapshot=%s WHERE corpus=%s', (snapshot_id, corpus))

    def snapshot(self, corpus, snapshot_id=None):
        with self.connection() as conn:
            if snapshot_id:
                row = conn.execute("SELECT * FROM kf_snapshots WHERE corpus=%s AND id=%s AND status='published'", (corpus, snapshot_id)).fetchone()
            else:
                row = conn.execute("SELECT s.* FROM kf_corpora c JOIN kf_snapshots s ON s.id=c.active_snapshot WHERE c.corpus=%s AND s.status='published'", (corpus,)).fetchone()
        if not row:
            raise ValueError('no selected published snapshot; import and explicitly publish first')
        return row

    def evaluation_snapshot(self, corpus, snapshot_id):
        """Read one explicit ready snapshot for evaluation without publishing it."""
        if not snapshot_id:
            raise ValueError('evaluation requires an explicit snapshot ID')
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM kf_snapshots WHERE corpus=%s AND id=%s AND status IN ('ready','published')",
                               (corpus, snapshot_id)).fetchone()
        if not row:
            raise ValueError('evaluation snapshot is absent from this corpus')
        return row

    def snapshots(self, corpus=None):
        with self.connection() as conn:
            return conn.execute('SELECT s.*,c.active_snapshot=s.id AS active FROM kf_snapshots s LEFT JOIN kf_corpora c ON c.corpus=s.corpus WHERE (%s::text IS NULL OR s.corpus=%s) ORDER BY s.created_at DESC', (corpus, corpus)).fetchall()

    def vector_coverage(self, snapshot_id):
        with self.connection() as conn:
            return {r['kind']: {'records': r['records'], 'vectors': r['vectors']} for r in conn.execute(
                'SELECT kind,count(*) AS records,count(vector_id) AS vectors FROM kf_records '
                'WHERE snapshot_id=%s GROUP BY kind', (snapshot_id,))}

    def search(self, snapshot, query, kind, method, search, vector=None, source_ids=None, document_ids=None):
        if kind not in {'passage', 'claim', 'summary'} or method not in {'keyword', 'vector', 'combined'}:
            raise ValueError('invalid retrieval mode/method')
        sid = snapshot['id']
        language = snapshot['identity']['language']
        scope = 'r.snapshot_id=%s AND r.kind=%s'
        args = [sid, kind]
        if source_ids:
            scope += ' AND r.source_ids && %s::text[]'
            args.append(list(source_ids))
        if document_ids:
            scope += ' AND r.document_ids && %s::text[]'
            args.append(list(document_ids))
        lists = []
        with self.connection() as conn:
            if method in {'keyword', 'combined'}:
                rows = conn.execute("""WITH q0 AS (SELECT to_tsvector(%s::regconfig,%s) AS vector),
                    q AS (SELECT vector, coalesce((SELECT string_agg(quote_literal(t),' | ')
                        FROM unnest(tsvector_to_array(vector)) t),'')::tsquery AS terms FROM q0),
                    candidates AS MATERIALIZED (SELECT r.* FROM kf_records r CROSS JOIN q WHERE """ + scope + """
                        AND r.terms @@ q.terms),
                    memberships AS MATERIALIZED (
                        SELECT id AS record_id, unnest(evidence_ids) AS evidence_id FROM candidates
                        WHERE kind='passage'),
                    documents AS MATERIALIZED (
                        SELECT DISTINCT e.id, to_tsvector(%s::regconfig,e.payload->>'text') AS terms
                        FROM kf_evidence e JOIN memberships m ON e.id=m.evidence_id
                        WHERE e.snapshot_id=%s
                        UNION ALL SELECT id,terms FROM candidates
                    )""" + LEXICAL_SCORES + """,
                    span_scores AS (SELECT m.record_id, max(s.score) AS score, max(s.density) AS density
                        FROM memberships m JOIN scores s ON s.id=m.evidence_id GROUP BY m.record_id),
                    ranked AS (SELECT r.id, (b.score + coalesce(s.score,b.score))/2 AS score,
                        greatest(b.score,coalesce(s.score,b.score)) AS coverage,
                        coalesce(s.density,b.density) AS density
                        FROM candidates r JOIN scores b ON b.id=r.id
                        LEFT JOIN span_scores s ON s.record_id=r.id)
                    SELECT r.*, ranked.score::float8 AS score FROM candidates r JOIN ranked USING(id)
                    WHERE ranked.coverage >= %s AND ranked.score > %s
                    ORDER BY ranked.score DESC, ranked.density DESC, r.id LIMIT %s""",
                    [language, query, *args, language, sid, search.get('minimum_keyword_coverage', .25),
                     search['minimum_keyword_score'], search['candidate_k']]).fetchall()
                lists.append(rows)
            if method in {'vector', 'combined'}:
                if vector is None or not snapshot['embedding_profile'] or len(vector) != snapshot['embedding_profile']['dimensions']:
                    raise ValueError('vector search requires the snapshot embedding profile')
                available = conn.execute('SELECT count(*) AS n FROM kf_records WHERE snapshot_id=%s AND kind=%s AND vector_id IS NOT NULL', (sid, kind)).fetchone()['n']
                if available == 0:
                    raise ValueError('selected retrieval mode has no vectors; use keyword or reimport with --embed-missing')
                value = vector_literal(vector)
                rows = conn.execute('SELECT r.*,1-(v.embedding <=> %s::vector) AS score FROM kf_records r JOIN kf_vectors v ON v.id=r.vector_id WHERE ' + scope +
                                    ' AND 1-(v.embedding <=> %s::vector)>=%s ORDER BY v.embedding <=> %s::vector,r.id LIMIT %s',
                                    [value, *args, value, search['minimum_vector_similarity'], value, search['candidate_k']]).fetchall()
                lists.append(rows)
        if method != 'combined':
            return lists[0][:search['top_k']]
        scores, rows = {}, {}
        for result in lists:
            for rank, row in enumerate(result, 1):
                key = row['id']
                rows[key] = row
                scores[key] = scores.get(key, 0.) + 1 / (search['rrf_constant'] + rank)
        return [{**rows[k], 'score': scores[k]} for k in sorted(scores, key=lambda k: (-scores[k], k))[:search['top_k']]]

    def list_siblings(self, snapshot_id, seeds, limit):
        """Complete bounded lists using saved direct governing relationships."""
        parents = self.evidence(snapshot_id, sorted({k for seed in seeds for k in seed['context_ids']}))
        groups, memberships = {}, {}
        for seed in seeds:
            governing = [parents[k] for k in seed['context_ids'] if k in parents
                         and parents[k]['element_kind'] in {'heading', 'paragraph'}]
            if len(governing) == 1:
                memberships[seed['id']] = (seed['document']['id'], governing[0]['id'])
        with self.connection() as conn:
            for doc_id, parent in sorted(set(memberships.values())):
                rows = conn.execute("""SELECT payload FROM kf_evidence WHERE snapshot_id=%s
                    AND payload->'reference'->>'document_id'=%s
                    AND payload->>'element_kind'='list_item' AND payload->>'origin'='artifact'
                    AND (payload->'context_ids') ? %s ORDER BY id LIMIT %s""",
                    (snapshot_id, doc_id, parent, limit + 1)).fetchall()
                groups[(doc_id, parent)] = [r['payload'] for r in rows]
        return {seed_id: groups[key] for seed_id, key in memberships.items()}

    def rank_evidence(self, snapshot_id, query, evidence):
        """Order exact spans with the same lexical normalization as retrieval."""
        if not evidence:
            return []
        with self.connection() as conn:
            language = conn.execute('SELECT identity->>\'language\' AS language FROM kf_snapshots WHERE id=%s',
                                    (snapshot_id,)).fetchone()['language']
            rows = conn.execute("""WITH q0 AS (SELECT to_tsvector(%s::regconfig,%s) AS vector),
                q AS (SELECT vector, coalesce((SELECT string_agg(quote_literal(t),' | ')
                    FROM unnest(tsvector_to_array(vector)) t),'')::tsquery AS terms FROM q0),
                documents AS (SELECT id,to_tsvector(%s::regconfig,text) AS terms
                    FROM unnest(%s::text[],%s::text[]) AS input(id,text))""" + LEXICAL_SCORES + """
                SELECT id,score,density FROM scores ORDER BY score DESC,density DESC,id""",
                (language, query, language, [e['id'] for e in evidence],
                 [e['text'] for e in evidence])).fetchall()
            by_id = {e['id']: e for e in evidence}
            ranked = [{**by_id[r['id']], 'lexical_score': float(r['score'])} for r in rows]
        return ranked

    def supporting_passages(self, snapshot_id, evidence_ids, original_chunk_ids, query, limit,
                            language, source_ids=None, document_ids=None):
        if not original_chunk_ids:
            raise ValueError('auxiliary record has no original chunk provenance')
        with self.connection() as conn:
            return conn.execute("WITH q AS (SELECT coalesce((SELECT string_agg(quote_literal(t),' | ') FROM unnest(tsvector_to_array(to_tsvector(%s::regconfig,%s))) t),'')::tsquery AS terms) "
                "SELECT r.* FROM kf_records r CROSS JOIN q WHERE snapshot_id=%s AND kind='passage' AND evidence_ids && %s::text[] "
                "AND (metadata->'original'->'original_chunk_ids') ?| %s::text[] "
                'AND (%s::text[] IS NULL OR source_ids && %s::text[]) AND (%s::text[] IS NULL OR document_ids && %s::text[]) '
                'ORDER BY ts_rank_cd(r.terms,q.terms,32) DESC,id LIMIT %s',
                (language, query, snapshot_id, evidence_ids, original_chunk_ids, source_ids, source_ids,
                 document_ids, document_ids, limit)).fetchall()

    def evidence(self, snapshot_id, ids):
        if not ids:
            return {}
        with self.connection() as conn:
            rows = conn.execute('SELECT id,payload FROM kf_evidence WHERE snapshot_id=%s AND id=ANY(%s)', (snapshot_id, list(ids))).fetchall()
        return {r['id']: r['payload'] for r in rows}

    @staticmethod
    def owner_hash(token):
        if not isinstance(token, str) or len(token) < 32:
            raise ValueError('session token must contain at least 32 characters')
        return hashlib.sha256(token.encode()).hexdigest()

    def new_session(self, snapshot_id, token):
        sid = str(uuid.uuid4())
        with self.connection() as conn:
            row = conn.execute("SELECT id FROM kf_snapshots WHERE id=%s AND status='published'", (snapshot_id,)).fetchone()
            if not row:
                raise ValueError('session requires a published snapshot')
            conn.execute('INSERT INTO kf_sessions(id,owner_hash,snapshot_id) VALUES (%s,%s,%s)', (sid, self.owner_hash(token), snapshot_id))
        return sid

    def sessions(self, snapshot_id, token):
        with self.connection() as conn:
            rows = conn.execute('SELECT id,created_at FROM kf_sessions WHERE snapshot_id=%s AND owner_hash=%s ORDER BY created_at DESC', (snapshot_id, self.owner_hash(token))).fetchall()
        return [{**r, 'id': str(r['id'])} for r in rows]

    def history(self, session_id, token, snapshot_id=None):
        with self.connection() as conn:
            session = conn.execute('SELECT * FROM kf_sessions WHERE id=%s AND owner_hash=%s', (session_id, self.owner_hash(token))).fetchone()
            if not session or (snapshot_id and session['snapshot_id'] != snapshot_id):
                raise ValueError('conversation is unavailable for this session and snapshot')
            rows = conn.execute('SELECT * FROM kf_messages WHERE session_id=%s ORDER BY sequence', (session_id,)).fetchall()
        return [{**r, 'id': str(r['id']), 'session_id': str(r['session_id'])} for r in rows]

    def save_turn(self, session_id, token, snapshot_id, question, answer):
        mid = str(uuid.uuid4())
        with self.connection() as conn:
            owned = conn.execute('SELECT id FROM kf_sessions WHERE id=%s AND owner_hash=%s AND snapshot_id=%s FOR UPDATE', (session_id, self.owner_hash(token), snapshot_id)).fetchone()
            if not owned:
                raise ValueError('conversation is unavailable for this session and snapshot')
            conn.execute('INSERT INTO kf_messages(id,session_id,role,content,payload) VALUES (%s,%s,%s,%s,%s)', (str(uuid.uuid4()), session_id, 'user', question, Jsonb({})))
            conn.execute('INSERT INTO kf_messages(id,session_id,role,content,payload) VALUES (%s,%s,%s,%s,%s)', (mid, session_id, 'assistant', answer['text'], Jsonb(answer)))
        return mid

    def feedback(self, session_id, token, message_id, rating, comment=''):
        if rating not in (-1, 1) or len(comment) > 2000:
            raise ValueError('feedback requires rating -1/1 and comment <=2000 characters')
        with self.connection() as conn:
            owned = conn.execute("SELECT m.id FROM kf_messages m JOIN kf_sessions s ON s.id=m.session_id WHERE m.id=%s AND m.session_id=%s AND s.owner_hash=%s AND m.role='assistant'", (message_id, session_id, self.owner_hash(token))).fetchone()
            if not owned:
                raise ValueError('feedback message is unavailable for this session')
            conn.execute('INSERT INTO kf_feedback(message_id,rating,comment) VALUES (%s,%s,%s) ON CONFLICT(message_id) DO UPDATE SET rating=excluded.rating,comment=excluded.comment,updated_at=now()', (message_id, rating, comment))
