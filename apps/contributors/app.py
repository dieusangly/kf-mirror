"""Run the local corpus contributor workspace."""

import argparse
import base64
from contextlib import closing
from datetime import datetime, timezone
from getpass import getpass
from http.cookies import CookieError, SimpleCookie
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import quote, urlsplit

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from accounts import Accounts, LoginLimited, SESSION_SECONDS, connect, manageable
from catalog import CATEGORIES, FAMILIES, load_catalog


MAX_FILE_BYTES = 15 * 1024 * 1024
MAX_REQUEST_BYTES = 4 * ((MAX_FILE_BYTES + 2) // 3) + 32 * 1024
MAX_LOGIN_REQUEST_BYTES = 8192
FILE_SUFFIXES = {'.pdf', '.docx', '.html', '.htm', '.txt'}
REVIEW_SECTIONS = ('Périmètre', 'Faits', 'Règles ou sources', 'Question',
                   'Réponse ou raisonnement', 'Famille ou catégorie', 'Versions FR/EN')
REVIEW_OUTCOMES = ('Confirmer la référence', 'Demander une correction', 'Signaler un écart FR/EN')
ASSETS = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/styles.css': ('styles.css', 'text/css; charset=utf-8'),
    '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
}


def text_field(data, key, label, limit, *, required=True):
    value = data.get(key, '')
    if not isinstance(value, str):
        raise ValueError(f'{label} est invalide.')
    value = value.strip()
    if (required and not value) or len(value) > limit:
        raise ValueError(f'{label} doit contenir entre 1 et {limit} caractères.' if required
                         else f'{label} est limité à {limit} caractères.')
    return value


def choice(data, key, choices):
    value = data.get(key)
    if not isinstance(value, str) or value not in choices:
        raise ValueError('Vérifiez les options sélectionnées.')
    return value


def source_fields(data):
    mode = choice(data, 'mode', ('file', 'url'))
    fields = {
        'mode': mode,
        'title': text_field(data, 'title', 'Le titre', 200),
        'issuer': text_field(data, 'issuer', 'L’organisme émetteur', 120),
        'language': choice(data, 'language', ('fr', 'en', 'other')),
        'version': text_field(data, 'version', 'La date ou version', 100, required=False),
        'locator': text_field(data, 'locator', 'Le passage', 240, required=False),
    }
    if mode == 'url':
        url = text_field(data, 'url', 'L’URL', 2048)
        parts = urlsplit(url)
        if (parts.scheme not in ('http', 'https') or not parts.hostname or parts.username is not None
                or any(char.isspace() for char in url) or '\\' in url):
            raise ValueError('Saisissez une URL HTTP ou HTTPS valide.')
        return fields | {'url': url}, None

    filename = text_field(data, 'filename', 'Le nom du document', 240)
    if any(char in '/\\' or ord(char) < 32 for char in filename):
        raise ValueError('Le nom du fichier est invalide.')
    if Path(filename).suffix.lower() not in FILE_SUFFIXES:
        raise ValueError('Formats acceptés · PDF, DOCX, HTML et TXT.')
    encoded = data.get('content')
    if not isinstance(encoded, str):
        raise ValueError('Sélectionnez un document.')
    content = base64.b64decode(encoded, validate=True)
    if not 0 < len(content) <= MAX_FILE_BYTES:
        raise ValueError('Le fichier doit être non vide et ne pas dépasser 15 Mio.')
    return fields | {'filename': filename}, content


def public_origin(value):
    try:
        parts = urlsplit(value)
        if (parts.scheme == 'https' and parts.hostname and parts.username is None
                and parts.path in ('', '/') and not parts.query and not parts.fragment
                and not any(char.isspace() or char == '\\' for char in value)
                and (parts.port is None or 0 < parts.port <= 65535)):
            return 'https://' + parts.netloc.lower().removesuffix(':443')
    except ValueError:
        pass
    raise argparse.ArgumentTypeError('Indiquez une origine HTTPS sans chemin, par exemple https://corpus.example.org.')


class Contributions:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect(path)) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS contributions (
                    id INTEGER PRIMARY KEY,
                    kind TEXT NOT NULL,
                    reference TEXT,
                    created_at TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    content BLOB
                );
                CREATE UNIQUE INDEX IF NOT EXISTS proposal_code
                ON contributions(reference) WHERE kind = 'typology';

                CREATE TABLE IF NOT EXISTS contribution_transitions (
                    id INTEGER PRIMARY KEY,
                    contribution_id INTEGER NOT NULL REFERENCES contributions(id),
                    action TEXT NOT NULL CHECK (action IN ('withdrawn', 'restored')),
                    occurred_at TEXT NOT NULL,
                    reason TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS contribution_transition_order
                ON contribution_transitions(contribution_id, id DESC);

                CREATE VIEW IF NOT EXISTS active_contributions AS
                SELECT c.* FROM contributions AS c
                WHERE (
                    SELECT t.action FROM contribution_transitions AS t
                    WHERE t.contribution_id = c.id ORDER BY t.id DESC LIMIT 1
                ) IS NOT 'withdrawn';
            """)
            db.execute('BEGIN IMMEDIATE')
            for table in ('contributions', 'contribution_transitions'):
                if not any(column[1] == 'user_id' for column in db.execute(f'PRAGMA table_info({table})')):
                    db.execute(f'ALTER TABLE {table} ADD COLUMN user_id INTEGER REFERENCES users(id)')

    def add(self, user, kind, fields, reference=None, content=None):
        created_at = datetime.now(timezone.utc).isoformat()
        with closing(connect(self.path)) as db, db:
            entry = db.execute("""
                INSERT INTO contributions(kind, reference, created_at, metadata, content, user_id)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (kind, reference, created_at, json.dumps(fields, ensure_ascii=False), content, user['id']))
            return fields | {'id': entry.lastrowid, 'created_at': created_at, 'can_edit': True}

    def list(self, user):
        records = {'source': [], 'review': [], 'typology': []}
        with closing(connect(self.path)) as db:
            for id_, kind, date, metadata, owner in db.execute("""
                SELECT id, kind, created_at, metadata, user_id FROM active_contributions ORDER BY id DESC
            """):
                records[kind].append(json.loads(metadata) | {
                    'id': id_, 'created_at': date, 'can_edit': manageable(user, owner),
                })
        return records

    def transition(self, user, id_, action, reason):
        with closing(connect(self.path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("""
                SELECT kind, created_at, metadata, user_id,
                    EXISTS(SELECT 1 FROM active_contributions AS active WHERE active.id = c.id)
                FROM contributions AS c WHERE id = ?
            """, (id_,)).fetchone()
            if row is None:
                raise LookupError('Contribution introuvable.')
            kind, created_at, metadata, owner, was_active = row
            if not manageable(user, owner):
                raise PermissionError('Votre compte ne peut pas modifier cette contribution.')
            active = action == 'restored'
            if active != bool(was_active):
                db.execute("""
                    INSERT INTO contribution_transitions(contribution_id, action, occurred_at, reason, user_id)
                    VALUES (?, ?, ?, ?, ?)
                """, (id_, action, datetime.now(timezone.utc).isoformat(), reason, user['id']))
        return {'kind': kind, 'active': active,
                'contribution': json.loads(metadata) | {'id': id_, 'created_at': created_at, 'can_edit': True}}

    def history(self, user):
        with closing(connect(self.path)) as db:
            events = db.execute("""
                WITH events AS (
                    SELECT id AS contribution_id, id AS event_id, 'added' AS action,
                        created_at AS occurred_at, '' AS reason, user_id
                    FROM contributions
                    UNION ALL
                    SELECT contribution_id, id, action, occurred_at, reason, user_id
                    FROM contribution_transitions
                )
                SELECT c.id, c.kind, c.created_at, c.metadata, c.user_id, e.action, e.occurred_at, e.reason,
                    u.display_name,
                    active.id IS NOT NULL AS active,
                    e.action = 'withdrawn' AND e.event_id = (
                        SELECT t.id FROM contribution_transitions AS t
                        WHERE t.contribution_id = c.id ORDER BY t.id DESC LIMIT 1
                    ) AS can_restore
                FROM events AS e
                JOIN contributions AS c ON c.id = e.contribution_id
                LEFT JOIN active_contributions AS active ON active.id = c.id
                LEFT JOIN users AS u ON u.id = e.user_id
                ORDER BY e.occurred_at DESC, e.action != 'added' DESC, e.event_id DESC
            """).fetchall()
        return [{
            'kind': kind, 'action': action, 'occurred_at': occurred_at, 'reason': reason, 'username': name,
            'active': bool(active), 'can_restore': bool(can_restore) and manageable(user, owner),
            'contribution': json.loads(metadata) | {
                'id': id_, 'created_at': created_at, 'can_edit': manageable(user, owner),
            },
        } for id_, kind, created_at, metadata, owner, action, occurred_at, reason, name, active, can_restore in events]

    def document(self, id_):
        with closing(connect(self.path)) as db:
            row = db.execute("""
                SELECT metadata, content FROM active_contributions
                WHERE id = ? AND kind = 'source' AND content IS NOT NULL
            """, (id_,)).fetchone()
        return (json.loads(row[0])['filename'], row[1]) if row else None


class ContributorHandler(BaseHTTPRequestHandler):
    def send_bytes(self, data, mime, status=HTTPStatus.OK, headers=()):
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy',
                         "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, data, status=HTTPStatus.OK, headers=()):
        encoded = json.dumps(data, ensure_ascii=False).encode()
        self.send_bytes(encoded, 'application/json; charset=utf-8', status, headers)

    def allowed_request(self):
        port = self.server.server_port
        host = self.headers.get('Host', '').lower()
        origin = self.headers.get('Origin')
        public = self.server.public_origin
        if public:
            allowed = host == urlsplit(public).netloc and origin in (None, public)
        else:
            allowed = host in {f'127.0.0.1:{port}', f'localhost:{port}'} and origin in (None, f'http://{host}')
        if not allowed:
            self.send_json({'error': 'Adresse de connexion non autorisée.'}, HTTPStatus.FORBIDDEN)
        return allowed

    def session_cookie(self, token, age):
        name = self.server.session_cookie_name
        secure = '; Secure' if self.server.public_origin else ''
        return ('Set-Cookie', f'{name}={token}; Path=/; Max-Age={age}; HttpOnly; SameSite=Strict{secure}')

    def session_token(self):
        try:
            cookies = SimpleCookie(self.headers.get('Cookie', ''))
            cookie = cookies.get(self.server.session_cookie_name)
            if cookie and re.fullmatch(r'[A-Za-z0-9_-]{43}', cookie.value):
                return cookie.value
        except CookieError:
            pass
        return None

    def require_login(self):
        self.send_json({'error': 'Connectez-vous pour accéder au corpus.'}, HTTPStatus.UNAUTHORIZED)

    def do_GET(self):
        if not self.allowed_request():
            return
        route = urlsplit(self.path).path
        if route in ASSETS:
            filename, mime = ASSETS[route]
            self.send_bytes((APP_DIR / filename).read_bytes(), mime)
            return
        if route == '/favicon.ico':
            self.send_bytes(b'', 'image/x-icon', HTTPStatus.NO_CONTENT)
            return
        user = self.server.accounts.session_user(self.session_token())
        if route == '/api/session':
            self.send_json({'user': user})
        elif user is None:
            self.require_login()
        elif route == '/api/catalog':
            self.send_json(self.server.catalog)
        elif route == '/api/contributions':
            self.send_json(self.server.contributions.list(user))
        elif route == '/api/history':
            self.send_json(self.server.contributions.history(user))
        elif route.startswith('/api/cases/') and (case := self.server.cases.get(route.split('/')[-1])):
            self.send_json(case)
        elif re.fullmatch(r'/documents/[0-9]{1,18}', route) and (
            document := self.server.contributions.document(int(route.split('/')[-1]))
        ):
            name, content = document
            self.send_bytes(content, 'application/octet-stream', headers=(
                ('Content-Disposition', f"attachment; filename*=UTF-8''{quote(name, safe='')}"),
            ))
        else:
            self.send_json({'error': 'Page introuvable.'}, HTTPStatus.NOT_FOUND)

    def do_POST(self):
        if not self.allowed_request():
            return
        kind = {'/api/sources': 'source', '/api/reviews': 'review', '/api/typologies': 'typology',
                '/api/contributions/state': 'transition', '/api/login': 'login',
                '/api/logout': 'logout'}.get(urlsplit(self.path).path)
        if kind is None:
            self.send_json({'error': 'Page introuvable.'}, HTTPStatus.NOT_FOUND)
            return
        if self.headers.get_content_type() != 'application/json':
            self.send_json({'error': 'Format de requête invalide.'}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return
        try:
            token = self.session_token()
            if kind not in ('login', 'logout'):
                user = self.server.accounts.session_user(token)
                if user is None:
                    self.require_login()
                    return
            size = int(self.headers.get('Content-Length', '0'))
            limit = MAX_LOGIN_REQUEST_BYTES if kind in ('login', 'logout') else MAX_REQUEST_BYTES
            if size > limit:
                self.send_json({'error': 'La requête dépasse la taille autorisée.'}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                return
            if size < 1:
                raise ValueError('La contribution est vide.')
            try:
                data = json.loads(self.rfile.read(size))
            except ValueError:
                raise ValueError('Le formulaire est illisible.') from None
            if not isinstance(data, dict):
                raise ValueError('Le formulaire est invalide.')
            if kind == 'login':
                self.login(data, token)
                return
            if kind == 'logout':
                self.server.accounts.logout(token)
                self.send_json({'user': None}, headers=(self.session_cookie('', 0),))
                return
            record = self.record_contribution(user, kind, data)
        except LoginLimited as error:
            self.send_json({'error': 'Trop de tentatives de connexion. Réessayez plus tard.'},
                           HTTPStatus.TOO_MANY_REQUESTS, (('Retry-After', str(error.seconds)),))
        except PermissionError as error:
            self.send_json({'error': str(error)}, HTTPStatus.FORBIDDEN)
        except ValueError as error:
            self.send_json({'error': str(error)}, HTTPStatus.BAD_REQUEST)
        except LookupError as error:
            self.send_json({'error': str(error)}, HTTPStatus.NOT_FOUND)
        except sqlite3.IntegrityError:
            self.send_json({'error': 'Une proposition utilise déjà ce code. Retrouvez-la dans l’historique.'},
                           HTTPStatus.CONFLICT)
        except sqlite3.Error:
            self.log_error('Contribution storage unavailable')
            self.send_json({'error': 'L’enregistrement est indisponible. Réessayez.'}, HTTPStatus.SERVICE_UNAVAILABLE)
        else:
            self.send_json(record, HTTPStatus.OK if kind == 'transition' else HTTPStatus.CREATED)

    def login(self, data, token):
        session = self.server.accounts.login(data.get('email'), data.get('password'), token)
        if session is None:
            self.send_json({'error': 'E-mail ou mot de passe incorrect.'}, HTTPStatus.UNAUTHORIZED)
            return
        user, new_token = session
        self.send_json({'user': user}, headers=(self.session_cookie(new_token, SESSION_SECONDS),))

    def record_contribution(self, user, kind, data):
        storage = self.server.contributions
        if kind == 'transition':
            id_ = data.get('id')
            if type(id_) is not int or not 0 < id_ < 2**63:
                raise ValueError('Identifiant de contribution invalide.')
            action = choice(data, 'action', ('withdrawn', 'restored'))
            reason = text_field(data, 'reason', 'Le motif', 1000) if action == 'withdrawn' else ''
            return storage.transition(user, id_, action, reason)
        if kind == 'source':
            fields, content = source_fields(data)
            return storage.add(user, kind, fields, content=content)
        if kind == 'review':
            case_id = text_field(data, 'case', 'Le scénario', 100)
            case = self.server.cases.get(case_id)
            if not case or data.get('reference') != case['reference']:
                raise ValueError('La version du scénario a changé. Rouvrez la fiche pour la relire.')
            return storage.add(user, kind, {
                'case': case_id, 'dataset': case['dataset'], 'version': case['version'],
                'reference': case['reference'], 'language': choice(data, 'language', ('fr', 'en', 'both')),
                'section': choice(data, 'section', REVIEW_SECTIONS),
                'outcome': choice(data, 'outcome', REVIEW_OUTCOMES),
                'comment': text_field(data, 'comment', 'L’avis', 4000),
            })
        type_ = choice(data, 'type', ('family', 'category'))
        code = text_field(data, 'code', 'Le code', 12).upper()
        if not re.fullmatch(r'[A-Z][A-Z0-9-]{1,11}', code):
            raise ValueError('Le code doit compter de 2 à 12 lettres, chiffres ou tirets et commencer par une lettre.')
        if code in (FAMILIES if type_ == 'family' else CATEGORIES):
            raise ValueError('Ce code figure déjà dans le référentiel.')
        return storage.add(user, kind, {
            'type': type_, 'code': code, 'label': text_field(data, 'label', 'Le libellé', 120),
            'definition': text_field(data, 'definition', 'La définition', 1500),
            'example': text_field(data, 'example', 'L’exemple', 1500, required=type_ == 'category'),
        }, reference=f'{type_}/{code}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8503)
    parser.add_argument('--store', type=Path,
                        default=ROOT / 'artifacts' / 'contributions' / 'contributions.sqlite3')
    parser.add_argument('--public-origin', type=public_origin)
    parser.add_argument('--create-user', metavar='EMAIL')
    parser.add_argument('--name', help='Nom affiché dans l’historique pour le compte créé.')
    parser.add_argument('--admin', action='store_true', help='Créer un compte administrateur.')
    args = parser.parse_args()
    if args.create_user and not args.name:
        parser.error('--create-user requiert --name.')
    if not args.create_user and (args.name or args.admin):
        parser.error('--name et --admin s’utilisent avec --create-user.')
    if args.create_user:
        try:
            password = getpass('Mot de passe : ')
            if password != getpass('Confirmer le mot de passe : '):
                raise ValueError('Les mots de passe diffèrent.')
            Accounts(args.store).add(args.create_user, args.name, password, admin=args.admin)
        except sqlite3.IntegrityError:
            parser.exit(1, 'Un compte utilise déjà cette adresse e-mail.\n')
        except ValueError as error:
            parser.exit(1, f'{error}\n')
        print('Compte créé.')
        return
    try:
        cases, catalog = load_catalog()
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f'Impossible de charger les jeux de données acceptés · {error}\n')
    with ThreadingHTTPServer(('127.0.0.1', args.port), ContributorHandler) as server:
        server.public_origin = args.public_origin
        server.session_cookie_name = '__Host-corpus_session' if args.public_origin else 'corpus_session'
        server.cases = cases
        server.catalog = catalog | {'review_sections': REVIEW_SECTIONS, 'review_outcomes': REVIEW_OUTCOMES}
        server.accounts = Accounts(args.store)
        server.contributions = Contributions(args.store)
        origin = args.public_origin or f'http://127.0.0.1:{server.server_port}'
        print(f'Corpus LCB-FT · {origin}', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
