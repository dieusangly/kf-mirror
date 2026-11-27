"""Validate reviews, uploaded sources, typology proposals and transitions."""
import base64
from pathlib import Path
import re
from urllib.parse import urlsplit

from .catalog import CATEGORIES, FAMILIES


MAX_FILE_BYTES = 15 * 1024 * 1024
MAX_REQUEST_BYTES = 4 * ((MAX_FILE_BYTES + 2) // 3) + 32 * 1024
MAX_LOGIN_REQUEST_BYTES = 8192
FILE_SUFFIXES = {'.pdf', '.docx', '.html', '.htm', '.txt'}
REVIEW_SECTIONS = ('Périmètre', 'Faits', 'Règles ou sources', 'Question',
                   'Réponse ou raisonnement', 'Famille ou catégorie', 'Versions FR/EN')
REVIEW_OUTCOMES = ('Confirmer la référence', 'Demander une correction', 'Signaler un écart FR/EN')


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


def record_contribution(storage, cases, user, kind, data):
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
        case = cases.get(case_id)
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
