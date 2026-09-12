#!/usr/bin/env python3

import argparse
import bisect
import csv
import datetime
import glob
import io
import os
import re
import signal
import sqlite3
import subprocess
import sys
import time

from flask import Flask, Response, g, jsonify, request, send_from_directory

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.join(SCRIPT_DIR, 'data', 'DBs')
DB_PATH = os.path.join(DB_DIR, 'bluetooth.db')
SETTINGS_DB_PATH = os.path.join(DB_DIR, 'settings.db')
HISTORIES_DB_PATH = os.path.join(DB_DIR, 'bluetooth_histories.db')
SETTINGS_BASELINE_DB_PATH = os.path.join(DB_DIR, 'baseline', 'settings_baseline.db')
BLUEZ_MONITOR_PATH = os.path.join(SCRIPT_DIR, 'bluez_monitor.py')
ASSETS_DIR = os.path.join(SCRIPT_DIR, 'data', 'assets')
DEBUG_MODE = False
MONITOR_STOP_TIMEOUT_SECONDS = 20
MONITOR_FORCE_STOP_GRACE_SECONDS = 5
MONITOR_POLL_INTERVAL_SECONDS = 0.2
DEFAULT_INTERFACE = 'hci0'
ALLOWED_INTERFACES = ('hci0', 'hci1')
DEFAULT_SCAN_MODE = 'auto'
ALLOWED_SCAN_MODES = ('bredr', 'le', 'auto')
DEFAULT_AUTO_START = True
DEFAULT_CONNECT_TIMEOUT_SECONDS = 3
MIN_CONNECT_TIMEOUT_SECONDS = 3
MAX_CONNECT_TIMEOUT_SECONDS = 300
DEFAULT_AUTO_CONNECT_LATEST = False
DEFAULT_AUTO_CONNECT_LATEST_INTERVAL_SECONDS = 3
MIN_AUTO_CONNECT_LATEST_INTERVAL_SECONDS = 1
MAX_AUTO_CONNECT_LATEST_INTERVAL_SECONDS = 300
DEFAULT_PERIODIC_RESTART = True
DEFAULT_PERIODIC_RESTART_INTERVAL_MINUTES = 60
MIN_PERIODIC_RESTART_INTERVAL_MINUTES = 5
MAX_PERIODIC_RESTART_INTERVAL_MINUTES = 1440
DEFAULT_COLUMN_ORDER = ('count', 'rssi', 'uuid', 'first_seen', 'last_seen', 'mac', 'type', 'tags', 'service_data')
DEFAULT_HIDDEN_COLUMNS = ('mac', 'service_data')
ALLOWED_COLUMNS = set(DEFAULT_COLUMN_ORDER)
DEFAULT_COLUMN_WIDTHS = {
    'count': 50, 'rssi': 50, 'uuid': 50,
    'first_seen': 110, 'last_seen': 110,
    'mac': 130, 'type': 65, 'tags': 160, 'service_data': 90,
}
MIN_COLUMN_WIDTH = 24
MAX_COLUMN_WIDTH = 400
DEFAULT_THEME_COLORS = {
    'bg': '#0b0f14',
    'surface': '#16202b',
    'border': '#2a3a4a',
    'text': '#d8e1e8',
    'muted': '#7d93a8',
    'accent': '#4fd1c5',
    'success': '#6bcf7f',
    'danger': '#ff6b6b',
}
ALLOWED_THEME_COLOR_KEYS = set(DEFAULT_THEME_COLORS)
THEME_COLOR_HEX_RE = re.compile(r'^#[0-9a-fA-F]{6}$')
MAC_STYLE_ALIAS_RE = re.compile(r'^[0-9A-Fa-f]{2}(-[0-9A-Fa-f]{2}){5}$')
app = Flask(__name__)


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True, timeout=30)
    return g.db


def get_histories_db():
    if 'histories_db' not in g:
        g.histories_db = sqlite3.connect(f'file:{HISTORIES_DB_PATH}?mode=ro', uri=True, timeout=30)
    return g.histories_db


def _histories_tables_exist(histories_db):
    return histories_db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='device_alias_history'"
    ).fetchone() is not None


def _create_settings_schema(db):
    db.execute('''
        CREATE TABLE IF NOT EXISTS tags (
            tag_id     INTEGER PRIMARY KEY,
            name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
            created_at TEXT NOT NULL
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS device_tags (
            address    TEXT NOT NULL,
            tag_id     INTEGER NOT NULL REFERENCES tags(tag_id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            PRIMARY KEY (address, tag_id)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS monitor_status (
            id         INTEGER PRIMARY KEY CHECK (id = 1),
            running    INTEGER NOT NULL DEFAULT 0,
            pid        INTEGER,
            updated_at TEXT NOT NULL
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS interface_config (
            id         INTEGER PRIMARY KEY CHECK (id = 1),
            interface  TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''')
    db.execute(
        'INSERT OR IGNORE INTO interface_config (id, interface, updated_at) VALUES (1, ?, ?)',
        (DEFAULT_INTERFACE, datetime.datetime.now(datetime.timezone.utc).isoformat()),
    )
    db.execute('''
        CREATE TABLE IF NOT EXISTS scan_mode_config (
            id         INTEGER PRIMARY KEY CHECK (id = 1),
            scan_mode  TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''')
    db.execute(
        'INSERT OR IGNORE INTO scan_mode_config (id, scan_mode, updated_at) VALUES (1, ?, ?)',
        (DEFAULT_SCAN_MODE, datetime.datetime.now(datetime.timezone.utc).isoformat()),
    )
    db.execute('''
        CREATE TABLE IF NOT EXISTS auto_start_config (
            id         INTEGER PRIMARY KEY CHECK (id = 1),
            enabled    INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''')
    db.execute(
        'INSERT OR IGNORE INTO auto_start_config (id, enabled, updated_at) VALUES (1, ?, ?)',
        (1 if DEFAULT_AUTO_START else 0, datetime.datetime.now(datetime.timezone.utc).isoformat()),
    )
    db.execute('''
        CREATE TABLE IF NOT EXISTS connect_timeout_config (
            id              INTEGER PRIMARY KEY CHECK (id = 1),
            timeout_seconds INTEGER NOT NULL,
            updated_at      TEXT NOT NULL
        )
    ''')
    db.execute(
        'INSERT OR IGNORE INTO connect_timeout_config (id, timeout_seconds, updated_at) VALUES (1, ?, ?)',
        (DEFAULT_CONNECT_TIMEOUT_SECONDS, datetime.datetime.now(datetime.timezone.utc).isoformat()),
    )
    db.execute('''
        CREATE TABLE IF NOT EXISTS auto_connect_latest_config (
            id         INTEGER PRIMARY KEY CHECK (id = 1),
            enabled    INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''')
    db.execute(
        'INSERT OR IGNORE INTO auto_connect_latest_config (id, enabled, updated_at) VALUES (1, ?, ?)',
        (1 if DEFAULT_AUTO_CONNECT_LATEST else 0, datetime.datetime.now(datetime.timezone.utc).isoformat()),
    )
    db.execute('''
        CREATE TABLE IF NOT EXISTS auto_connect_latest_interval_config (
            id               INTEGER PRIMARY KEY CHECK (id = 1),
            interval_seconds INTEGER NOT NULL,
            updated_at       TEXT NOT NULL
        )
    ''')
    db.execute(
        'INSERT OR IGNORE INTO auto_connect_latest_interval_config (id, interval_seconds, updated_at) VALUES (1, ?, ?)',
        (DEFAULT_AUTO_CONNECT_LATEST_INTERVAL_SECONDS, datetime.datetime.now(datetime.timezone.utc).isoformat()),
    )
    db.execute('''
        CREATE TABLE IF NOT EXISTS periodic_restart_config (
            id         INTEGER PRIMARY KEY CHECK (id = 1),
            enabled    INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''')
    db.execute(
        'INSERT OR IGNORE INTO periodic_restart_config (id, enabled, updated_at) VALUES (1, ?, ?)',
        (1 if DEFAULT_PERIODIC_RESTART else 0, datetime.datetime.now(datetime.timezone.utc).isoformat()),
    )
    db.execute('''
        CREATE TABLE IF NOT EXISTS periodic_restart_interval_config (
            id               INTEGER PRIMARY KEY CHECK (id = 1),
            interval_minutes INTEGER NOT NULL,
            updated_at       TEXT NOT NULL
        )
    ''')
    db.execute(
        'INSERT OR IGNORE INTO periodic_restart_interval_config (id, interval_minutes, updated_at) VALUES (1, ?, ?)',
        (DEFAULT_PERIODIC_RESTART_INTERVAL_MINUTES, datetime.datetime.now(datetime.timezone.utc).isoformat()),
    )
    db.execute('''
        CREATE TABLE IF NOT EXISTS column_config (
            column_key TEXT PRIMARY KEY,
            position   INTEGER NOT NULL,
            visible    INTEGER NOT NULL,
            width      INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''')
    _ensure_column_width_column(db)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for position, key in enumerate(DEFAULT_COLUMN_ORDER):
        db.execute(
            'INSERT OR IGNORE INTO column_config (column_key, position, visible, width, updated_at) '
            'VALUES (?, ?, ?, ?, ?)',
            (key, position, 0 if key in DEFAULT_HIDDEN_COLUMNS else 1, DEFAULT_COLUMN_WIDTHS[key], now),
        )
    db.execute('''
        CREATE TABLE IF NOT EXISTS theme_config (
            color_key   TEXT PRIMARY KEY,
            color_value TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )
    ''')
    for key, value in DEFAULT_THEME_COLORS.items():
        db.execute(
            'INSERT OR IGNORE INTO theme_config (color_key, color_value, updated_at) VALUES (?, ?, ?)',
            (key, value, now),
        )
    _seed_reference_tables_if_needed(db)
    db.commit()


def _seed_reference_tables_if_needed(db):
    db.execute('''
        CREATE TABLE IF NOT EXISTS manufacturerdata_names (
            manufacturerdata_id INTEGER PRIMARY KEY,
            category_id          INTEGER REFERENCES categories(category_id) ON DELETE SET NULL,
            name                 TEXT NOT NULL,
            icon                 TEXT,
            color                TEXT CHECK (color IN ('RED','YELLOW','GREEN','PINK'))
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS categories (
            category_id INTEGER PRIMARY KEY,
            name        TEXT NOT NULL UNIQUE,
            description TEXT,
            icon        TEXT,
            color       TEXT NOT NULL CHECK (color IN ('RED','YELLOW','GREEN','PINK'))
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS ouids (
            ouid_id     INTEGER PRIMARY KEY,
            category_id INTEGER REFERENCES categories(category_id) ON DELETE SET NULL,
            oui         TEXT NOT NULL,
            org_name    TEXT NOT NULL
        )
    ''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_ouids_oui ON ouids(oui)')
    db.execute('CREATE INDEX IF NOT EXISTS idx_ouids_category ON ouids(category_id)')
    db.execute('''
        CREATE TABLE IF NOT EXISTS uuid_lookups (
            uuid_lookup_id INTEGER PRIMARY KEY,
            uuid           TEXT NOT NULL UNIQUE,
            name           TEXT NOT NULL,
            type           TEXT NOT NULL CHECK (type IN ('vendor','info')),
            icon           TEXT
        )
    ''')
    _ensure_uuid_lookup_icon_column(db)
    _ensure_manufacturerdata_category_column(db)
    already_seeded = db.execute('SELECT 1 FROM categories LIMIT 1').fetchone() is not None
    if already_seeded:
        return
    db.execute('ATTACH DATABASE ? AS ref', (SETTINGS_BASELINE_DB_PATH,))
    try:
        db.execute('INSERT OR IGNORE INTO categories SELECT * FROM ref.categories')
        if _attached_table_has_column(db, 'ref', 'manufacturerdata_names', 'category_id'):
            db.execute(
                'INSERT OR IGNORE INTO manufacturerdata_names '
                '(manufacturerdata_id, category_id, name, icon, color) '
                'SELECT manufacturerdata_id, category_id, name, icon, color FROM ref.manufacturerdata_names'
            )
        else:
            db.execute(
                'INSERT OR IGNORE INTO manufacturerdata_names (manufacturerdata_id, name, icon, color) '
                'SELECT manufacturerdata_id, name, icon, color FROM ref.manufacturerdata_names'
            )
        db.execute('INSERT OR IGNORE INTO ouids SELECT * FROM ref.ouids')
        db.execute('INSERT OR IGNORE INTO uuid_lookups SELECT * FROM ref.uuid_lookups')
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.execute('DETACH DATABASE ref')


def _attached_table_has_column(conn, schema, table, column):
    return any(
        row[1] == column
        for row in conn.execute(f'PRAGMA {schema}.table_info({table})').fetchall()
    )


def _ensure_uuid_lookup_icon_column(db):
    existing_columns = {row[1] for row in db.execute('PRAGMA table_info(uuid_lookups)').fetchall()}
    if not existing_columns or 'icon' in existing_columns:
        return
    db.execute('ALTER TABLE uuid_lookups ADD COLUMN icon TEXT')
    db.execute('ATTACH DATABASE ? AS ref', (SETTINGS_BASELINE_DB_PATH,))
    try:
        db.execute('''
            UPDATE uuid_lookups SET icon = (
                SELECT r.icon FROM ref.uuid_lookups AS r
                WHERE r.uuid = uuid_lookups.uuid
            )
        ''')
        db.commit()
    finally:
        db.execute('DETACH DATABASE ref')


def _ensure_manufacturerdata_category_column(db):
    existing_columns = {row[1] for row in db.execute('PRAGMA table_info(manufacturerdata_names)').fetchall()}
    if not existing_columns or 'category_id' in existing_columns:
        return
    db.execute('ALTER TABLE manufacturerdata_names ADD COLUMN category_id INTEGER REFERENCES categories(category_id) ON DELETE SET NULL')
    db.execute('ATTACH DATABASE ? AS ref', (SETTINGS_BASELINE_DB_PATH,))
    try:
        if _attached_table_has_column(db, 'ref', 'manufacturerdata_names', 'category_id'):
            db.execute('''
                UPDATE manufacturerdata_names SET category_id = (
                    SELECT r.category_id FROM ref.manufacturerdata_names AS r
                    WHERE r.manufacturerdata_id = manufacturerdata_names.manufacturerdata_id
                )
            ''')
            db.commit()
    finally:
        db.execute('DETACH DATABASE ref')


def _ensure_column_width_column(db):
    existing_columns = {row[1] for row in db.execute('PRAGMA table_info(column_config)').fetchall()}
    if not existing_columns or 'width' in existing_columns:
        return
    db.execute('ALTER TABLE column_config ADD COLUMN width INTEGER NOT NULL DEFAULT 80')
    for (key,) in db.execute('SELECT column_key FROM column_config').fetchall():
        db.execute(
            'UPDATE column_config SET width = ? WHERE column_key = ?',
            (DEFAULT_COLUMN_WIDTHS.get(key, 80), key),
        )


def _migrate_settings_schema_if_needed(db):
    tables = {row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    if 'tags' in tables or 'device_tags' not in tables:
        _create_settings_schema(db)
        return
    old_rows = db.execute('SELECT address, tag, created_at FROM device_tags').fetchall()
    db.execute('ALTER TABLE device_tags RENAME TO device_tags_old')
    _create_settings_schema(db)
    for address, tag, created_at in old_rows:
        tag_id = _get_or_create_tag_id(db, tag, created_at)
        db.execute(
            'INSERT OR IGNORE INTO device_tags (address, tag_id, created_at) VALUES (?, ?, ?)',
            (address, tag_id, created_at),
        )
    db.execute('DROP TABLE device_tags_old')
    db.commit()


def _get_or_create_tag_id(db, name, created_at=None):
    row = db.execute('SELECT tag_id FROM tags WHERE name = ?', (name,)).fetchone()
    if row:
        return row[0]
    cur = db.execute(
        'INSERT INTO tags (name, created_at) VALUES (?, ?)',
        (name, created_at or datetime.datetime.now(datetime.timezone.utc).isoformat()),
    )
    return cur.lastrowid


def get_settings_db():
    if 'settings_db' not in g:
        is_new = not os.path.exists(SETTINGS_DB_PATH)
        g.settings_db = sqlite3.connect(SETTINGS_DB_PATH)
        g.settings_db.execute('PRAGMA journal_mode = WAL')
        g.settings_db.execute('PRAGMA foreign_keys = ON')
        if is_new:
            _create_settings_schema(g.settings_db)
        else:
            _migrate_settings_schema_if_needed(g.settings_db)
    return g.settings_db


def _create_connections_schema(db):
    db.execute('''
        CREATE TABLE IF NOT EXISTS device_connections (
            address    TEXT PRIMARY KEY,
            state      TEXT NOT NULL CHECK (state IN ('connecting', 'connected', 'disconnected', 'error')),
            error      TEXT,
            updated_at TEXT NOT NULL
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS auto_connect_successes (
            address            TEXT PRIMARY KEY,
            first_connected_at TEXT NOT NULL,
            last_connected_at  TEXT NOT NULL
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS device_connect_requests (
            address      TEXT PRIMARY KEY,
            action       TEXT NOT NULL CHECK (action IN ('connect', 'disconnect')),
            requested_at TEXT NOT NULL
        )
    ''')
    db.commit()


def get_connections_db():
    if 'connections_db' not in g:
        g.connections_db = sqlite3.connect(HISTORIES_DB_PATH)
        g.connections_db.execute('PRAGMA journal_mode = WAL')
        _create_connections_schema(g.connections_db)
    return g.connections_db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()
    settings_db = g.pop('settings_db', None)
    if settings_db is not None:
        settings_db.close()
    connections_db = g.pop('connections_db', None)
    if connections_db is not None:
        connections_db.close()
    histories_db = g.pop('histories_db', None)
    if histories_db is not None:
        histories_db.close()


DEVICE_SERVICE_UUIDS_ALL_QUERY = '''
    SELECT ds.device_id, s.uuid, s.uuid_lookup_id
    FROM device_service_uuids ds
    JOIN service_uuids s ON s.uuid_id = ds.uuid_id
'''
DEVICE_SOLICITED_UUIDS_ALL_QUERY = '''
    SELECT dsol.device_id, s.uuid, s.uuid_lookup_id
    FROM device_solicited_uuids dsol
    JOIN service_uuids s ON s.uuid_id = dsol.uuid_id
'''
DEVICE_SERVICE_DATA_ALL_QUERY = '''
    SELECT dsd.device_id, s.uuid, dsd.data_hex
    FROM device_service_data dsd
    JOIN service_uuids s ON s.uuid_id = dsd.uuid_id
'''

BT_BASE_UUID_SUFFIX = '-0000-1000-8000-00805f9b34fb'
EDDYSTONE_URL_SCHEMES = ['http://www.', 'https://www.', 'http://', 'https://']
EDDYSTONE_URL_EXPANSIONS = [
    '.com/', '.org/', '.edu/', '.net/', '.info/', '.biz/', '.gov/',
    '.com', '.org', '.edu', '.net', '.info', '.biz', '.gov',
]


def _short_service_uuid(uuid):
    u = uuid.lower()
    if len(u) == 36 and u.startswith('0000') and u.endswith(BT_BASE_UUID_SUFFIX):
        return u[4:8].upper()
    return None


def _int8(b):
    return b - 256 if b > 127 else b


def _int16(b):
    return b - 65536 if b > 32767 else b


def _decode_eddystone_url(payload):
    return ''.join(
        EDDYSTONE_URL_EXPANSIONS[b] if b < len(EDDYSTONE_URL_EXPANSIONS) else chr(b)
        for b in payload
    )


def _decode_eddystone(payload):
    if not payload:
        return None
    frame_type = payload[0]
    if frame_type == 0x00 and len(payload) >= 18:
        tx_power = _int8(payload[1])
        return f'Tx power {tx_power} dBm, namespace {payload[2:12].hex()}, instance {payload[12:18].hex()}'
    if frame_type == 0x10 and len(payload) >= 3:
        tx_power = _int8(payload[1])
        scheme = EDDYSTONE_URL_SCHEMES[payload[2]] if payload[2] < len(EDDYSTONE_URL_SCHEMES) else ''
        return f'Tx power {tx_power} dBm, {scheme}{_decode_eddystone_url(payload[3:])}'
    if frame_type == 0x20 and len(payload) >= 14:
        battery_mv = (payload[2] << 8) | payload[3]
        temp = _int16((payload[4] << 8) | payload[5]) / 256
        adv_count = (payload[6] << 24) | (payload[7] << 16) | (payload[8] << 8) | payload[9]
        uptime_ds = (payload[10] << 24) | (payload[11] << 16) | (payload[12] << 8) | payload[13]
        return (
            f'battery {battery_mv} mV, temp {temp:.1f}°C, '
            f'{adv_count} adverts sent, uptime {(uptime_ds / 10):.1f}s'
        )
    if frame_type == 0x30:
        return f'encrypted ephemeral ID {payload[1:].hex()}'
    return None


def _bytes_to_ascii(payload):
    return ''.join(chr(b) if 0x20 <= b <= 0x7e else '·' for b in payload)


def _decode_service_data_text(uuid, data_hex):
    try:
        payload = bytes.fromhex(data_hex)
    except ValueError:
        return None
    short = _short_service_uuid(uuid)
    if short == 'FEAA':
        eddystone = _decode_eddystone(payload)
        if eddystone:
            return eddystone
    if short == 'FE2C' and len(payload) == 3:
        return f'Model ID {payload.hex()}'
    if short == '180F' and len(payload) == 1:
        return f'{payload[0]}%'
    if not payload:
        return None
    return _bytes_to_ascii(payload)


def _service_data_search_text_by_device(db):
    text_by_device = {}
    for device_id, uuid, data_hex in db.execute(DEVICE_SERVICE_DATA_ALL_QUERY).fetchall():
        parts = text_by_device.setdefault(device_id, [])
        parts.append(data_hex)
        decoded = _decode_service_data_text(uuid, data_hex)
        if decoded:
            parts.append(decoded)
    return {device_id: ' '.join(parts) for device_id, parts in text_by_device.items()}


def _uuid_lookup_names(settings_db, uuid_lookup_ids):
    uuid_lookup_ids = {i for i in uuid_lookup_ids if i is not None}
    if not uuid_lookup_ids:
        return {}
    placeholders = ','.join('?' * len(uuid_lookup_ids))
    return dict(settings_db.execute(
        f'SELECT uuid_lookup_id, name FROM uuid_lookups WHERE uuid_lookup_id IN ({placeholders})',
        list(uuid_lookup_ids),
    ).fetchall())


def _uuid_summaries_by_device(db, settings_db, query):
    rows = db.execute(query).fetchall()
    names_by_lookup_id = _uuid_lookup_names(settings_db, (lookup_id for _, _, lookup_id in rows))
    uuids_by_device = {}
    names_by_device = {}
    for device_id, uuid_value, lookup_id in rows:
        uuids_by_device.setdefault(device_id, []).append(uuid_value)
        name = names_by_lookup_id.get(lookup_id)
        if name is not None:
            names_by_device.setdefault(device_id, []).append(name)
    return {
        device_id: (
            ','.join(dict.fromkeys(uuids)),
            ','.join(dict.fromkeys(names_by_device.get(device_id, []))) or None,
            len(set(uuids)),
        )
        for device_id, uuids in uuids_by_device.items()
    }

DOTS_QUERY = '''
    SELECT
        d.device_id, d.address, d.alias, a.first_seen_at, a.last_seen_at, a.obs_count, a.rssi_last,
        d.address_type, d.manufacturerdata_id, d.manufacturerdata_name, d.appearance, d.bearer
    FROM device_appearances a
    JOIN devices d ON d.device_id = a.device_id
    {where_clause}
    ORDER BY d.address, a.first_seen_at
'''

POINTS_QUERY = '''
    SELECT
        d.device_id, d.address, d.alias, o.seen_at, o.rssi,
        d.address_type, d.manufacturerdata_id, d.manufacturerdata_name, d.appearance, d.bearer
    FROM raw_observations o
    JOIN devices d ON d.device_id = o.device_id
    {where_clause}
    ORDER BY d.address, o.seen_at
'''


def _tags_by_address():
    tags_by_address = {}
    query = '''
        SELECT dt.address, t.name
        FROM device_tags dt
        JOIN tags t ON t.tag_id = dt.tag_id
        ORDER BY dt.created_at
    '''
    for address, name in get_settings_db().execute(query).fetchall():
        tags_by_address.setdefault(address, []).append(name)
    return tags_by_address


def _service_data_bytes_by_address():
    if not os.path.exists(HISTORIES_DB_PATH):
        return {}
    histories_db = get_histories_db()
    if not _histories_tables_exist(histories_db):
        return {}
    bytes_by_device_id = dict(histories_db.execute(
        'SELECT device_id, SUM(LENGTH(data_hex) / 2) FROM device_service_data_history GROUP BY device_id'
    ).fetchall())
    if not bytes_by_device_id:
        return {}
    bytes_by_address = {}
    for device_id, address in get_db().execute('SELECT device_id, address FROM devices').fetchall():
        total = bytes_by_device_id.get(device_id)
        if total:
            bytes_by_address[address] = bytes_by_address.get(address, 0) + total
    return bytes_by_address


def _multi_alias_addresses():
    if not os.path.exists(HISTORIES_DB_PATH):
        return set()
    histories_db = get_histories_db()
    if not _histories_tables_exist(histories_db):
        return set()
    multi_device_ids = {
        device_id for (device_id,) in histories_db.execute(
            'SELECT device_id FROM device_alias_history GROUP BY device_id HAVING COUNT(DISTINCT alias) > 1'
        ).fetchall()
    }
    if not multi_device_ids:
        return set()
    return {
        address for device_id, address in get_db().execute('SELECT device_id, address FROM devices').fetchall()
        if device_id in multi_device_ids
    }


def _display_alias_by_device():
    if not os.path.exists(HISTORIES_DB_PATH):
        return {}
    histories_db = get_histories_db()
    if not _histories_tables_exist(histories_db):
        return {}
    display_alias_by_device = {}
    rows = histories_db.execute(
        'SELECT device_id, alias FROM device_alias_history ORDER BY device_id, last_seen_at DESC'
    ).fetchall()
    for device_id, alias in rows:
        if device_id in display_alias_by_device:
            continue
        if not MAC_STYLE_ALIAS_RE.match(alias or ''):
            display_alias_by_device[device_id] = alias
    return display_alias_by_device


def _alias_search_text_by_device():
    if not os.path.exists(HISTORIES_DB_PATH):
        return {}
    histories_db = get_histories_db()
    if not _histories_tables_exist(histories_db):
        return {}
    text_by_device = {}
    for device_id, alias in histories_db.execute(
        'SELECT DISTINCT device_id, alias FROM device_alias_history'
    ).fetchall():
        text_by_device.setdefault(device_id, []).append(alias)
    return {device_id: ' '.join(aliases) for device_id, aliases in text_by_device.items()}


def _classifications_by_device(db, settings_db, device_ids, manufacturerdata_id_by_device):
    device_ids = [d for d in device_ids if d is not None]
    if not device_ids:
        return {}
    id_placeholders = ','.join('?' * len(device_ids))

    ouid_matches = db.execute(
        f'SELECT device_id, ouid_id FROM device_ouid_matches WHERE device_id IN ({id_placeholders})',
        device_ids,
    ).fetchall()
    alias_matches = db.execute(
        f'SELECT device_id, category_id FROM device_alias_category_matches WHERE device_id IN ({id_placeholders})',
        device_ids,
    ).fetchall()

    ouids_by_id = {}
    referenced_ouid_ids = {ouid_id for _, ouid_id in ouid_matches}
    if referenced_ouid_ids:
        ouid_placeholders = ','.join('?' * len(referenced_ouid_ids))
        for ouid_id, category_id, oui, org_name in settings_db.execute(
            f'SELECT ouid_id, category_id, oui, org_name FROM ouids WHERE ouid_id IN ({ouid_placeholders})',
            list(referenced_ouid_ids),
        ).fetchall():
            ouids_by_id[ouid_id] = (category_id, oui, org_name)

    referenced_category_ids = {category_id for _, category_id in alias_matches} | {
        category_id for category_id, _, _ in ouids_by_id.values() if category_id is not None
    }
    categories_by_id = {}
    if referenced_category_ids:
        cat_placeholders = ','.join('?' * len(referenced_category_ids))
        for category_id, name, icon, color, description in settings_db.execute(
            f'SELECT category_id, name, icon, color, description FROM categories WHERE category_id IN ({cat_placeholders})',
            list(referenced_category_ids),
        ).fetchall():
            categories_by_id[category_id] = (name, icon, color, description)

    manufacturer_by_id = {}
    referenced_manufacturerdata_ids = {mid for mid in manufacturerdata_id_by_device.values() if mid is not None}
    if referenced_manufacturerdata_ids:
        mid_placeholders = ','.join('?' * len(referenced_manufacturerdata_ids))
        manufacturer_rows = settings_db.execute(
            f'SELECT manufacturerdata_id, name, icon, color, category_id FROM manufacturerdata_names '
            f'WHERE manufacturerdata_id IN ({mid_placeholders})',
            list(referenced_manufacturerdata_ids),
        ).fetchall()

        needs_fallback = [r for r in manufacturer_rows if r[2] is None]
        category_id_by_org_name = {}
        if needs_fallback:
            category_id_by_org_name = dict(settings_db.execute(
                'SELECT org_name, category_id FROM ouids WHERE category_id IS NOT NULL'
            ).fetchall())

        extra_category_ids = {
            r[4] for r in needs_fallback if r[4] is not None and r[4] not in categories_by_id
        } | {
            category_id_by_org_name[r[1]] for r in needs_fallback
            if r[1] in category_id_by_org_name and category_id_by_org_name[r[1]] not in categories_by_id
        }
        if extra_category_ids:
            extra_placeholders = ','.join('?' * len(extra_category_ids))
            for category_id, name, icon, color, description in settings_db.execute(
                f'SELECT category_id, name, icon, color, description FROM categories WHERE category_id IN ({extra_placeholders})',
                list(extra_category_ids),
            ).fetchall():
                categories_by_id[category_id] = (name, icon, color, description)

        for manufacturerdata_id, name, icon, color, category_id in manufacturer_rows:
            if icon is None and category_id is not None:
                category_info = categories_by_id.get(category_id)
                if category_info is not None:
                    _, icon, color, _ = category_info
            if icon is None:
                ouid_category_id = category_id_by_org_name.get(name)
                category_info = categories_by_id.get(ouid_category_id) if ouid_category_id is not None else None
                if category_info is not None:
                    _, icon, color, _ = category_info
            if icon is not None:
                manufacturer_by_id[manufacturerdata_id] = (name, icon, color)

    uuid_matches = db.execute(
        f'''
        SELECT device_id, uuid_lookup_id, uuid FROM (
            SELECT ds.device_id AS device_id, s.uuid_lookup_id AS uuid_lookup_id, s.uuid AS uuid
            FROM device_service_uuids ds
            JOIN service_uuids s ON s.uuid_id = ds.uuid_id
            WHERE ds.device_id IN ({id_placeholders})
            UNION
            SELECT dsol.device_id AS device_id, s.uuid_lookup_id AS uuid_lookup_id, s.uuid AS uuid
            FROM device_solicited_uuids dsol
            JOIN service_uuids s ON s.uuid_id = dsol.uuid_id
            WHERE dsol.device_id IN ({id_placeholders})
        )
        WHERE uuid_lookup_id IS NOT NULL
        ''',
        device_ids + device_ids,
    ).fetchall()
    uuid_lookup_by_id = {}
    referenced_uuid_lookup_ids = {uuid_lookup_id for _, uuid_lookup_id, _ in uuid_matches}
    if referenced_uuid_lookup_ids:
        uuid_placeholders = ','.join('?' * len(referenced_uuid_lookup_ids))
        for uuid_lookup_id, name, icon in settings_db.execute(
            f'SELECT uuid_lookup_id, name, icon FROM uuid_lookups '
            f'WHERE uuid_lookup_id IN ({uuid_placeholders}) AND icon IS NOT NULL',
            list(referenced_uuid_lookup_ids),
        ).fetchall():
            uuid_lookup_by_id[uuid_lookup_id] = (name, icon)

    rows_by_device = {device_id: [] for device_id in device_ids}
    for device_id, ouid_id in ouid_matches:
        ouid_info = ouids_by_id.get(ouid_id)
        if ouid_info is None:
            continue
        category_id, oui, org_name = ouid_info
        name, icon, color, description = categories_by_id.get(category_id, (None, None, None, None))
        rows_by_device[device_id].append({
            'oui': oui, 'org_name': org_name, 'category_id': category_id,
            'category_name': name, 'category_icon': icon, 'category_color': color,
            'category_description': description, 'source': 'ouid',
        })
    for device_id, category_id in alias_matches:
        category_info = categories_by_id.get(category_id)
        if category_info is None:
            continue
        name, icon, color, description = category_info
        rows_by_device[device_id].append({
            'oui': None, 'org_name': None, 'category_id': category_id,
            'category_name': name, 'category_icon': icon, 'category_color': color,
            'category_description': description, 'source': 'alias',
        })
    for device_id, manufacturerdata_id in manufacturerdata_id_by_device.items():
        entry = manufacturer_by_id.get(manufacturerdata_id)
        if entry is None or device_id not in rows_by_device:
            continue
        name, icon, color = entry
        rows_by_device[device_id].append({
            'oui': None, 'org_name': None, 'category_id': None,
            'category_name': name, 'category_icon': icon, 'category_color': color,
            'category_description': None, 'source': 'manufacturer',
            'manufacturer_id': manufacturerdata_id,
        })
    for device_id, uuid_lookup_id, uuid in uuid_matches:
        entry = uuid_lookup_by_id.get(uuid_lookup_id)
        if entry is None:
            continue
        name, icon = entry
        rows_by_device[device_id].append({
            'oui': None, 'org_name': None, 'category_id': None,
            'category_name': name, 'category_icon': icon, 'category_color': None,
            'category_description': None, 'source': 'uuid', 'uuid': uuid,
        })
    return rows_by_device


def _primary_category_color(rows):
    if not rows:
        return None
    best = min(rows, key=lambda r: (r['category_id'] is None, r['category_id'] or 0))
    return best['category_color']


_REGIONAL_INDICATOR_RANGE = range(0x1F1E6, 0x1F200)
_EMOJI_VARIATION_SELECTORS = (0xFE0E, 0xFE0F)
_EMOJI_SKIN_TONE_RANGE = range(0x1F3FB, 0x1F400)
_ZERO_WIDTH_JOINER = 0x200D


def _split_icon_glyphs(icon):
    glyphs = []
    cluster = ''
    prev_cp = None
    prev_is_regional_indicator = False
    for ch in icon:
        cp = ord(ch)
        is_regional_indicator = cp in _REGIONAL_INDICATOR_RANGE
        if not cluster:
            cluster = ch
        elif (prev_cp == _ZERO_WIDTH_JOINER or cp == _ZERO_WIDTH_JOINER
              or cp in _EMOJI_VARIATION_SELECTORS or cp in _EMOJI_SKIN_TONE_RANGE
              or (prev_is_regional_indicator and is_regional_indicator and len(cluster) == 1)):
            cluster += ch
        else:
            glyphs.append(cluster)
            cluster = ch
        prev_cp = cp
        prev_is_regional_indicator = is_regional_indicator
    if cluster:
        glyphs.append(cluster)
    return glyphs


def _icons_string(rows):
    glyph_category_ids = {}
    for r in rows:
        if r['category_icon']:
            for glyph in _split_icon_glyphs(r['category_icon']):
                glyph_category_ids.setdefault(glyph, []).append(r['category_id'])
    if not glyph_category_ids:
        return None

    def sort_key(glyph):
        category_ids = [c for c in glyph_category_ids[glyph] if c is not None]
        manufacturer_only = not category_ids
        return (manufacturer_only, min(category_ids) if category_ids else 0)

    return ','.join(sorted(glyph_category_ids, key=sort_key))


def _distinct_concat(rows, key):
    values = list(dict.fromkeys(r[key] for r in rows if r[key] is not None))
    return ','.join(values) if values else None


def _manufacturer_id_hex(manufacturer_id):
    return f'0x{manufacturer_id:04X}'


def _build_search_fields(address, alias, alias_history_text, address_type, manufacturer,
                          manufacturer_id_hex, manufacturerdata_id, oui, org_names,
                          category_names, category_descriptions, color,
                          service_uuids, service_uuid_names, solicited_uuids, solicited_uuid_names,
                          service_data_text, tags, bearer):
    manufacturer_id_text = ' '.join(str(p) for p in (
        manufacturer_id_hex, manufacturerdata_id,
    ) if p is not None) or None
    return {
        'mac_addr': address,
        'alias': ' '.join(str(p) for p in (alias, alias_history_text) if p),
        'mac_type': address_type,
        'manufacturer_name': manufacturer,
        'manufacturer_id': manufacturer_id_text,
        'oui_id': oui,
        'oui_name': org_names,
        'category_names': category_names,
        'category_description': category_descriptions,
        'color': color,
        'uuid': ' '.join(str(p) for p in (
            service_uuids, service_uuid_names, solicited_uuids, solicited_uuid_names,
        ) if p) or None,
        'data': service_data_text,
        'tags': ','.join(tags) if tags else None,
        'bt_type': bearer,
    }


def _search_blob(address, alias, alias_history_text, address_type, manufacturer, appearance, icons,
                  org_names, category_names, category_descriptions,
                  service_uuids, service_uuid_names,
                  solicited_uuids, solicited_uuid_names, service_data_text, tags,
                  manufacturer_id_hex):
    return ' '.join(str(p) for p in (
        address, alias, alias_history_text, address_type, manufacturer, appearance,
        icons, org_names, category_names, category_descriptions,
        service_uuids, service_uuid_names,
        solicited_uuids, solicited_uuid_names,
        service_data_text, ' '.join(tags), manufacturer_id_hex,
    ) if p is not None).lower()


_FILTER_ESCAPED_SEP_RE = re.compile(r'\\([;+])')
_FILTER_AND_SPLIT_RE = re.compile(r'(?<!\\)\+')
_FILTER_OR_SPLIT_RE = re.compile(r'(?<!\\);')
_FIELD_TERM_RE = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*):"(.*)"$', re.DOTALL)


FIELD_SEARCH_KEYS = {
    'mac_addr', 'alias', 'mac_type', 'manufacturer_name', 'manufacturer_id',
    'oui_id', 'oui_name', 'category_names', 'category_description', 'color',
    'uuid', 'data', 'tags', 'bt_type',
}
ENUM_FIELD_KEYS = {'mac_type', 'color', 'bt_type'}


def _unescape_filter_text(s):
    return _FILTER_ESCAPED_SEP_RE.sub(r'\1', s)


def _parse_filter_terms(raw):
    groups = []
    for and_group in _FILTER_AND_SPLIT_RE.split(raw):
        alternatives = []
        for alt in _FILTER_OR_SPLIT_RE.split(and_group):
            unescaped = _unescape_filter_text(alt).strip()
            field_match = _FIELD_TERM_RE.match(unescaped)
            if field_match and field_match.group(1).lower() in FIELD_SEARCH_KEYS:
                field_key = field_match.group(1).lower()
                value = field_match.group(2).replace('\\"', '"').lower()
                alternatives.append(('field', field_key, value))
                continue
            words = unescaped.lower().split()
            if words:
                alternatives.append(('words', words))
        if alternatives:
            groups.append(alternatives)
    return groups


def _field_text_matches(value, candidate):
    if candidate is None:
        return False
    candidate = str(candidate).lower()
    if value.endswith('*'):
        prefix = value[:-1]
        return any(seg.startswith(prefix) for seg in re.split(r'[,\s]+', candidate) if seg)
    return value in candidate


def _field_matches(field_key, value, fields):
    candidate = fields.get(field_key)
    if field_key in ENUM_FIELD_KEYS:
        return candidate is not None and str(candidate).strip().lower() == value
    return _field_text_matches(value, candidate)


def _filter_matches(blob, fields, filter_groups, not_equals):
    if not filter_groups:
        return True

    def alternative_matches(alt):
        if alt[0] == 'field':
            return _field_matches(alt[1], alt[2], fields)
        return all(word in blob for word in alt[1])

    is_match = all(
        any(alternative_matches(alt) for alt in alternatives)
        for alternatives in filter_groups
    )
    return not is_match if not_equals else is_match


def _parse_query_filter():
    filter_groups = _parse_filter_terms(request.args.get('q', ''))
    not_equals = request.args.get('mode') == 'not-equals'
    return filter_groups, not_equals


def _parse_top_n():
    raw = request.args.get('top')
    if not raw:
        return None
    try:
        top_n = int(raw)
    except ValueError:
        return None
    return top_n if top_n >= 1 else None


def _limit_to_top_addresses(rows, weight_of, top_n):
    if top_n is None:
        return rows
    weights = {}
    for row in rows:
        weights[row['address']] = weights.get(row['address'], 0) + weight_of(row)
    top_addresses = {
        address for address, _ in
        sorted(weights.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]
    }
    return [row for row in rows if row['address'] in top_addresses]


ONE_DAY_SECONDS = 86400
ONE_WEEK_SECONDS = 7 * ONE_DAY_SECONDS
ONE_MONTH_SECONDS = 30 * ONE_DAY_SECONDS
ONE_YEAR_SECONDS = 365 * ONE_DAY_SECONDS

POINTS_BUCKET_TIERS = (
    (ONE_WEEK_SECONDS, 5 * 60),
    (ONE_MONTH_SECONDS, 15 * 60),
    (ONE_YEAR_SECONDS, 3 * 3600),
)


POINTS_BUCKET_FALLBACK_SECONDS = ONE_DAY_SECONDS


def _parse_iso(value):
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None


def _window_span_seconds(start, end):
    start_dt = _parse_iso(start)
    if start_dt is None:
        return None
    end_dt = _parse_iso(end) or datetime.datetime.now(datetime.timezone.utc)
    try:
        return (end_dt - start_dt).total_seconds()
    except TypeError:
        return None


def _points_bucket_width_seconds(start, end):
    span_seconds = _window_span_seconds(start, end)
    if span_seconds is not None and span_seconds <= ONE_DAY_SECONDS:
        return None
    if span_seconds is None:
        return POINTS_BUCKET_FALLBACK_SECONDS
    for upper_bound_seconds, width_seconds in POINTS_BUCKET_TIERS:
        if span_seconds <= upper_bound_seconds:
            return width_seconds
    return POINTS_BUCKET_FALLBACK_SECONDS


def _downsample_points(rows, bucket_width_seconds):
    latest_by_bucket = {}
    order = []
    for row in rows:
        seen_dt = _parse_iso(row['seen_at'])

        bucket_index = int(seen_dt.timestamp() // bucket_width_seconds) if seen_dt else row['seen_at']
        key = (row['address'], bucket_index)
        existing = latest_by_bucket.get(key)
        if existing is None:
            order.append(key)
            latest_by_bucket[key] = row
        elif row['seen_at'] > existing['seen_at']:
            latest_by_bucket[key] = row
    return [latest_by_bucket[key] for key in order]


def _filtered_dots_rows(start, end, filter_groups, not_equals):
    conditions = []
    params = []
    if start:
        conditions.append('a.last_seen_at >= ?')
        params.append(start)
    if end:
        conditions.append('a.first_seen_at <= ?')
        params.append(end)
    where_clause = ('WHERE ' + ' AND '.join(conditions)) if conditions else ''
    query = DOTS_QUERY.format(where_clause=where_clause)

    tags_by_address = _tags_by_address()
    service_data_bytes_by_address = _service_data_bytes_by_address()
    multi_alias_addresses = _multi_alias_addresses()
    display_alias_by_device = _display_alias_by_device()
    alias_search_text_by_device = _alias_search_text_by_device()
    db = get_db()
    settings_db = get_settings_db()
    rows = db.execute(query, params).fetchall()
    manufacturerdata_id_by_device = {r[0]: r[8] for r in rows}
    classifications_by_device = _classifications_by_device(
        db, settings_db, manufacturerdata_id_by_device.keys(), manufacturerdata_id_by_device,
    )
    service_uuid_summary_by_device = _uuid_summaries_by_device(db, settings_db, DEVICE_SERVICE_UUIDS_ALL_QUERY)
    solicited_uuid_summary_by_device = _uuid_summaries_by_device(db, settings_db, DEVICE_SOLICITED_UUIDS_ALL_QUERY)
    service_data_search_text_by_device = _service_data_search_text_by_device(db)
    result = []
    device_id_by_address = {}
    for (device_id, address, alias, start_at, end_at, obs_count, rssi_last, address_type,
         manufacturerdata_id, manufacturer, appearance, bearer) in rows:
        alias = display_alias_by_device.get(device_id, alias)
        tags = tags_by_address.get(address, [])
        classification_rows = classifications_by_device.get(device_id, [])
        icons = _icons_string(classification_rows)
        color = _primary_category_color(classification_rows)
        org_names = _distinct_concat(classification_rows, 'org_name')
        category_names = _distinct_concat(classification_rows, 'category_name')
        category_descriptions = _distinct_concat(classification_rows, 'category_description')
        oui = _distinct_concat(classification_rows, 'oui')
        service_uuids, service_uuid_names, uuid_count = service_uuid_summary_by_device.get(
            device_id, (None, None, 0))
        solicited_uuids, solicited_uuid_names, _ = solicited_uuid_summary_by_device.get(
            device_id, (None, None, 0))
        manufacturer_id_hex = _manufacturer_id_hex(manufacturerdata_id) if manufacturerdata_id is not None else None
        service_data_text = service_data_search_text_by_device.get(device_id)
        blob = _search_blob(
            address, alias, alias_search_text_by_device.get(device_id), address_type, manufacturer, appearance,
            icons, org_names, category_names, category_descriptions,
            service_uuids, service_uuid_names,
            solicited_uuids, solicited_uuid_names,
            service_data_text, tags, manufacturer_id_hex,
        )
        fields = _build_search_fields(
            address, alias, alias_search_text_by_device.get(device_id), address_type, manufacturer,
            manufacturer_id_hex, manufacturerdata_id, oui, org_names,
            category_names, category_descriptions, color,
            service_uuids, service_uuid_names, solicited_uuids, solicited_uuid_names,
            service_data_text, tags, bearer,
        )
        if not _filter_matches(blob, fields, filter_groups, not_equals):
            continue
        device_id_by_address[address] = device_id
        result.append({
            'address': address,
            'alias': alias,
            'start': start_at,
            'end': end_at,
            'obs_count': obs_count,
            'rssi_last': rssi_last,
            'uuid_count': uuid_count or 0,
            'icons': icons.split(',') if icons else [],
            'color': color,
            'tags': tags,
            'service_data_bytes': service_data_bytes_by_address.get(address, 0),
            'bearer': bearer,
            'multi_alias': address in multi_alias_addresses,
        })
    return result, device_id_by_address, classifications_by_device, manufacturerdata_id_by_device


@app.route('/api/dots')
def dots():
    if not os.path.exists(DB_PATH):
        return jsonify([])

    start = request.args.get('start')
    end = request.args.get('end')
    filter_groups, not_equals = _parse_query_filter()
    top_n = _parse_top_n()

    result, _, _, _ = _filtered_dots_rows(start, end, filter_groups, not_equals)
    result = _limit_to_top_addresses(result, lambda row: row['obs_count'] or 0, top_n)
    return jsonify(result)


def _sorted_stat_entries(counts):
    return [
        {'label': label, 'filter': filter_value, 'icon': icon, 'color': color, 'count': count}
        for (label, filter_value, icon, color), count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0][0]))
    ]


@app.route('/api/stats')
def device_stats():
    empty = {'oui': [], 'manufacturer': [], 'alias': [], 'uuid': []}
    if not os.path.exists(DB_PATH):
        return jsonify(empty)

    start = request.args.get('start')
    end = request.args.get('end')
    filter_groups, not_equals = _parse_query_filter()
    top_n = _parse_top_n()

    result, device_id_by_address, classifications_by_device, manufacturerdata_id_by_device = (
        _filtered_dots_rows(start, end, filter_groups, not_equals)
    )
    result = _limit_to_top_addresses(result, lambda row: row['obs_count'] or 0, top_n)
    shown_addresses = {row['address'] for row in result}
    shown_device_ids = {
        device_id_by_address[address] for address in shown_addresses if address in device_id_by_address
    }

    oui_counts = {}
    uuid_counts = {}
    alias_icon_color_by_device = {}
    for device_id in shown_device_ids:
        for c in classifications_by_device.get(device_id, []):
            if c['source'] == 'ouid' and c['org_name']:
                key = (c['org_name'], c['org_name'], c['category_icon'], c['category_color'])
                oui_counts[key] = oui_counts.get(key, 0) + 1
            elif c['source'] == 'uuid' and c.get('uuid'):
                label = f"{c['uuid']} - {c['category_name']}" if c['category_name'] else c['uuid']
                key = (label, c['uuid'], c['category_icon'], c['category_color'])
                uuid_counts[key] = uuid_counts.get(key, 0) + 1
            elif c['source'] == 'alias' and c['category_icon'] and device_id not in alias_icon_color_by_device:
                alias_icon_color_by_device[device_id] = (c['category_icon'], c['category_color'])

    settings_db = get_settings_db()
    referenced_mfr_ids = {
        manufacturerdata_id_by_device[device_id] for device_id in shown_device_ids
        if manufacturerdata_id_by_device.get(device_id) is not None
    }
    manufacturer_names_by_id = {}
    if referenced_mfr_ids:
        placeholders = ','.join('?' * len(referenced_mfr_ids))
        manufacturer_names_by_id = dict(settings_db.execute(
            f'SELECT manufacturerdata_id, name FROM manufacturerdata_names WHERE manufacturerdata_id IN ({placeholders})',
            list(referenced_mfr_ids),
        ).fetchall())
    manufacturer_icon_color_by_id = {}
    for device_id in shown_device_ids:
        for c in classifications_by_device.get(device_id, []):
            if c['source'] == 'manufacturer' and c.get('manufacturer_id') is not None:
                manufacturer_icon_color_by_id[c['manufacturer_id']] = (c['category_icon'], c['category_color'])

    mfr_counts = {}
    for device_id in shown_device_ids:
        manufacturer_id = manufacturerdata_id_by_device.get(device_id)
        if manufacturer_id is None:
            continue
        manufacturer_id_hex = _manufacturer_id_hex(manufacturer_id)
        name = manufacturer_names_by_id.get(manufacturer_id)
        icon, color = manufacturer_icon_color_by_id.get(manufacturer_id, (None, None))

        filter_value = name if name is not None else manufacturer_id_hex
        key = (f'{name or "Unknown"} ({manufacturer_id_hex})', filter_value, icon, color)
        mfr_counts[key] = mfr_counts.get(key, 0) + 1

    alias_counts = {}
    for row in result:
        alias = row['alias']
        if not alias or MAC_STYLE_ALIAS_RE.match(alias):
            continue
        icon, color = alias_icon_color_by_device.get(device_id_by_address.get(row['address']), (None, None))
        key = (alias, alias, icon, color)
        alias_counts[key] = alias_counts.get(key, 0) + 1

    return jsonify({
        'oui': _sorted_stat_entries(oui_counts),
        'manufacturer': _sorted_stat_entries(mfr_counts),
        'alias': _sorted_stat_entries(alias_counts),
        'uuid': _sorted_stat_entries(uuid_counts),
    })


@app.route('/api/points')
def points():
    if not os.path.exists(DB_PATH):
        return jsonify([])

    start = request.args.get('start')
    end = request.args.get('end')
    conditions = []
    params = []
    if start:
        conditions.append('o.seen_at >= ?')
        params.append(start)
    if end:
        conditions.append('o.seen_at <= ?')
        params.append(end)
    where_clause = ('WHERE ' + ' AND '.join(conditions)) if conditions else ''
    query = POINTS_QUERY.format(where_clause=where_clause)

    filter_groups, not_equals = _parse_query_filter()
    top_n = _parse_top_n()
    bucket_width_seconds = _points_bucket_width_seconds(start, end)

    tags_by_address = _tags_by_address()
    service_data_bytes_by_address = _service_data_bytes_by_address()
    multi_alias_addresses = _multi_alias_addresses()
    display_alias_by_device = _display_alias_by_device()
    alias_search_text_by_device = _alias_search_text_by_device()
    db = get_db()
    settings_db = get_settings_db()
    rows = db.execute(query, params).fetchall()
    manufacturerdata_id_by_device = {r[0]: r[6] for r in rows}
    classifications_by_device = _classifications_by_device(
        db, settings_db, manufacturerdata_id_by_device.keys(), manufacturerdata_id_by_device,
    )
    service_uuid_summary_by_device = _uuid_summaries_by_device(db, settings_db, DEVICE_SERVICE_UUIDS_ALL_QUERY)
    solicited_uuid_summary_by_device = _uuid_summaries_by_device(db, settings_db, DEVICE_SOLICITED_UUIDS_ALL_QUERY)
    service_data_search_text_by_device = _service_data_search_text_by_device(db)
    result = []
    for (device_id, address, alias, seen_at, rssi, address_type,
         manufacturerdata_id, manufacturer, appearance, bearer) in rows:
        alias = display_alias_by_device.get(device_id, alias)
        tags = tags_by_address.get(address, [])
        classification_rows = classifications_by_device.get(device_id, [])
        icons = _icons_string(classification_rows)
        color = _primary_category_color(classification_rows)
        org_names = _distinct_concat(classification_rows, 'org_name')
        category_names = _distinct_concat(classification_rows, 'category_name')
        category_descriptions = _distinct_concat(classification_rows, 'category_description')
        oui = _distinct_concat(classification_rows, 'oui')
        service_uuids, service_uuid_names, uuid_count = service_uuid_summary_by_device.get(
            device_id, (None, None, 0))
        solicited_uuids, solicited_uuid_names, _ = solicited_uuid_summary_by_device.get(
            device_id, (None, None, 0))
        manufacturer_id_hex = _manufacturer_id_hex(manufacturerdata_id) if manufacturerdata_id is not None else None
        service_data_text = service_data_search_text_by_device.get(device_id)
        blob = _search_blob(
            address, alias, alias_search_text_by_device.get(device_id), address_type, manufacturer, appearance,
            icons, org_names, category_names, category_descriptions,
            service_uuids, service_uuid_names,
            solicited_uuids, solicited_uuid_names,
            service_data_text, tags, manufacturer_id_hex,
        )
        fields = _build_search_fields(
            address, alias, alias_search_text_by_device.get(device_id), address_type, manufacturer,
            manufacturer_id_hex, manufacturerdata_id, oui, org_names,
            category_names, category_descriptions, color,
            service_uuids, service_uuid_names, solicited_uuids, solicited_uuid_names,
            service_data_text, tags, bearer,
        )
        if not _filter_matches(blob, fields, filter_groups, not_equals):
            continue
        result.append({
            'address': address,
            'alias': alias,
            'seen_at': seen_at,
            'rssi': rssi,
            'uuid_count': uuid_count or 0,
            'icons': icons.split(',') if icons else [],
            'color': color,
            'tags': tags,
            'service_data_bytes': service_data_bytes_by_address.get(address, 0),
            'bearer': bearer,
            'multi_alias': address in multi_alias_addresses,
        })
    result = _limit_to_top_addresses(result, lambda row: 1, top_n)
    if bucket_width_seconds is not None:
        result = _downsample_points(result, bucket_width_seconds)
    return jsonify(result)


DEVICE_QUERY = '''
    SELECT device_id, address, address_type, alias, manufacturerdata_id, manufacturerdata_name,
           appearance, bearer, first_seen_at, last_seen_at
    FROM devices
    WHERE address = ?
    ORDER BY last_seen_at DESC
    LIMIT 1
'''

ALIAS_HISTORY_LIMIT = 20
ALIAS_HISTORY_QUERY = '''
    SELECT alias, first_seen_at, last_seen_at
    FROM device_alias_history
    WHERE device_id = ?
    ORDER BY first_seen_at DESC
    LIMIT ?
'''

RSSI_HISTORY_LIMIT = 10
RSSI_HISTORY_QUERY = '''
    SELECT seen_at, rssi
    FROM raw_observations
    WHERE device_id = ? AND rssi IS NOT NULL
    ORDER BY seen_at DESC
    LIMIT ?
'''

SERVICE_UUIDS_QUERY = '''
    SELECT s.uuid, s.uuid_lookup_id
    FROM device_service_uuids ds
    JOIN service_uuids s ON s.uuid_id = ds.uuid_id
    WHERE ds.device_id = ?
    ORDER BY ds.first_seen_at
'''

SOLICITED_UUIDS_QUERY = '''
    SELECT s.uuid, s.uuid_lookup_id
    FROM device_solicited_uuids dsol
    JOIN service_uuids s ON s.uuid_id = dsol.uuid_id
    WHERE dsol.device_id = ?
    ORDER BY dsol.first_seen_at
'''


def _uuid_lookup_names_and_types(settings_db, uuid_lookup_ids):
    uuid_lookup_ids = {i for i in uuid_lookup_ids if i is not None}
    if not uuid_lookup_ids:
        return {}
    placeholders = ','.join('?' * len(uuid_lookup_ids))
    return {
        uuid_lookup_id: (name, uuid_type, icon)
        for uuid_lookup_id, name, uuid_type, icon in settings_db.execute(
            f'SELECT uuid_lookup_id, name, type, icon FROM uuid_lookups WHERE uuid_lookup_id IN ({placeholders})',
            list(uuid_lookup_ids),
        ).fetchall()
    }

SERVICE_DATA_HISTORY_QUERY = '''
    SELECT uuid_id, data_hex, seen_at
    FROM device_service_data_history
    WHERE device_id = ?
    ORDER BY seen_at DESC
'''


def _service_uuid_labels(db, settings_db, uuid_ids):
    if not uuid_ids:
        return {}
    placeholders = ','.join('?' * len(uuid_ids))
    rows = db.execute(
        f'SELECT uuid_id, uuid, uuid_lookup_id FROM service_uuids WHERE uuid_id IN ({placeholders})',
        list(uuid_ids),
    ).fetchall()
    names_by_lookup_id = _uuid_lookup_names(settings_db, (lookup_id for _, _, lookup_id in rows))
    return {
        uuid_id: (uuid, names_by_lookup_id.get(lookup_id))
        for uuid_id, uuid, lookup_id in rows
    }

OBSERVATION_COUNT_QUERY = 'SELECT COUNT(*) FROM raw_observations WHERE device_id = ?'

DEVICE_RSSI_QUERY = '''
    SELECT rssi_last, rssi_best, rssi_avg, obs_count
    FROM device_appearances
    WHERE device_id = ?
    ORDER BY last_seen_at DESC
'''

DEVICE_TAGS_QUERY = '''
    SELECT t.name FROM device_tags dt JOIN tags t ON t.tag_id = dt.tag_id
    WHERE dt.address = ? ORDER BY dt.created_at
'''

MAX_TAG_LENGTH = 40


def _device_rssi_stats(db, device_id):
    rows = db.execute(DEVICE_RSSI_QUERY, (device_id,)).fetchall()
    if not rows:
        return {'last': None, 'best': None, 'avg': None}
    rssi_last = rows[0][0]
    best_values = [rssi_best for _, rssi_best, _, _ in rows if rssi_best is not None]
    rssi_best = max(best_values) if best_values else None
    weighted_rows = [(rssi_avg, obs_count) for _, _, rssi_avg, obs_count in rows if rssi_avg is not None]
    weight = sum(obs_count for _, obs_count in weighted_rows)
    rssi_avg = round(sum(avg * obs_count for avg, obs_count in weighted_rows) / weight, 1) if weight else None
    return {'last': rssi_last, 'best': rssi_best, 'avg': rssi_avg}


@app.route('/api/device/<address>')
def device_detail(address):
    if not os.path.exists(DB_PATH):
        return jsonify({'error': 'not found'}), 404

    db = get_db()
    settings_db = get_settings_db()
    row = db.execute(DEVICE_QUERY, (address,)).fetchone()
    if row is None:
        return jsonify({'error': 'not found'}), 404
    (device_id, address, address_type, alias, manufacturerdata_id, manufacturer,
     appearance, bearer, first_seen_at, last_seen_at) = row

    if os.path.exists(HISTORIES_DB_PATH) and _histories_tables_exist(get_histories_db()):
        histories_db = get_histories_db()
        alias_history_rows = histories_db.execute(ALIAS_HISTORY_QUERY, (device_id, ALIAS_HISTORY_LIMIT)).fetchall()
        service_data_history_rows = histories_db.execute(SERVICE_DATA_HISTORY_QUERY, (device_id,)).fetchall()
    else:
        alias_history_rows = []
        service_data_history_rows = []
    uuid_labels = _service_uuid_labels(db, settings_db, {uuid_id for uuid_id, _, _ in service_data_history_rows})

    classification_rows = _classifications_by_device(
        db, settings_db, [device_id], {device_id: manufacturerdata_id},
    ).get(device_id, [])
    classification_rows.sort(key=lambda r: (r['source'] != 'ouid', r['oui'] or ''))

    service_uuid_rows = db.execute(SERVICE_UUIDS_QUERY, (device_id,)).fetchall()
    solicited_uuid_rows = db.execute(SOLICITED_UUIDS_QUERY, (device_id,)).fetchall()
    uuid_names_and_types = _uuid_lookup_names_and_types(
        settings_db,
        [lookup_id for _, lookup_id in service_uuid_rows] + [lookup_id for _, lookup_id in solicited_uuid_rows],
    )

    return jsonify({
        'address': address,
        'address_type': address_type,
        'alias': alias,
        'manufacturer': manufacturer,
        'appearance': appearance,
        'bearer': bearer,
        'first_seen_at': first_seen_at,
        'last_seen_at': last_seen_at,
        'observation_count': db.execute(OBSERVATION_COUNT_QUERY, (device_id,)).fetchone()[0],
        'rssi': _device_rssi_stats(db, device_id),
        'tags': [row[0] for row in get_settings_db().execute(DEVICE_TAGS_QUERY, (address,)).fetchall()],
        'classifications': [
            {'oui': r['oui'], 'org_name': r['org_name'], 'category_name': r['category_name'],
             'icon': r['category_icon'], 'color': r['category_color'], 'source': r['source'],
             'uuid': r.get('uuid'), 'manufacturer_id': r.get('manufacturer_id')}
            for r in classification_rows
        ],
        'alias_history': [
            {'alias': alias, 'first_seen_at': first_seen_at, 'last_seen_at': last_seen_at}
            for alias, first_seen_at, last_seen_at in alias_history_rows
        ],
        'rssi_history': [
            {'seen_at': seen_at, 'rssi': rssi}
            for seen_at, rssi in db.execute(RSSI_HISTORY_QUERY, (device_id, RSSI_HISTORY_LIMIT)).fetchall()
        ],
        'service_uuids': [
            {'uuid': uuid, 'name': uuid_names_and_types.get(lookup_id, (None, None, None))[0],
             'type': uuid_names_and_types.get(lookup_id, (None, None, None))[1],
             'icon': uuid_names_and_types.get(lookup_id, (None, None, None))[2]}
            for uuid, lookup_id in service_uuid_rows
        ],
        'solicited_uuids': [
            {'uuid': uuid, 'name': uuid_names_and_types.get(lookup_id, (None, None, None))[0],
             'type': uuid_names_and_types.get(lookup_id, (None, None, None))[1],
             'icon': uuid_names_and_types.get(lookup_id, (None, None, None))[2]}
            for uuid, lookup_id in solicited_uuid_rows
        ],
        'service_data': [
            {
                'uuid': uuid_labels.get(uuid_id, (str(uuid_id), None))[0],
                'name': uuid_labels.get(uuid_id, (None, None))[1],
                'data_hex': data_hex,
                'seen_at': seen_at,
            }
            for uuid_id, data_hex, seen_at in service_data_history_rows
        ],
    })


SERVICE_UUID_TIMELINE_QUERY = '''
    SELECT s.uuid, s.uuid_lookup_id, ds.first_seen_at
    FROM device_service_uuids ds
    JOIN service_uuids s ON s.uuid_id = ds.uuid_id
    WHERE ds.device_id = ?
'''
SOLICITED_UUID_TIMELINE_QUERY = '''
    SELECT s.uuid, s.uuid_lookup_id, dsol.first_seen_at
    FROM device_solicited_uuids dsol
    JOIN service_uuids s ON s.uuid_id = dsol.uuid_id
    WHERE dsol.device_id = ?
'''
ALIAS_TIMELINE_QUERY = '''
    SELECT first_seen_at, alias
    FROM device_alias_history
    WHERE device_id = ?
    ORDER BY first_seen_at
'''
MANUFACTURERDATA_TIMELINE_QUERY = '''
    SELECT first_seen_at, manufacturerdata_id
    FROM device_manufacturerdata_history
    WHERE device_id = ?
    ORDER BY first_seen_at
'''
SERVICE_DATA_TIMELINE_QUERY = '''
    SELECT seen_at, uuid_id, data_hex
    FROM device_service_data_history
    WHERE device_id = ?
    ORDER BY seen_at
'''
EXPORT_SESSIONS_QUERY = '''
    SELECT session_id, first_seen_at, last_seen_at
    FROM device_appearances
    WHERE device_id = ?
'''
EXPORT_OBSERVATIONS_QUERY = '''
    SELECT session_id, seen_at, rssi
    FROM raw_observations
    WHERE device_id = ?
    ORDER BY seen_at
'''


def _latest_as_of(starts, values, seen_at):
    idx = bisect.bisect_right(starts, seen_at) - 1
    return values[idx] if idx >= 0 else None


def _csv_cell(value):
    return str(value) if value not in (None, '') else '--'


@app.route('/api/device/<address>/export.csv')
def device_export_csv(address):
    if not os.path.exists(DB_PATH):
        return jsonify({'error': 'not found'}), 404

    db = get_db()
    settings_db = get_settings_db()
    row = db.execute(DEVICE_QUERY, (address,)).fetchone()
    if row is None:
        return jsonify({'error': 'not found'}), 404

    device_id, address, _, _, _, _, _, bearer, _, _ = row

    oui_key = address.replace(':', '').upper()[:6]
    oui_display = ':'.join(address.split(':')[:3]).upper()
    oui_org_row = settings_db.execute('SELECT org_name FROM ouids WHERE oui = ? LIMIT 1', (oui_key,)).fetchone()
    oui_label = f'{oui_org_row[0]} ({oui_display})' if oui_org_row else f'Unknown ({oui_display})'

    observations = db.execute(EXPORT_OBSERVATIONS_QUERY, (device_id,)).fetchall()

    sessions = db.execute(EXPORT_SESSIONS_QUERY, (device_id,)).fetchall()
    session_window = {session_id: (start, end) for session_id, start, end in sessions}

    uuid_timeline = sorted(
        db.execute(SERVICE_UUID_TIMELINE_QUERY, (device_id,)).fetchall() +
        db.execute(SOLICITED_UUID_TIMELINE_QUERY, (device_id,)).fetchall(),
        key=lambda r: r[2],
    )
    uuid_starts = [r[2] for r in uuid_timeline]
    uuid_raw_values = [r[0] for r in uuid_timeline]

    uuid_names_and_types = _uuid_lookup_names_and_types(
        settings_db, {r[1] for r in uuid_timeline if r[1] is not None},
    )
    uuid_display_values = []
    for uuid, uuid_lookup_id, _ in uuid_timeline:
        entry = uuid_names_and_types.get(uuid_lookup_id)
        uuid_display_values.append(f'{uuid} — {entry[0]} ({entry[1]})' if entry else uuid)

    if os.path.exists(HISTORIES_DB_PATH) and _histories_tables_exist(get_histories_db()):
        histories_db = get_histories_db()
        alias_timeline = histories_db.execute(ALIAS_TIMELINE_QUERY, (device_id,)).fetchall()
        mfr_timeline = histories_db.execute(MANUFACTURERDATA_TIMELINE_QUERY, (device_id,)).fetchall()
        service_data_rows = histories_db.execute(SERVICE_DATA_TIMELINE_QUERY, (device_id,)).fetchall()
    else:
        alias_timeline, mfr_timeline, service_data_rows = [], [], []

    alias_starts = [r[0] for r in alias_timeline]
    alias_values = [r[1] for r in alias_timeline]
    mfr_starts = [r[0] for r in mfr_timeline]
    mfr_values = [r[1] for r in mfr_timeline]

    referenced_mfr_ids = {v for v in mfr_values if v is not None}
    manufacturer_names_by_id = {}
    if referenced_mfr_ids:
        placeholders = ','.join('?' * len(referenced_mfr_ids))
        manufacturer_names_by_id = dict(settings_db.execute(
            f'SELECT manufacturerdata_id, name FROM manufacturerdata_names WHERE manufacturerdata_id IN ({placeholders})',
            list(referenced_mfr_ids),
        ).fetchall())

    def _format_manufacturer(manufacturer_id):
        if manufacturer_id is None:
            return None
        name = manufacturer_names_by_id.get(manufacturer_id, 'Unknown')
        return f'{name} ({manufacturer_id})'

    service_data_seen_ats = [r[0] for r in service_data_rows]
    referenced_uuid_ids = {uuid_id for _, uuid_id, _ in service_data_rows}
    uuid_text_by_id = {}
    if referenced_uuid_ids:
        placeholders = ','.join('?' * len(referenced_uuid_ids))
        uuid_text_by_id = dict(db.execute(
            f'SELECT uuid_id, uuid FROM service_uuids WHERE uuid_id IN ({placeholders})',
            list(referenced_uuid_ids),
        ).fetchall())

    service_data_by_session_uuid = {}
    service_data_decoded_by_session_uuid = {}
    for session_id, (start, end) in session_window.items():
        lo = bisect.bisect_left(service_data_seen_ats, start)
        hi = bisect.bisect_right(service_data_seen_ats, end)
        seen_by_uuid, decoded_seen_by_uuid = {}, {}
        for _, uuid_id, data_hex in service_data_rows[lo:hi]:
            uuid = uuid_text_by_id.get(uuid_id, uuid_id)
            seen, parts = seen_by_uuid.setdefault(uuid, (set(), []))
            if data_hex not in seen:
                seen.add(data_hex)
                parts.append(data_hex)
            decoded = _decode_service_data_text(uuid, data_hex)
            if decoded:
                dseen, dparts = decoded_seen_by_uuid.setdefault(uuid, (set(), []))
                if decoded not in dseen:
                    dseen.add(decoded)
                    dparts.append(decoded)
        for uuid, (_, parts) in seen_by_uuid.items():
            service_data_by_session_uuid[(session_id, uuid)] = ','.join(parts)
        for uuid, (_, dparts) in decoded_seen_by_uuid.items():
            service_data_decoded_by_session_uuid[(session_id, uuid)] = ','.join(dparts)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        'mac_address', 'seen_at', 'aliases', 'rssi', 'bearer', 'oui',
        'manufacturer_id', 'uuid', 'service_data', 'service_data_decoded',
    ])

    last_known_rssi = None
    for session_id, seen_at, rssi in observations:
        if rssi is not None:
            last_known_rssi = rssi
        else:
            rssi = last_known_rssi
        alias = _latest_as_of(alias_starts, alias_values, seen_at)
        manufacturer_id = _latest_as_of(mfr_starts, mfr_values, seen_at)
        common = [address, seen_at, alias, rssi, bearer, oui_label, _format_manufacturer(manufacturer_id)]

        known_count = bisect.bisect_right(uuid_starts, seen_at)
        if known_count == 0:
            writer.writerow([_csv_cell(v) for v in [*common, None, None, None]])
            continue
        for i in range(known_count):
            uuid = uuid_raw_values[i]
            writer.writerow([_csv_cell(v) for v in [
                *common, uuid_display_values[i],
                service_data_by_session_uuid.get((session_id, uuid)),
                service_data_decoded_by_session_uuid.get((session_id, uuid)),
            ]])

    filename = f"{address.replace(':', '-')}_export.csv"

    return Response(
        buf.getvalue().encode('utf-8-sig'),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


def _get_device_connect_status(connections_db, address):
    row = connections_db.execute(
        'SELECT state, error FROM device_connections WHERE address = ?', (address,)
    ).fetchone()
    if row is None:
        return {'state': 'disconnected', 'error': None}
    state, error = row
    return {'state': state, 'error': error}


def _set_device_connect_action(connections_db, address, action):
    connections_db.execute(
        'INSERT INTO device_connect_requests (address, action, requested_at) VALUES (?, ?, ?) '
        'ON CONFLICT (address) DO UPDATE SET action = excluded.action, requested_at = excluded.requested_at',
        (address, action, datetime.datetime.now(datetime.timezone.utc).isoformat()),
    )
    connections_db.commit()


@app.route('/api/device/<address>/connect-status')
def get_device_connect_status(address):
    return jsonify(_get_device_connect_status(get_connections_db(), address))


@app.route('/api/device-connections')
def get_device_connections():
    rows = get_connections_db().execute('SELECT address, state, error FROM device_connections').fetchall()
    return jsonify({address: {'state': state, 'error': error} for address, state, error in rows})


@app.route('/api/auto-connect-successes/count')
def get_auto_connect_success_count():
    count = get_connections_db().execute('SELECT COUNT(*) FROM auto_connect_successes').fetchone()[0]
    return jsonify({'count': count})


@app.route('/api/device/<address>/connect', methods=['POST'])
def connect_device_route(address):
    _set_device_connect_action(get_connections_db(), address, 'connect')
    return jsonify({'ok': True})


@app.route('/api/device/<address>/disconnect', methods=['POST'])
def disconnect_device_route(address):
    _set_device_connect_action(get_connections_db(), address, 'disconnect')
    return jsonify({'ok': True})


def _clean_tag_name(raw):
    name = re.sub(r'\s+', ' ', raw).strip() if isinstance(raw, str) else ''
    if not name:
        return None, 'tag must not be empty'
    if len(name) > MAX_TAG_LENGTH:
        return None, f'tag must be {MAX_TAG_LENGTH} characters or fewer'
    return name, None


@app.route('/api/device/<address>/tags', methods=['POST'])
def add_device_tag(address):
    tag, error = _clean_tag_name((request.get_json(silent=True) or {}).get('tag', ''))
    if error:
        return jsonify({'error': error}), 400

    settings_db = get_settings_db()
    tag_id = _get_or_create_tag_id(settings_db, tag)
    settings_db.execute(
        'INSERT INTO device_tags (address, tag_id, created_at) VALUES (?, ?, ?) '
        'ON CONFLICT (address, tag_id) DO NOTHING',
        (address, tag_id, datetime.datetime.now(datetime.timezone.utc).isoformat()),
    )
    settings_db.commit()
    return jsonify({'tags': [row[0] for row in settings_db.execute(DEVICE_TAGS_QUERY, (address,)).fetchall()]})


@app.route('/api/device/<address>/tags/<tag>', methods=['DELETE'])
def remove_device_tag(address, tag):
    settings_db = get_settings_db()
    row = settings_db.execute('SELECT tag_id FROM tags WHERE name = ?', (tag,)).fetchone()
    if row is not None:
        settings_db.execute('DELETE FROM device_tags WHERE address = ? AND tag_id = ?', (address, row[0]))
        settings_db.commit()
    return jsonify({'tags': [row[0] for row in settings_db.execute(DEVICE_TAGS_QUERY, (address,)).fetchall()]})


@app.route('/api/tags')
def list_tags():
    if not os.path.exists(SETTINGS_DB_PATH):
        return jsonify([])
    settings_db = get_settings_db()
    tags = settings_db.execute(
        'SELECT tag_id, name FROM tags ORDER BY name COLLATE NOCASE'
    ).fetchall()
    addresses_by_tag_id = {}
    for tag_id, address in settings_db.execute(
        'SELECT tag_id, address FROM device_tags ORDER BY created_at'
    ).fetchall():
        addresses_by_tag_id.setdefault(tag_id, []).append(address)
    return jsonify([
        {'tag': name, 'addresses': addresses_by_tag_id.get(tag_id, [])}
        for tag_id, name in tags
    ])


@app.route('/api/tags', methods=['POST'])
def create_tag():
    name, error = _clean_tag_name((request.get_json(silent=True) or {}).get('name', ''))
    if error:
        return jsonify({'error': error}), 400

    settings_db = get_settings_db()
    if settings_db.execute('SELECT 1 FROM tags WHERE name = ?', (name,)).fetchone():
        return jsonify({'error': 'a tag with that name already exists'}), 409
    _get_or_create_tag_id(settings_db, name)
    settings_db.commit()
    return jsonify({'ok': True}), 201


@app.route('/api/tags/<name>', methods=['PATCH'])
def rename_tag(name):
    new_name, error = _clean_tag_name((request.get_json(silent=True) or {}).get('name', ''))
    if error:
        return jsonify({'error': error}), 400

    settings_db = get_settings_db()
    row = settings_db.execute('SELECT tag_id FROM tags WHERE name = ?', (name,)).fetchone()
    if row is None:
        return jsonify({'error': 'not found'}), 404
    try:
        settings_db.execute('UPDATE tags SET name = ? WHERE tag_id = ?', (new_name, row[0]))
        settings_db.commit()
    except sqlite3.IntegrityError:
        return jsonify({'error': 'a tag with that name already exists'}), 409
    return jsonify({'ok': True})


@app.route('/api/tags/<name>', methods=['DELETE'])
def delete_tag(name):
    settings_db = get_settings_db()
    settings_db.execute('DELETE FROM tags WHERE name = ?', (name,))
    settings_db.commit()
    return jsonify({'ok': True})


def _pid_alive(pid):
    try:
        reaped_pid, _ = os.waitpid(pid, os.WNOHANG)
        if reaped_pid == pid:
            return False
    except ChildProcessError:
        pass
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _monitor_status_row(settings_db):
    row = settings_db.execute('SELECT running, pid FROM monitor_status WHERE id = 1').fetchone()
    if row is None:
        return False, None
    running, pid = bool(row[0]), row[1]
    if running and (pid is None or not _pid_alive(pid)):
        settings_db.execute(
            'UPDATE monitor_status SET running = 0, pid = NULL, updated_at = ? WHERE id = 1',
            (datetime.datetime.now(datetime.timezone.utc).isoformat(),),
        )
        settings_db.commit()
        return False, None
    return running, pid


def _wait_until_gone(pid, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(MONITOR_POLL_INTERVAL_SECONDS)
    return not _pid_alive(pid)


def _stop_monitor(settings_db, wait=True, timeout=MONITOR_STOP_TIMEOUT_SECONDS):
    running, pid = _monitor_status_row(settings_db)
    if not running or pid is None:
        return True
    try:
        os.kill(pid, signal.SIGINT)
    except ProcessLookupError:
        return True
    if not wait:
        return True
    if _wait_until_gone(pid, timeout):
        return True

    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return True
        if _wait_until_gone(pid, MONITOR_FORCE_STOP_GRACE_SECONDS):
            return True

    return not _pid_alive(pid)


@app.route('/api/monitor/status')
def monitor_status():
    running, pid = _monitor_status_row(get_settings_db())
    return jsonify({'running': running, 'pid': pid})


def _get_interface(settings_db):
    row = settings_db.execute('SELECT interface FROM interface_config WHERE id = 1').fetchone()
    return row[0] if row else DEFAULT_INTERFACE


def _set_interface(settings_db, interface):
    settings_db.execute(
        'UPDATE interface_config SET interface = ?, updated_at = ? WHERE id = 1',
        (interface, datetime.datetime.now(datetime.timezone.utc).isoformat()),
    )
    settings_db.commit()


@app.route('/api/interface')
def get_interface():
    return jsonify({'interface': _get_interface(get_settings_db())})


@app.route('/api/interface', methods=['POST'])
def set_interface():
    interface = (request.get_json(silent=True) or {}).get('interface')
    if interface not in ALLOWED_INTERFACES:
        return jsonify({'error': f'interface must be one of {", ".join(ALLOWED_INTERFACES)}'}), 400
    _set_interface(get_settings_db(), interface)
    return jsonify({'ok': True, 'interface': interface})


def _get_scan_mode(settings_db):
    row = settings_db.execute('SELECT scan_mode FROM scan_mode_config WHERE id = 1').fetchone()
    return row[0] if row else DEFAULT_SCAN_MODE


def _set_scan_mode(settings_db, scan_mode):
    settings_db.execute(
        'UPDATE scan_mode_config SET scan_mode = ?, updated_at = ? WHERE id = 1',
        (scan_mode, datetime.datetime.now(datetime.timezone.utc).isoformat()),
    )
    settings_db.commit()


@app.route('/api/scan-mode')
def get_scan_mode():
    return jsonify({'scan_mode': _get_scan_mode(get_settings_db())})


@app.route('/api/scan-mode', methods=['POST'])
def set_scan_mode():
    scan_mode = (request.get_json(silent=True) or {}).get('scan_mode')
    if scan_mode not in ALLOWED_SCAN_MODES:
        return jsonify({'error': f'scan_mode must be one of {", ".join(ALLOWED_SCAN_MODES)}'}), 400
    _set_scan_mode(get_settings_db(), scan_mode)
    return jsonify({'ok': True, 'scan_mode': scan_mode})


def _get_auto_start(settings_db):
    row = settings_db.execute('SELECT enabled FROM auto_start_config WHERE id = 1').fetchone()
    return bool(row[0]) if row else DEFAULT_AUTO_START


def _set_auto_start(settings_db, enabled):
    settings_db.execute(
        'UPDATE auto_start_config SET enabled = ?, updated_at = ? WHERE id = 1',
        (1 if enabled else 0, datetime.datetime.now(datetime.timezone.utc).isoformat()),
    )
    settings_db.commit()


@app.route('/api/auto-start')
def get_auto_start():
    return jsonify({'enabled': _get_auto_start(get_settings_db())})


@app.route('/api/auto-start', methods=['POST'])
def set_auto_start():
    enabled = (request.get_json(silent=True) or {}).get('enabled')
    if not isinstance(enabled, bool):
        return jsonify({'error': 'enabled must be a boolean'}), 400
    _set_auto_start(get_settings_db(), enabled)
    return jsonify({'ok': True, 'enabled': enabled})


def _get_connect_timeout(settings_db):
    row = settings_db.execute('SELECT timeout_seconds FROM connect_timeout_config WHERE id = 1').fetchone()
    return row[0] if row else DEFAULT_CONNECT_TIMEOUT_SECONDS


def _set_connect_timeout(settings_db, timeout_seconds):
    settings_db.execute(
        'UPDATE connect_timeout_config SET timeout_seconds = ?, updated_at = ? WHERE id = 1',
        (timeout_seconds, datetime.datetime.now(datetime.timezone.utc).isoformat()),
    )
    settings_db.commit()


@app.route('/api/connect-timeout')
def get_connect_timeout():
    return jsonify({'timeout_seconds': _get_connect_timeout(get_settings_db())})


@app.route('/api/connect-timeout', methods=['POST'])
def set_connect_timeout():
    timeout_seconds = (request.get_json(silent=True) or {}).get('timeout_seconds')
    if (
        not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool)
        or not (MIN_CONNECT_TIMEOUT_SECONDS <= timeout_seconds <= MAX_CONNECT_TIMEOUT_SECONDS)
    ):
        return jsonify({
            'error': f'timeout_seconds must be an integer between '
                     f'{MIN_CONNECT_TIMEOUT_SECONDS} and {MAX_CONNECT_TIMEOUT_SECONDS}',
        }), 400
    _set_connect_timeout(get_settings_db(), timeout_seconds)
    return jsonify({'ok': True, 'timeout_seconds': timeout_seconds})


def _get_auto_connect_latest(settings_db):
    row = settings_db.execute('SELECT enabled FROM auto_connect_latest_config WHERE id = 1').fetchone()
    return bool(row[0]) if row else DEFAULT_AUTO_CONNECT_LATEST


def _set_auto_connect_latest(settings_db, enabled):
    settings_db.execute(
        'UPDATE auto_connect_latest_config SET enabled = ?, updated_at = ? WHERE id = 1',
        (1 if enabled else 0, datetime.datetime.now(datetime.timezone.utc).isoformat()),
    )
    settings_db.commit()


@app.route('/api/auto-connect-latest')
def get_auto_connect_latest():
    return jsonify({'enabled': _get_auto_connect_latest(get_settings_db())})


@app.route('/api/auto-connect-latest', methods=['POST'])
def set_auto_connect_latest():
    enabled = (request.get_json(silent=True) or {}).get('enabled')
    if not isinstance(enabled, bool):
        return jsonify({'error': 'enabled must be a boolean'}), 400
    _set_auto_connect_latest(get_settings_db(), enabled)
    return jsonify({'ok': True, 'enabled': enabled})


def _get_auto_connect_latest_interval(settings_db):
    row = settings_db.execute(
        'SELECT interval_seconds FROM auto_connect_latest_interval_config WHERE id = 1'
    ).fetchone()
    return row[0] if row else DEFAULT_AUTO_CONNECT_LATEST_INTERVAL_SECONDS


def _set_auto_connect_latest_interval(settings_db, interval_seconds):
    settings_db.execute(
        'UPDATE auto_connect_latest_interval_config SET interval_seconds = ?, updated_at = ? WHERE id = 1',
        (interval_seconds, datetime.datetime.now(datetime.timezone.utc).isoformat()),
    )
    settings_db.commit()


@app.route('/api/auto-connect-latest-interval')
def get_auto_connect_latest_interval():
    return jsonify({'interval_seconds': _get_auto_connect_latest_interval(get_settings_db())})


@app.route('/api/auto-connect-latest-interval', methods=['POST'])
def set_auto_connect_latest_interval():
    interval_seconds = (request.get_json(silent=True) or {}).get('interval_seconds')
    if (
        not isinstance(interval_seconds, int) or isinstance(interval_seconds, bool)
        or not (MIN_AUTO_CONNECT_LATEST_INTERVAL_SECONDS <= interval_seconds <= MAX_AUTO_CONNECT_LATEST_INTERVAL_SECONDS)
    ):
        return jsonify({
            'error': f'interval_seconds must be an integer between '
                     f'{MIN_AUTO_CONNECT_LATEST_INTERVAL_SECONDS} and {MAX_AUTO_CONNECT_LATEST_INTERVAL_SECONDS}',
        }), 400
    _set_auto_connect_latest_interval(get_settings_db(), interval_seconds)
    return jsonify({'ok': True, 'interval_seconds': interval_seconds})


def _get_periodic_restart(settings_db):
    row = settings_db.execute('SELECT enabled FROM periodic_restart_config WHERE id = 1').fetchone()
    return bool(row[0]) if row else DEFAULT_PERIODIC_RESTART


def _set_periodic_restart(settings_db, enabled):
    settings_db.execute(
        'UPDATE periodic_restart_config SET enabled = ?, updated_at = ? WHERE id = 1',
        (1 if enabled else 0, datetime.datetime.now(datetime.timezone.utc).isoformat()),
    )
    settings_db.commit()


@app.route('/api/periodic-restart')
def get_periodic_restart():
    return jsonify({'enabled': _get_periodic_restart(get_settings_db())})


@app.route('/api/periodic-restart', methods=['POST'])
def set_periodic_restart():
    enabled = (request.get_json(silent=True) or {}).get('enabled')
    if not isinstance(enabled, bool):
        return jsonify({'error': 'enabled must be a boolean'}), 400
    _set_periodic_restart(get_settings_db(), enabled)
    return jsonify({'ok': True, 'enabled': enabled})


def _get_periodic_restart_interval(settings_db):
    row = settings_db.execute(
        'SELECT interval_minutes FROM periodic_restart_interval_config WHERE id = 1'
    ).fetchone()
    return row[0] if row else DEFAULT_PERIODIC_RESTART_INTERVAL_MINUTES


def _set_periodic_restart_interval(settings_db, interval_minutes):
    settings_db.execute(
        'UPDATE periodic_restart_interval_config SET interval_minutes = ?, updated_at = ? WHERE id = 1',
        (interval_minutes, datetime.datetime.now(datetime.timezone.utc).isoformat()),
    )
    settings_db.commit()


@app.route('/api/periodic-restart-interval')
def get_periodic_restart_interval():
    return jsonify({'interval_minutes': _get_periodic_restart_interval(get_settings_db())})


@app.route('/api/periodic-restart-interval', methods=['POST'])
def set_periodic_restart_interval():
    interval_minutes = (request.get_json(silent=True) or {}).get('interval_minutes')
    if (
        not isinstance(interval_minutes, int) or isinstance(interval_minutes, bool)
        or not (MIN_PERIODIC_RESTART_INTERVAL_MINUTES <= interval_minutes <= MAX_PERIODIC_RESTART_INTERVAL_MINUTES)
    ):
        return jsonify({
            'error': f'interval_minutes must be an integer between '
                     f'{MIN_PERIODIC_RESTART_INTERVAL_MINUTES} and {MAX_PERIODIC_RESTART_INTERVAL_MINUTES}',
        }), 400
    _set_periodic_restart_interval(get_settings_db(), interval_minutes)
    return jsonify({'ok': True, 'interval_minutes': interval_minutes})


@app.route('/api/device-connections/clear', methods=['POST'])
def clear_device_connections():
    get_connections_db().execute('DELETE FROM device_connections')
    get_connections_db().commit()
    return jsonify({'ok': True})


def _get_columns(settings_db):
    rows = settings_db.execute(
        'SELECT column_key, visible, width FROM column_config ORDER BY position'
    ).fetchall()
    return [{'key': key, 'visible': bool(visible), 'width': width} for key, visible, width in rows]


def _set_columns(settings_db, columns):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for position, col in enumerate(columns):
        settings_db.execute(
            'UPDATE column_config SET position = ?, visible = ?, width = ?, updated_at = ? WHERE column_key = ?',
            (position, 1 if col['visible'] else 0, int(round(col['width'])), now, col['key']),
        )
    settings_db.commit()


@app.route('/api/columns')
def get_columns():
    return jsonify({'columns': _get_columns(get_settings_db())})


@app.route('/api/columns', methods=['POST'])
def set_columns():
    data = request.get_json(silent=True) or {}
    columns = data.get('columns')
    if not isinstance(columns, list) or not all(
        isinstance(c, dict) and isinstance(c.get('key'), str) and isinstance(c.get('visible'), bool)
        and isinstance(c.get('width'), (int, float)) and not isinstance(c.get('width'), bool)
        and MIN_COLUMN_WIDTH <= c.get('width') <= MAX_COLUMN_WIDTH
        for c in columns
    ):
        return jsonify({
            'error': f'columns must be a list of {{key, visible, width}} objects, '
                     f'width between {MIN_COLUMN_WIDTH} and {MAX_COLUMN_WIDTH}',
        }), 400
    keys = [c['key'] for c in columns]
    if set(keys) != ALLOWED_COLUMNS or len(keys) != len(ALLOWED_COLUMNS):
        return jsonify({'error': f'columns must include exactly one each of: {", ".join(DEFAULT_COLUMN_ORDER)}'}), 400
    _set_columns(get_settings_db(), columns)
    return jsonify({'columns': _get_columns(get_settings_db())})


def _get_theme_colors(settings_db):
    rows = settings_db.execute('SELECT color_key, color_value FROM theme_config').fetchall()
    colors = dict(DEFAULT_THEME_COLORS)
    colors.update({key: value for key, value in rows if key in ALLOWED_THEME_COLOR_KEYS})
    return colors


def _set_theme_colors(settings_db, colors):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for key, value in colors.items():
        settings_db.execute(
            'UPDATE theme_config SET color_value = ?, updated_at = ? WHERE color_key = ?',
            (value, now, key),
        )
    settings_db.commit()


@app.route('/api/theme-colors')
def get_theme_colors():
    return jsonify({'colors': _get_theme_colors(get_settings_db())})


@app.route('/api/theme-colors', methods=['POST'])
def set_theme_colors():
    data = request.get_json(silent=True) or {}
    colors = data.get('colors')
    if not isinstance(colors, dict) or not all(
        isinstance(value, str) and THEME_COLOR_HEX_RE.match(value) for value in colors.values()
    ):
        return jsonify({'error': 'colors must be an object mapping each key to a #rrggbb hex string'}), 400
    if set(colors) != ALLOWED_THEME_COLOR_KEYS:
        return jsonify({'error': f'colors must include exactly one each of: {", ".join(sorted(ALLOWED_THEME_COLOR_KEYS))}'}), 400
    _set_theme_colors(get_settings_db(), colors)
    return jsonify({'colors': _get_theme_colors(get_settings_db())})


def _launch_monitor(settings_db):
    monitor_args = [
        sys.executable, '-u', BLUEZ_MONITOR_PATH,
        '--interface', _get_interface(settings_db),
        '--scan-mode', _get_scan_mode(settings_db),
    ]
    if DEBUG_MODE:
        monitor_args.append('--debug')
    subprocess.Popen(
        monitor_args,
        cwd=SCRIPT_DIR,
        stdout=None,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


@app.route('/api/monitor/start', methods=['POST'])
def start_monitor():
    settings_db = get_settings_db()
    running, _ = _monitor_status_row(settings_db)
    if running:
        return jsonify({'ok': True, 'already_running': True})
    _launch_monitor(settings_db)
    return jsonify({'ok': True}), 202


@app.route('/api/monitor/stop', methods=['POST'])
def stop_monitor():
    settings_db = get_settings_db()
    if not _stop_monitor(settings_db):
        return jsonify({'error': 'bluez_monitor.py would not stop even after a forced kill'}), 504
    return jsonify({'ok': True})


@app.route('/api/monitor/restart', methods=['POST'])
def restart_monitor():
    settings_db = get_settings_db()
    if not _stop_monitor(settings_db):
        return jsonify({'error': 'bluez_monitor.py would not stop even after a forced kill'}), 504
    _launch_monitor(settings_db)
    return jsonify({'ok': True}), 202


@app.route('/api/monitor/clear-db', methods=['POST'])
def clear_db():
    settings_db = get_settings_db()
    if not _stop_monitor(settings_db):
        return jsonify({
            'error': 'bluez_monitor.py would not stop even after a forced kill -- database not cleared',
        }), 504

    for path in glob.glob(DB_PATH + '*') + glob.glob(HISTORIES_DB_PATH + '*'):
        os.remove(path)

    _launch_monitor(settings_db)
    return jsonify({'ok': True}), 202


def _theme_style_tag(settings_db):
    colors = _get_theme_colors(settings_db)
    declarations = ' '.join(f'--theme-{key}: {value};' for key, value in colors.items())
    return f'<style>:root {{ {declarations} }}</style>'


@app.route('/logo.png')
def logo():
    return send_from_directory(os.path.join(SCRIPT_DIR, 'data'), 'logo.png')


@app.route('/')
def index():
    with open(os.path.join(ASSETS_DIR, 'index.html')) as f:
        html = f.read().replace('<head>', '<head>\n' + _theme_style_tag(get_settings_db()), 1)
    return Response(html, mimetype='text/html')


@app.route('/settings')
def settings_page():
    with open(os.path.join(ASSETS_DIR, 'settings.html')) as f:
        html = f.read().replace('<head>', '<head>\n' + _theme_style_tag(get_settings_db()), 1)
    return Response(html, mimetype='text/html')


if __name__ == '__main__':
    _parser = argparse.ArgumentParser(description="""Read-only web viewer for bluetooth.db: a Gantt-style chart of device
presence (device_appearances table). The chart is drawn client
side from /api/dots, which returns only what's needed to plot each session
and label its row (which device, its alias and classification if any, its
[start, end) time range, obs_count for the appearances total, rssi_last for
its most recent signal reading, and uuid_count for its distinct advertised
service UUIDs) -- not the full appearances row (rssi_best/rssi_avg, etc).
/api/points is the point-level counterpart: one entry per raw
advert (raw_observations) with its seen_at/rssi, for a toggle-able view that
plots individual observations instead of folded presence windows.

Device tags (user-supplied labels, added/removed from the device detail
modal) and app-level state (bluez_monitor.py's running/stopped status) live
in a separate database, settings.db (see get_settings_db) -- NOT in
bluetooth.db, which stays read-only here and is otherwise only ever written
by bluez_monitor.py. Keeping this in its own file means this app never
writes to bluetooth.db at all, so there's no lock contention with that
separate process to manage in the first place.""")
    _parser.add_argument(
        '--debug', action='store_true',
        help="Forward --debug to every bluez_monitor.py launch, so it writes "
             "data/errors.log (default: errors are swallowed, no file created)",
    )
    DEBUG_MODE = _parser.parse_args().debug

    with app.app_context():
        settings_db = get_settings_db()
        if _get_auto_start(settings_db) and not _monitor_status_row(settings_db)[0]:
            _launch_monitor(settings_db)

    app.run(host='0.0.0.0', port=5000, threaded=True)
