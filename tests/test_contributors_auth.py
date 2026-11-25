"""Regression coverage for private contributor access and audit attribution."""

from contextlib import closing
from http.client import HTTPConnection
import importlib.util
import json
from pathlib import Path
import sqlite3
from threading import Thread

import pytest


APP = Path(__file__).resolve().parents[1] / 'apps' / 'contributors' / 'app.py'
PASSWORD = 'test password for the contributor fixtures'


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(APP.parent))
    spec = importlib.util.spec_from_file_location('contributor_app_under_test', APP)
    app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app)
    path = tmp_path / 'contributions.sqlite3'
    with closing(sqlite3.connect(path)) as old, old:
        old.execute("""
            CREATE TABLE contributions (
                id INTEGER PRIMARY KEY, kind TEXT NOT NULL, reference TEXT,
                created_at TEXT NOT NULL, metadata TEXT NOT NULL, content BLOB
            )
        """)
        old.execute("""
            INSERT INTO contributions(kind, created_at, metadata, content)
            VALUES ('source', '2025-01-01T00:00:00+00:00', ?, ?)
        """, (json.dumps({'title': 'Document historique', 'mode': 'file', 'filename': 'historique.txt'}), b'legacy'))

    with app.ThreadingHTTPServer(('127.0.0.1', 0), app.ContributorHandler) as server:
        server.public_origin = 'https://corpus.example.org'
        server.session_cookie_name = '__Host-corpus_session'
        server.accounts = app.Accounts(path)
        server.accounts.add('camille@example.org', 'Camille', PASSWORD)
        server.accounts.add('alex@example.org', 'Alex', PASSWORD)
        server.accounts.add('admin@example.org', 'Administrateur', PASSWORD, admin=True)
        server.contributions = app.Contributions(path)
        server.catalog = {'cases': []}
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join()


def request(server, route, data=None, *, cookie=None, origin='https://corpus.example.org'):
    connection = HTTPConnection('127.0.0.1', server.server_port, timeout=5)
    headers = {'Host': 'corpus.example.org', 'Origin': origin}
    if cookie:
        headers['Cookie'] = cookie
    method, body = 'GET', None
    if data is not None:
        method, body = 'POST', json.dumps(data)
        headers['Content-Type'] = 'application/json'
    with closing(connection):
        connection.request(method, route, body, headers)
        response = connection.getresponse()
        body = response.read()
        if response.getheader('Content-Type').startswith('application/json'):
            body = json.loads(body)
        return response.status, dict(response.getheaders()), body


def login(server, email):
    status, headers, body = request(server, '/api/login', {'email': email, 'password': PASSWORD})
    assert status == 200
    for attribute in ('HttpOnly', 'Secure', 'SameSite=Strict', 'Path=/'):
        assert attribute in headers['Set-Cookie']
    return headers['Set-Cookie'].split(';', 1)[0], body['user']


def test_private_routes_and_session_revocation(server):
    for route in ('/api/catalog', '/api/history', '/documents/1'):
        assert request(server, route)[0] == 401
    assert request(server, '/api/sources', {})[0] == 401
    assert request(server, '/api/login', {'email': 'camille@example.org', 'password': PASSWORD},
                   origin='https://another.example.org')[0] == 403
    assert request(server, '/api/login', {'email': 'camille@example.org', 'password': 'wrong'})[0] == 401
    cookie, user = login(server, 'CAMILLE@example.org')
    assert user['name'] == 'Camille'
    assert request(server, '/documents/1', cookie=cookie)[2] == b'legacy'
    assert request(server, '/api/logout', {}, cookie=cookie)[0] == 200
    assert request(server, '/api/session', cookie=cookie)[2] == {'user': None}
    assert request(server, '/documents/1', cookie=cookie)[0] == 401


def test_audit_uses_authenticated_actor_and_preserves_existing_rows(server):
    owner, _ = login(server, 'camille@example.org')
    peer, _ = login(server, 'alex@example.org')
    admin, _ = login(server, 'admin@example.org')

    status, _, source = request(server, '/api/sources', {
        'mode': 'url', 'title': 'Source de Camille', 'issuer': 'Test', 'language': 'fr',
        'url': 'https://example.org/source', 'username': 'Alex',
    }, cookie=owner)
    assert status == 201
    state = {'id': source['id'], 'action': 'withdrawn', 'reason': 'Autre édition'}
    assert request(server, '/api/contributions/state', state, cookie=peer)[0] == 403
    assert request(server, '/api/contributions/state', state, cookie=admin)[0] == 200
    history = request(server, '/api/history', cookie=peer)[2]
    assert [(row['action'], row['username'], row['can_restore']) for row in history] == [
        ('withdrawn', 'Administrateur', False), ('added', 'Camille', False), ('added', None, False),
    ]
    assert history[-1]['occurred_at'] == '2025-01-01T00:00:00+00:00'
    assert request(server, '/api/history', cookie=owner)[2][0]['can_restore']
    assert request(server, '/api/contributions/state', state | {'action': 'restored'}, cookie=owner)[0] == 200
    assert request(server, '/api/history', cookie=peer)[2][0]['username'] == 'Camille'


def test_repeated_login_attempts_are_limited(server):
    data = {'email': 'camille@example.org', 'password': 'incorrect'}
    for _ in range(5):
        assert request(server, '/api/login', data)[0] == 401
    status, headers, _ = request(server, '/api/login', data | {'password': PASSWORD})
    assert status == 429
    assert int(headers['Retry-After']) > 0
    assert request(server, '/api/session')[2] == {'user': None}
