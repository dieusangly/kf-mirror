"""Run the local corpus contributor workspace."""

import argparse
from getpass import getpass
from http.cookies import CookieError, SimpleCookie
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import quote, urlsplit

from knowledge_foundry.shared.paths import REPOSITORY_ROOT as ROOT
from knowledge_foundry.storage.accounts import Accounts, LoginLimited, SESSION_SECONDS, connect, manageable
from knowledge_foundry.storage.contributions import Contributions
from .catalog import CATEGORIES, FAMILIES, load_catalog
from .submissions import (FILE_SUFFIXES, MAX_FILE_BYTES, choice, source_fields, text_field,
                          MAX_LOGIN_REQUEST_BYTES, MAX_REQUEST_BYTES, REVIEW_OUTCOMES,
                          REVIEW_SECTIONS, record_contribution)

APP_DIR = ROOT / 'apps/contributors'
ASSETS = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/styles.css': ('styles.css', 'text/css; charset=utf-8'),
    '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
}


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
        return record_contribution(self.server.contributions, self.server.cases, user, kind, data)


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
