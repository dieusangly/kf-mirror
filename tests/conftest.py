"""Real PostgreSQL/pgvector tests, isolated in a unique disposable schema."""
import os
import uuid

import psycopg
from psycopg import sql
import pytest

from knowledge_foundry.retrieval.artifacts import profile
from knowledge_foundry.storage.postgres import PostgresStore
from rag_fixtures import config, projection


@pytest.fixture
def store():
    dsn = os.environ.get('KF_TEST_DATABASE_URL')
    if not dsn:
        pytest.skip('set KF_TEST_DATABASE_URL for real PostgreSQL/pgvector integration')
    schema = 'kf_test_' + uuid.uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute('CREATE EXTENSION IF NOT EXISTS vector')
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    db = PostgresStore(psycopg.conninfo.make_conninfo(dsn, options=f'-c search_path={schema},public'))
    db.migrate()
    try:
        yield db
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


@pytest.fixture
def published(store, tmp_path):
    c = config(tmp_path)
    records, evidence, identity = projection()
    result = store.import_snapshot('animals', identity, records, evidence, profile(c['embedding']), 'english')
    store.publish('animals', result['snapshot_id'])
    return c, result['snapshot_id']
