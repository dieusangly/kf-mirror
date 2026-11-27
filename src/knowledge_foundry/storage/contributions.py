"""Persist contributor submissions, attachment bytes and transition history."""
from contextlib import closing
from datetime import datetime, timezone
import json

from .accounts import connect, manageable


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
