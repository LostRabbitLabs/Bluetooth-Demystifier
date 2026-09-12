#!/usr/bin/env python3
import argparse
import datetime
import logging
import os
import queue
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid as uuid_module

import dbus
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

ANSI_BLUE = '\033[94m'
ANSI_RESET = '\033[0m'
BLUEZ_SERVICE = 'org.bluez'
ADAPTER_IFACE = 'org.bluez.Adapter1'
DEVICE_IFACE = 'org.bluez.Device1'
GATT_SERVICE_IFACE = 'org.bluez.GattService1'
GATT_CHRC_IFACE = 'org.bluez.GattCharacteristic1'
PROPS_IFACE = 'org.freedesktop.DBus.Properties'
OM_IFACE = 'org.freedesktop.DBus.ObjectManager'
AGENT_IFACE = 'org.bluez.Agent1'
AGENT_MANAGER_IFACE = 'org.bluez.AgentManager1'
AGENT_PATH = '/lostrabbitlabs/bluetooth_demystifier/agent'
AGENT_CAPABILITY = 'KeyboardDisplay'
DEFAULT_INTERFACE = 'hci0'
ADAPTER_PATH = None
AUTO_CONNECT = False
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, 'data')
DB_DIR = os.path.join(DATA_DIR, 'DBs')
BASELINE_DIR = os.path.join(DB_DIR, 'baseline')
BASELINE_DB_PATH = os.path.join(BASELINE_DIR, 'baseline.db')
SETTINGS_BASELINE_DB_PATH = os.path.join(BASELINE_DIR, 'settings_baseline.db')
DB_PATH = os.path.join(DB_DIR, 'bluetooth.db')
ERROR_LOG_PATH = os.path.join(DATA_DIR, 'errors.log')
SETTINGS_DB_PATH = os.path.join(DB_DIR, 'settings.db')
HISTORIES_DB_PATH = os.path.join(DB_DIR, 'bluetooth_histories.db')

logger = logging.getLogger('bluez_monitor')
logger.setLevel(logging.ERROR)


def _configure_error_logging(debug):
    if debug:
        handler = logging.FileHandler(ERROR_LOG_PATH)
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        logger.addHandler(handler)
    else:
        logger.addHandler(logging.NullHandler())


def log_uncaught_exception(exc_type, exc_value, exc_traceback):
    logger.error("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))
    sys.__excepthook__(exc_type, exc_value, exc_traceback)

sys.excepthook = log_uncaught_exception
SESSION_TIMEOUT_SECONDS = 300
COMMIT_INTERVAL_SECONDS = 2
CONNECT_POLL_INTERVAL_SECONDS = 2
DEFAULT_CONNECT_TIMEOUT_SECONDS = 3
DEFAULT_AUTO_CONNECT_LATEST = False
DEFAULT_AUTO_CONNECT_LATEST_INTERVAL_SECONDS = 3
AUTO_CONNECT_LATEST_RETRY_AFTER_SECONDS = 3600
DEFAULT_PERIODIC_RESTART = True
DEFAULT_PERIODIC_RESTART_INTERVAL_MINUTES = 60
DEV_PATH_RE = re.compile(r'/dev_([0-9A-Fa-f_]{17})')
BASE_UUID_16_RE = re.compile(r'^0000([0-9a-f]{4})-0000-1000-8000-00805f9b34fb$', re.IGNORECASE)
MANUFACTURERDATA_NAMES = {}
CATEGORY_NAMES = {}
OUIDS_BY_OUI = {}
UUID_LOOKUPS_BY_UUID = {}

bus = None
om = None
db = None

histories_db = None
adapter = None
connecting = set()
resolved = set()

manual_connections = set()

pending_connects = set()
path_to_device_id = {}
bluetoothctl_proc = None


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def address_from_path(path):
    match = DEV_PATH_RE.search(path)
    if not match:
        return None
    return match.group(1).replace('_', ':').upper()


def device_path_for_address(address):
    return f"{ADAPTER_PATH}/dev_{address.upper().replace(':', '_')}"


def _connect_settings_db():
    conn = sqlite3.connect(SETTINGS_DB_PATH, timeout=5)
    conn.execute('PRAGMA journal_mode = WAL')
    return conn


def _set_monitor_status(running):
    conn = _connect_settings_db()
    try:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS monitor_status (
                id         INTEGER PRIMARY KEY CHECK (id = 1),
                running    INTEGER NOT NULL DEFAULT 0,
                pid        INTEGER,
                updated_at TEXT NOT NULL
            )
        ''')
        conn.execute(
            'INSERT INTO monitor_status (id, running, pid, updated_at) VALUES (1, ?, ?, ?) '
            'ON CONFLICT (id) DO UPDATE SET running = excluded.running, pid = excluded.pid, '
            'updated_at = excluded.updated_at',
            (1 if running else 0, os.getpid() if running else None, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def _set_device_connection_state(address, state, error=None):
    histories_db.execute('''
        CREATE TABLE IF NOT EXISTS device_connections (
            address    TEXT PRIMARY KEY,
            state      TEXT NOT NULL CHECK (state IN ('connecting', 'connected', 'disconnected', 'error')),
            error      TEXT,
            updated_at TEXT NOT NULL
        )
    ''')
    histories_db.execute(
        'INSERT INTO device_connections (address, state, error, updated_at) VALUES (?, ?, ?, ?) '
        'ON CONFLICT (address) DO UPDATE SET state = excluded.state, error = excluded.error, '
        'updated_at = excluded.updated_at',
        (address, state, error, now_iso()),
    )
    histories_db.commit()


def _record_auto_connect_success(address):
    histories_db.execute('''
        CREATE TABLE IF NOT EXISTS auto_connect_successes (
            address            TEXT PRIMARY KEY,
            first_connected_at TEXT NOT NULL,
            last_connected_at  TEXT NOT NULL
        )
    ''')
    ts = now_iso()
    histories_db.execute(
        'INSERT INTO auto_connect_successes (address, first_connected_at, last_connected_at) '
        'VALUES (?, ?, ?) '
        'ON CONFLICT (address) DO UPDATE SET last_connected_at = excluded.last_connected_at',
        (address, ts, ts),
    )
    histories_db.commit()


def _get_connect_timeout_seconds():
    try:
        conn = _connect_settings_db()
        try:
            row = conn.execute('SELECT timeout_seconds FROM connect_timeout_config WHERE id = 1').fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return DEFAULT_CONNECT_TIMEOUT_SECONDS
    return row[0] if row else DEFAULT_CONNECT_TIMEOUT_SECONDS


def _get_auto_connect_latest_enabled():
    try:
        conn = _connect_settings_db()
        try:
            row = conn.execute('SELECT enabled FROM auto_connect_latest_config WHERE id = 1').fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return DEFAULT_AUTO_CONNECT_LATEST
    return bool(row[0]) if row else DEFAULT_AUTO_CONNECT_LATEST


def _get_auto_connect_latest_interval_seconds():
    try:
        conn = _connect_settings_db()
        try:
            row = conn.execute(
                'SELECT interval_seconds FROM auto_connect_latest_interval_config WHERE id = 1'
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return DEFAULT_AUTO_CONNECT_LATEST_INTERVAL_SECONDS
    return row[0] if row else DEFAULT_AUTO_CONNECT_LATEST_INTERVAL_SECONDS


def _get_periodic_restart_enabled():
    try:
        conn = _connect_settings_db()
        try:
            row = conn.execute('SELECT enabled FROM periodic_restart_config WHERE id = 1').fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return DEFAULT_PERIODIC_RESTART
    return bool(row[0]) if row else DEFAULT_PERIODIC_RESTART


def _get_periodic_restart_interval_seconds():
    try:
        conn = _connect_settings_db()
        try:
            row = conn.execute(
                'SELECT interval_minutes FROM periodic_restart_interval_config WHERE id = 1'
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return DEFAULT_PERIODIC_RESTART_INTERVAL_MINUTES * 60
    return (row[0] if row else DEFAULT_PERIODIC_RESTART_INTERVAL_MINUTES) * 60


def _latest_observed_address():
    row = db.execute('SELECT address FROM devices ORDER BY last_seen_at DESC LIMIT 1').fetchone()
    return row[0] if row else None


def _recently_connected(address):
    row = histories_db.execute(
        'SELECT updated_at FROM device_connections WHERE address = ?', (address,)
    ).fetchone()
    if not row:
        return False
    age = (datetime.datetime.now(datetime.timezone.utc) - datetime.datetime.fromisoformat(row[0])).total_seconds()
    return age < AUTO_CONNECT_LATEST_RETRY_AFTER_SECONDS


def _auto_connect_latest_attempt():
    if not _get_auto_connect_latest_enabled():
        return
    if pending_connects:
        return
    address = _latest_observed_address()
    if address is None or _recently_connected(address):
        return
    connect_device(device_path_for_address(address), manual=False)


def auto_connect_latest_tick():
    try:
        _auto_connect_latest_attempt()
    except Exception:
        logger.error("auto_connect_latest_tick: attempt failed, will retry next tick", exc_info=True)
    GLib.timeout_add_seconds(_get_auto_connect_latest_interval_seconds(), auto_connect_latest_tick)
    return False


def _seed_reference_tables_if_needed(conn):
    conn.execute('''
        CREATE TABLE IF NOT EXISTS manufacturerdata_names (
            manufacturerdata_id INTEGER PRIMARY KEY,
            category_id          INTEGER REFERENCES categories(category_id) ON DELETE SET NULL,
            name                 TEXT NOT NULL,
            icon                 TEXT,
            color                TEXT CHECK (color IN ('RED','YELLOW','GREEN','PINK'))
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS categories (
            category_id INTEGER PRIMARY KEY,
            name        TEXT NOT NULL UNIQUE,
            description TEXT,
            icon        TEXT,
            color       TEXT NOT NULL CHECK (color IN ('RED','YELLOW','GREEN','PINK'))
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS ouids (
            ouid_id     INTEGER PRIMARY KEY,
            category_id INTEGER REFERENCES categories(category_id) ON DELETE SET NULL,
            oui         TEXT NOT NULL,
            org_name    TEXT NOT NULL
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_ouids_oui ON ouids(oui)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_ouids_category ON ouids(category_id)')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS uuid_lookups (
            uuid_lookup_id INTEGER PRIMARY KEY,
            uuid           TEXT NOT NULL UNIQUE,
            name           TEXT NOT NULL,
            type           TEXT NOT NULL CHECK (type IN ('vendor','info')),
            icon           TEXT
        )
    ''')
    _ensure_uuid_lookup_icon_column(conn)
    _ensure_manufacturerdata_category_column(conn)
    already_seeded = conn.execute('SELECT 1 FROM categories LIMIT 1').fetchone() is not None
    if already_seeded:
        return
    conn.execute('ATTACH DATABASE ? AS ref', (SETTINGS_BASELINE_DB_PATH,))
    try:
        if _attached_table_has_column(conn, 'ref', 'manufacturerdata_names', 'category_id'):
            conn.execute(
                'INSERT OR IGNORE INTO manufacturerdata_names '
                '(manufacturerdata_id, category_id, name, icon, color) '
                'SELECT manufacturerdata_id, category_id, name, icon, color FROM ref.manufacturerdata_names'
            )
        else:
            conn.execute(
                'INSERT OR IGNORE INTO manufacturerdata_names (manufacturerdata_id, name, icon, color) '
                'SELECT manufacturerdata_id, name, icon, color FROM ref.manufacturerdata_names'
            )
        conn.execute('INSERT OR IGNORE INTO categories SELECT * FROM ref.categories')
        conn.execute('INSERT OR IGNORE INTO ouids SELECT * FROM ref.ouids')
        conn.execute('INSERT OR IGNORE INTO uuid_lookups SELECT * FROM ref.uuid_lookups')
        conn.commit()
    finally:
        conn.execute('DETACH DATABASE ref')


def _attached_table_has_column(conn, schema, table, column):
    return any(
        row[1] == column
        for row in conn.execute(f'PRAGMA {schema}.table_info({table})').fetchall()
    )


def _ensure_uuid_lookup_icon_column(conn):
    existing_columns = {row[1] for row in conn.execute('PRAGMA table_info(uuid_lookups)').fetchall()}
    if not existing_columns or 'icon' in existing_columns:
        return
    conn.execute('ALTER TABLE uuid_lookups ADD COLUMN icon TEXT')
    conn.execute('ATTACH DATABASE ? AS ref', (SETTINGS_BASELINE_DB_PATH,))
    try:
        conn.execute('''
            UPDATE uuid_lookups SET icon = (
                SELECT r.icon FROM ref.uuid_lookups AS r
                WHERE r.uuid = uuid_lookups.uuid
            )
        ''')
        conn.commit()
    finally:
        conn.execute('DETACH DATABASE ref')


def _ensure_manufacturerdata_category_column(conn):
    existing_columns = {row[1] for row in conn.execute('PRAGMA table_info(manufacturerdata_names)').fetchall()}
    if not existing_columns or 'category_id' in existing_columns:
        return
    conn.execute('ALTER TABLE manufacturerdata_names ADD COLUMN category_id INTEGER REFERENCES categories(category_id) ON DELETE SET NULL')
    conn.execute('ATTACH DATABASE ? AS ref', (SETTINGS_BASELINE_DB_PATH,))
    try:
        if _attached_table_has_column(conn, 'ref', 'manufacturerdata_names', 'category_id'):
            conn.execute('''
                UPDATE manufacturerdata_names SET category_id = (
                    SELECT r.category_id FROM ref.manufacturerdata_names AS r
                    WHERE r.manufacturerdata_id = manufacturerdata_names.manufacturerdata_id
                )
            ''')
            conn.commit()
    finally:
        conn.execute('DETACH DATABASE ref')


def _load_reference_data():
    global MANUFACTURERDATA_NAMES, CATEGORY_ALIAS_KEYWORDS, CATEGORY_NAMES, OUIDS_BY_OUI, UUID_LOOKUPS_BY_UUID
    conn = _connect_settings_db()
    try:
        _seed_reference_tables_if_needed(conn)
        MANUFACTURERDATA_NAMES = dict(
            conn.execute('SELECT manufacturerdata_id, name FROM manufacturerdata_names').fetchall()
        )
        category_rows = conn.execute('SELECT category_id, name, description FROM categories').fetchall()
        CATEGORY_ALIAS_KEYWORDS = build_category_alias_keywords(category_rows)
        CATEGORY_NAMES = {category_id: name for category_id, name, _ in category_rows}
        OUIDS_BY_OUI = {}
        for ouid_id, oui, org_name in conn.execute('SELECT ouid_id, oui, org_name FROM ouids').fetchall():
            OUIDS_BY_OUI.setdefault(oui, []).append((ouid_id, org_name))
        UUID_LOOKUPS_BY_UUID = {
            uuid_value: (uuid_lookup_id, name, lookup_type)
            for uuid_lookup_id, uuid_value, name, lookup_type
            in conn.execute('SELECT uuid_lookup_id, uuid, name, type FROM uuid_lookups').fetchall()
        }
    finally:
        conn.close()


def init_db():
    global db
    if not os.path.exists(DB_PATH) or os.path.getsize(DB_PATH) == 0:
        shutil.copyfile(BASELINE_DB_PATH, DB_PATH)
    db = sqlite3.connect(DB_PATH)
    db.execute('PRAGMA journal_mode = WAL')
    db.execute('PRAGMA foreign_keys = ON')
    _load_reference_data()


def init_histories_db():
    global histories_db
    histories_db = sqlite3.connect(HISTORIES_DB_PATH)
    histories_db.execute('PRAGMA journal_mode = WAL')
    histories_db.execute('''
        CREATE TABLE IF NOT EXISTS device_alias_history (
            history_id    INTEGER PRIMARY KEY,
            device_id     TEXT NOT NULL,
            alias         TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at  TEXT NOT NULL
        )
    ''')
    histories_db.execute('CREATE INDEX IF NOT EXISTS idx_alias_history_device ON device_alias_history(device_id)')
    histories_db.execute(
        'CREATE INDEX IF NOT EXISTS idx_alias_history_time ON device_alias_history(first_seen_at, last_seen_at)'
    )
    histories_db.execute('''
        CREATE TABLE IF NOT EXISTS device_manufacturerdata_history (
            history_id            INTEGER PRIMARY KEY,
            device_id             TEXT NOT NULL,
            manufacturerdata_id   INTEGER,
            manufacturerdata_name TEXT,
            first_seen_at         TEXT NOT NULL,
            last_seen_at          TEXT NOT NULL
        )
    ''')
    histories_db.execute(
        'CREATE INDEX IF NOT EXISTS idx_manufacturerdata_history_device '
        'ON device_manufacturerdata_history(device_id)'
    )
    histories_db.execute(
        'CREATE INDEX IF NOT EXISTS idx_manufacturerdata_history_time '
        'ON device_manufacturerdata_history(first_seen_at, last_seen_at)'
    )
    histories_db.execute('''
        CREATE TABLE IF NOT EXISTS device_service_data_history (
            device_id     TEXT    NOT NULL,
            uuid_id       INTEGER NOT NULL,
            data_hex      TEXT    NOT NULL,
            seen_at       TEXT    NOT NULL
        )
    ''')
    histories_db.execute(
        'CREATE INDEX IF NOT EXISTS idx_device_service_data_history_device '
        'ON device_service_data_history (device_id, seen_at)'
    )


def get_or_create_device_id(address, address_type):
    row = db.execute(
        'SELECT device_id FROM devices WHERE address = ? AND address_type = ?',
        (address, address_type),
    ).fetchone()
    if row:
        return row[0], False
    device_id = str(uuid_module.uuid4())
    ts = now_iso()
    db.execute(
        'INSERT INTO devices (device_id, address, address_type, bearer, first_seen_at, last_seen_at) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (device_id, address, address_type, classify_bearer(address_type, None), ts, ts),
    )
    return device_id, True


def classify_bearer(address_type, class_of_device):
    if address_type == 'random':
        return 'le'
    if class_of_device is not None:
        return 'bredr'
    return 'le'


def _device_matches_scan_mode(address_type, class_of_device):
    if SCAN_MODE == 'auto':
        return True
    return classify_bearer(address_type, class_of_device) == SCAN_MODE


def extract_oui(address):
    return address.replace(':', '').upper()[:6]


def match_device_ouid(device_id, address, address_type):
    if address_type != 'public':
        return
    oui = extract_oui(address)
    rows = OUIDS_BY_OUI.get(oui, [])
    if not rows:
        return
    ts = now_iso()
    db.executemany(
        'INSERT OR IGNORE INTO device_ouid_matches (device_id, ouid_id, matched_at) VALUES (?, ?, ?)',
        [(device_id, ouid_id, ts) for ouid_id, _ in rows],
    )
    orgs = ', '.join(org_name for _, org_name in rows)
    print(f"[OUIDMatch] {address} -> {orgs}")

ALIAS_MATCH_STOPWORDS = {
    'a', 'an', 'and', 'or', 'of', 'the', 'n', 'no', 'we', 'have', 'this',
    'that', 'about', 'through', 'other', 'present', 'like', 'device', 'devices',
    'product', 'service', 'technology', 'personal', 'inc',
}
ALIAS_MATCH_EXCLUDED_CATEGORIES = {'TAGGED', 'UNKNOWN', 'PRIVATE'}
ALIAS_MATCH_TERM_OVERRIDES = {
    'gov': re.compile(r'government|gov '),
}
CATEGORY_ALIAS_KEYWORDS = {}


def _alias_term(word):
    return ALIAS_MATCH_TERM_OVERRIDES.get(word, word)


def _term_matches(term, alias_lower):
    if isinstance(term, re.Pattern):
        return term.search(alias_lower) is not None
    return term in alias_lower


def singularize(word):
    if word.endswith(('ches', 'shes', 'xes', 'zes', 'sses')):
        return word[:-2]
    if word.endswith('s') and not word.endswith('ss'):
        return word[:-1]
    return word


def build_category_alias_keywords(rows):
    rows = [row for row in rows if row[1] not in ALIAS_MATCH_EXCLUDED_CATEGORIES]
    name_tokens = {}
    for category_id, name, _ in rows:
        terms = set()
        for part in re.split(r'[/\s]+', name):
            part = part.strip().lower()
            if len(part) >= 2 and part not in ALIAS_MATCH_STOPWORDS:
                terms.add(part)
        name_tokens[category_id] = terms
    all_name_tokens = set().union(*name_tokens.values()) if name_tokens else set()
    keywords = {}
    for category_id, name, description in rows:
        terms = {_alias_term(t) for t in name_tokens[category_id]}
        other_name_tokens = all_name_tokens - name_tokens[category_id]
        for word in re.findall(r"[A-Za-z']+", description or ''):
            word = singularize(word.lower().strip("'"))
            if len(word) < 3 or word in ALIAS_MATCH_STOPWORDS or word in other_name_tokens:
                continue
            terms.add(_alias_term(word))
        if terms:
            keywords[category_id] = terms
    return keywords


def match_device_alias_categories(device_id, alias):
    ts = now_iso()
    db.execute('DELETE FROM device_alias_category_matches WHERE device_id = ?', (device_id,))
    if not alias:
        return
    alias_lower = alias.lower()
    matched_ids = [
        category_id for category_id, terms in CATEGORY_ALIAS_KEYWORDS.items()
        if any(_term_matches(term, alias_lower) for term in terms)
    ]
    if not matched_ids:
        return
    db.executemany(
        'INSERT INTO device_alias_category_matches (device_id, category_id, matched_at) VALUES (?, ?, ?)',
        [(device_id, category_id, ts) for category_id in matched_ids],
    )
    names = ', '.join(CATEGORY_NAMES[cid] for cid in matched_ids)
    print(f"[AliasMatch] {alias!r} -> {names}")


def extract_manufacturerdata(manufacturer_data):
    if not manufacturer_data:
        return None, None
    manufacturerdata_id = int(next(iter(manufacturer_data.keys())))
    return manufacturerdata_id, MANUFACTURERDATA_NAMES.get(manufacturerdata_id)


def extract_uuid16(full_uuid):
    match = BASE_UUID_16_RE.match(full_uuid)
    if not match:
        return None
    return match.group(1).upper()


def match_service_uuid(uuid_id, full_uuid):
    short_uuid = extract_uuid16(full_uuid)
    if short_uuid is None:
        return
    row = UUID_LOOKUPS_BY_UUID.get(short_uuid)
    if row is None:
        return
    uuid_lookup_id, name, lookup_type = row
    db.execute(
        'UPDATE service_uuids SET uuid_lookup_id = ? WHERE uuid_id = ?',
        (uuid_lookup_id, uuid_id),
    )
    print(f"[UUIDMatch] {short_uuid} -> {name} ({lookup_type})")


def get_or_create_service_uuid(uuid_str):
    cur = db.execute('INSERT OR IGNORE INTO service_uuids (uuid) VALUES (?)', (uuid_str,))
    uuid_id = db.execute(
        'SELECT uuid_id FROM service_uuids WHERE uuid = ?', (uuid_str,)
    ).fetchone()[0]
    if cur.rowcount > 0:
        match_service_uuid(uuid_id, uuid_str)
    return uuid_id


def record_service_uuids(device_id, uuids):
    if not uuids:
        return
    ts = now_iso()
    for uuid_str in uuids:
        uuid_id = get_or_create_service_uuid(uuid_str)
        db.execute(
            'INSERT OR IGNORE INTO device_service_uuids (device_id, uuid_id, first_seen_at) '
            'VALUES (?, ?, ?)',
            (device_id, uuid_id, ts),
        )


def record_service_data(device_id, service_data):
    if not service_data:
        return
    ts = now_iso()
    for uuid_str, data in service_data.items():
        uuid_id = get_or_create_service_uuid(str(uuid_str))
        data_hex = bytes(bytearray(data)).hex()
        row = db.execute(
            'SELECT 1 FROM device_service_data WHERE device_id = ? AND uuid_id = ?',
            (device_id, uuid_id),
        ).fetchone()
        histories_db.execute(
            'INSERT INTO device_service_data_history '
            '(device_id, uuid_id, data_hex, seen_at) VALUES (?, ?, ?, ?)',
            (device_id, uuid_id, data_hex, ts),
        )
        if row:
            db.execute(
                'UPDATE device_service_data SET data_hex = ?, last_seen_at = ? '
                'WHERE device_id = ? AND uuid_id = ?',
                (data_hex, ts, device_id, uuid_id),
            )
        else:
            db.execute(
                'INSERT INTO device_service_data '
                '(device_id, uuid_id, data_hex, first_seen_at, last_seen_at) '
                'VALUES (?, ?, ?, ?, ?)',
                (device_id, uuid_id, data_hex, ts, ts),
            )


def parse_solicited_uuids(advertising_data):
    uuids = []
    for ad_type, raw in advertising_data.items():
        ad_type = int(ad_type)
        raw = bytes(bytearray(raw))
        if ad_type == 0x14:  # 16-bit Service Solicitation UUIDs
            for i in range(0, len(raw) - 1, 2):
                val = int.from_bytes(raw[i:i + 2], 'little')
                uuids.append(f'0000{val:04x}-0000-1000-8000-00805f9b34fb')
        elif ad_type == 0x1F:  # 32-bit Service Solicitation UUIDs
            for i in range(0, len(raw) - 3, 4):
                val = int.from_bytes(raw[i:i + 4], 'little')
                uuids.append(f'{val:08x}-0000-1000-8000-00805f9b34fb')
        elif ad_type == 0x15:  # 128-bit Service Solicitation UUIDs
            for i in range(0, len(raw) - 15, 16):
                # 128-bit UUIDs are transmitted little-endian on the wire.
                chunk = raw[i:i + 16][::-1].hex()
                uuids.append(f'{chunk[0:8]}-{chunk[8:12]}-{chunk[12:16]}-{chunk[16:20]}-{chunk[20:32]}')
    return uuids


def record_solicited_uuids(device_id, uuids):
    if not uuids:
        return
    ts = now_iso()
    for uuid_str in uuids:
        uuid_id = get_or_create_service_uuid(uuid_str)
        db.execute(
            'INSERT OR IGNORE INTO device_solicited_uuids (device_id, uuid_id, first_seen_at) '
            'VALUES (?, ?, ?)',
            (device_id, uuid_id, ts),
        )


def record_alias_history(device_id, alias):
    ts = now_iso()
    row = histories_db.execute(
        'SELECT history_id, alias FROM device_alias_history '
        'WHERE device_id = ? ORDER BY last_seen_at DESC LIMIT 1',
        (device_id,),
    ).fetchone()
    if row and row[1] == alias:
        histories_db.execute(
            'UPDATE device_alias_history SET last_seen_at = ? WHERE history_id = ?',
            (ts, row[0]),
        )
    else:
        histories_db.execute(
            'INSERT INTO device_alias_history (device_id, alias, first_seen_at, last_seen_at) '
            'VALUES (?, ?, ?, ?)',
            (device_id, alias, ts, ts),
        )


def record_manufacturerdata_history(device_id, manufacturerdata_id, manufacturerdata_name):
    ts = now_iso()
    row = histories_db.execute(
        'SELECT history_id, manufacturerdata_id, manufacturerdata_name '
        'FROM device_manufacturerdata_history '
        'WHERE device_id = ? ORDER BY last_seen_at DESC LIMIT 1',
        (device_id,),
    ).fetchone()
    if row and row[1] == manufacturerdata_id and row[2] == manufacturerdata_name:
        histories_db.execute(
            'UPDATE device_manufacturerdata_history SET last_seen_at = ? WHERE history_id = ?',
            (ts, row[0]),
        )
    else:
        histories_db.execute(
            'INSERT INTO device_manufacturerdata_history '
            '(device_id, manufacturerdata_id, manufacturerdata_name, first_seen_at, last_seen_at) '
            'VALUES (?, ?, ?, ?, ?)',
            (device_id, manufacturerdata_id, manufacturerdata_name, ts, ts),
        )


def record_presence(device_id, rssi):
    ts = now_iso()
    row = db.execute(
        'SELECT session_id, last_seen_at, obs_count, rssi_best, rssi_avg '
        'FROM device_appearances WHERE device_id = ? ORDER BY last_seen_at DESC LIMIT 1',
        (device_id,),
    ).fetchone()
    gap = None
    if row:
        gap = (datetime.datetime.fromisoformat(ts)
               - datetime.datetime.fromisoformat(row[1])).total_seconds()
    if row and gap is not None and gap <= SESSION_TIMEOUT_SECONDS:
        session_id, _, obs_count, rssi_best, rssi_avg = row
        obs_count += 1
        if rssi is not None:
            rssi_best = rssi if rssi_best is None else max(rssi_best, rssi)
            rssi_avg = rssi if rssi_avg is None else rssi_avg + (rssi - rssi_avg) / obs_count
        db.execute(
            'UPDATE device_appearances SET last_seen_at = ?, obs_count = ?, '
            'rssi_last = COALESCE(?, rssi_last), rssi_best = ?, rssi_avg = ? '
            'WHERE session_id = ?',
            (ts, obs_count, rssi, rssi_best, rssi_avg, session_id),
        )
    else:
        cur = db.execute(
            'INSERT INTO device_appearances '
            '(device_id, first_seen_at, last_seen_at, obs_count, rssi_last, rssi_best, rssi_avg) '
            'VALUES (?, ?, ?, 1, ?, ?, ?)',
            (device_id, ts, ts, rssi, rssi, rssi),
        )
        session_id = cur.lastrowid
    db.execute(
        'INSERT INTO raw_observations (session_id, device_id, seen_at, rssi) VALUES (?, ?, ?, ?)',
        (session_id, device_id, ts, rssi),
    )


def record_device(path, props):
    device_id = path_to_device_id.get(path)
    if device_id is None:
        address = props.get('Address')
        address_type = props.get('AddressType')
        if not address or not address_type:
            try:
                dev_props = dbus.Interface(bus.get_object(BLUEZ_SERVICE, path), PROPS_IFACE)
                address = address or dev_props.Get(DEVICE_IFACE, 'Address')
                address_type = address_type or dev_props.Get(DEVICE_IFACE, 'AddressType')
            except dbus.exceptions.DBusException as error:
                logger.error("Failed to fetch static properties for %s: %s", path, error)
                return None
        address = str(address)
        address_type = str(address_type)
        class_of_device = props.get('Class')
        class_of_device = int(class_of_device) if class_of_device is not None else None
        if not _device_matches_scan_mode(address_type, class_of_device):
            return None
        device_id, is_new = get_or_create_device_id(address, address_type)
        path_to_device_id[path] = device_id
        if is_new:
            match_device_ouid(device_id, address, address_type)
    else:
        address_type, stored_class_of_device = db.execute(
            'SELECT address_type, class_of_device FROM devices WHERE device_id = ?', (device_id,)
        ).fetchone()
        class_of_device = props.get('Class')
        class_of_device = int(class_of_device) if class_of_device is not None else stored_class_of_device
        if not _device_matches_scan_mode(address_type, class_of_device):
            return None
        db.execute('UPDATE devices SET last_seen_at = ? WHERE device_id = ?', (now_iso(), device_id))
    alias = props.get('Alias')
    if alias is None:
        alias = props.get('Name')
    if alias is not None:
        alias = str(alias)
        previous_alias = db.execute(
            'SELECT alias FROM devices WHERE device_id = ?', (device_id,)
        ).fetchone()[0]
        db.execute('UPDATE devices SET alias = ? WHERE device_id = ?', (alias, device_id))
        record_alias_history(device_id, alias)
        if alias != previous_alias:
            match_device_alias_categories(device_id, alias)
    manufacturer_data = props.get('ManufacturerData')
    if manufacturer_data:
        manufacturerdata_id, manufacturerdata_name = extract_manufacturerdata(manufacturer_data)
        db.execute(
            'UPDATE devices SET manufacturerdata_id = ?, manufacturerdata_name = ? '
            'WHERE device_id = ?',
            (manufacturerdata_id, manufacturerdata_name, device_id),
        )
        record_manufacturerdata_history(device_id, manufacturerdata_id, manufacturerdata_name)
    uuids = props.get('UUIDs')
    if uuids:
        record_service_uuids(device_id, [str(u) for u in uuids])
    appearance = props.get('Appearance')
    if appearance is not None:
        db.execute(
            'UPDATE devices SET appearance = ? WHERE device_id = ?',
            (int(appearance), device_id),
        )
    fresh_class_of_device = props.get('Class')
    if fresh_class_of_device is not None:
        db.execute(
            'UPDATE devices SET class_of_device = ?, bearer = ? WHERE device_id = ?',
            (int(fresh_class_of_device), classify_bearer(address_type, int(fresh_class_of_device)), device_id),
        )
    service_data = props.get('ServiceData')
    if service_data:
        record_service_data(device_id, service_data)
    advertising_data = props.get('AdvertisingData')
    if advertising_data:
        record_solicited_uuids(device_id, parse_solicited_uuids(advertising_data))
    rssi = props.get('RSSI')
    record_presence(device_id, int(rssi) if rssi is not None else None)
    return device_id


def interfaces_added(path, interfaces):
    address = address_from_path(path)
    print(f"[InterfacesAdded] {address or path}")
    if DEVICE_IFACE in interfaces:
        record_device(path, interfaces[DEVICE_IFACE])
        if AUTO_CONNECT:
            connect_device(path)


def interfaces_removed(path, interfaces):
    address = address_from_path(path)
    print(f"[InterfacesRemoved] {address or path}")
    connecting.discard(path)
    resolved.discard(path)
    manual_connections.discard(path)
    pending_connects.discard(path)
    path_to_device_id.pop(path, None)


def properties_changed(interface, changed, invalidated, path=None):
    address = address_from_path(path) if path else None
    print(f"[PropertiesChanged] {address or path}")
    if interface != DEVICE_IFACE:
        return
    record_device(path, changed)
    if changed.get('ServicesResolved'):
        resolve_gatt(path)
    if 'Connected' in changed:
        if changed['Connected']:
            pending_connects.discard(path)
            if address:
                _set_device_connection_state(address, 'connected')
                if path not in manual_connections:
                    _record_auto_connect_success(address)
        else:
            connecting.discard(path)
            resolved.discard(path)
            pending_connects.discard(path)
            if address:
                _set_device_connection_state(address, 'disconnected')


class _RejectAgent(dbus.service.Object):
    def _reject(self, method_name, device, message):
        address = address_from_path(device) or device
        print(f"[AgentRejected] {method_name} for {address}: {message}")
        logger.error("Agent rejected %s for %s: %s", method_name, address, message)
        raise dbus.exceptions.DBusException(message, name='org.bluez.Error.Rejected')

    @dbus.service.method(AGENT_IFACE, in_signature='', out_signature='')
    def Release(self):
        pass

    @dbus.service.method(AGENT_IFACE, in_signature='os', out_signature='')
    def AuthorizeService(self, device, uuid):
        pass

    @dbus.service.method(AGENT_IFACE, in_signature='o', out_signature='s')
    def RequestPinCode(self, device):
        self._reject('RequestPinCode', device, 'Rejected: pairing is disabled')

    @dbus.service.method(AGENT_IFACE, in_signature='o', out_signature='u')
    def RequestPasskey(self, device):
        self._reject('RequestPasskey', device, 'Rejected: pairing is disabled')

    @dbus.service.method(AGENT_IFACE, in_signature='ouq', out_signature='')
    def DisplayPasskey(self, device, passkey, entered):
        pass

    @dbus.service.method(AGENT_IFACE, in_signature='os', out_signature='')
    def DisplayPinCode(self, device, pincode):
        pass

    @dbus.service.method(AGENT_IFACE, in_signature='ou', out_signature='')
    def RequestConfirmation(self, device, passkey):
        self._reject('RequestConfirmation', device, 'Rejected: pairing is disabled')

    @dbus.service.method(AGENT_IFACE, in_signature='o', out_signature='')
    def RequestAuthorization(self, device):
        self._reject('RequestAuthorization', device, 'Rejected: pairing is disabled')

    @dbus.service.method(AGENT_IFACE, in_signature='', out_signature='')
    def Cancel(self):
        pass

_reject_agent = None


def register_reject_agent():
    global _reject_agent
    agent_manager = dbus.Interface(bus.get_object(BLUEZ_SERVICE, '/org/bluez'), AGENT_MANAGER_IFACE)
    if _reject_agent is None:
        _reject_agent = _RejectAgent(bus, AGENT_PATH)
    try:
        agent_manager.RegisterAgent(AGENT_PATH, AGENT_CAPABILITY)
    except dbus.exceptions.DBusException as error:
        if error.get_dbus_name() != 'org.bluez.Error.AlreadyExists':
            raise
    agent_manager.RequestDefaultAgent(AGENT_PATH)


def _reassert_default_agent():
    try:
        register_reject_agent()
    except dbus.exceptions.DBusException as error:
        logger.error("Failed to (re-)assert reject-agent as default: %s", error)


def connect_device(path, manual=False):
    if path in connecting:
        return
    connecting.add(path)
    pending_connects.add(path)
    if manual:
        manual_connections.add(path)
    address = address_from_path(path)
    if not address:
        connecting.discard(path)
        pending_connects.discard(path)
        manual_connections.discard(path)
        return
    _set_device_connection_state(address, 'connecting')
    _reassert_default_agent()
    print(f"[Connecting] {address}")
    _send_bluetoothctl(f'connect {address}')
    GLib.timeout_add_seconds(_get_connect_timeout_seconds(), lambda: _connect_timeout(path))


def _connect_timeout(path):
    if path not in pending_connects:
        return False
    pending_connects.discard(path)
    connecting.discard(path)
    manual_connections.discard(path)
    address = address_from_path(path)
    if address:
        print(f"[ConnectGaveUpWaiting] {address}")
        _set_device_connection_state(address, 'disconnected')
    return False


def clear_device_cache(adapter):
    objects = om.GetManagedObjects()
    for path, obj_interfaces in objects.items():
        if DEVICE_IFACE not in obj_interfaces:
            continue
        if not path.startswith(ADAPTER_PATH + '/'):
            continue
        try:
            adapter.RemoveDevice(path)
            print(f"[CacheCleared] {address_from_path(path) or path}")
        except dbus.exceptions.DBusException as error:
            print(f"[CacheClearFailed] {address_from_path(path) or path}: {error}")
            logger.error("Failed to remove cached device %s: %s", address_from_path(path) or path, error)


def clear_bluetooth_cache_dir(adapter_address):
    cache_dir = f'/var/lib/bluetooth/{adapter_address}/cache'
    result = subprocess.run(
        ['sudo', '-n', 'find', cache_dir, '-mindepth', '1', '-delete'],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        print(f"[CacheDirClearFailed] {cache_dir}: {stderr}")
        logger.error(
            "Failed to clear %s (passwordless sudo required): %s", cache_dir, stderr
        )
    else:
        print(f"[CacheDirCleared] {cache_dir}")


def resolve_gatt(path):
    if path in resolved:
        return
    resolved.add(path)
    address = address_from_path(path)
    print(f"[GATT] {address or path}")
    gatt_uuids = []
    objects = om.GetManagedObjects()
    for obj_path, obj_interfaces in objects.items():
        if not obj_path.startswith(path + '/'):
            continue
        if GATT_SERVICE_IFACE in obj_interfaces:
            svc = obj_interfaces[GATT_SERVICE_IFACE]
            print(f"  [Service] {obj_path}")
            print(f"    UUID = {svc.get('UUID')}")
            print(f"    Primary = {svc.get('Primary')}")
            if svc.get('UUID'):
                gatt_uuids.append(str(svc.get('UUID')))
        elif GATT_CHRC_IFACE in obj_interfaces:
            chrc = obj_interfaces[GATT_CHRC_IFACE]
            print(f"  [Characteristic] {obj_path}")
            print(f"    UUID = {chrc.get('UUID')}")
            print(f"    Flags = {chrc.get('Flags')}")
    device_id = path_to_device_id.get(path)
    if device_id is not None and gatt_uuids:
        record_service_uuids(device_id, gatt_uuids)
    elif gatt_uuids:
        logger.error("Resolved GATT UUIDs for %s but no known device_id for %s", address or path, path)
    if path not in manual_connections:
        disconnect_device(path)


def disconnect_device(path):
    connecting.discard(path)
    manual_connections.discard(path)
    pending_connects.discard(path)
    address = address_from_path(path)
    if not address:
        return
    print(f"[Disconnecting] {address}")
    _send_bluetoothctl(f'disconnect {address}')
    _set_device_connection_state(address, 'disconnected')


def poll_connect_requests():
    try:
        histories_db.execute('''
            CREATE TABLE IF NOT EXISTS device_connect_requests (
                address      TEXT PRIMARY KEY,
                action       TEXT NOT NULL CHECK (action IN ('connect', 'disconnect')),
                requested_at TEXT NOT NULL
            )
        ''')
        rows = histories_db.execute('SELECT address, action FROM device_connect_requests').fetchall()
        if rows:
            histories_db.executemany(
                'DELETE FROM device_connect_requests WHERE address = ?',
                [(address,) for address, _ in rows],
            )
            histories_db.commit()
        for address, action in rows:
            path = device_path_for_address(address)
            if action == 'connect':
                connect_device(path, manual=True)
            elif action == 'disconnect':
                disconnect_device(path)
    except Exception:
        logger.error("poll_connect_requests: tick failed, will retry next tick", exc_info=True)
    return True


def periodic_commit():
    try:
        db.commit()
        histories_db.commit()
    except Exception:
        logger.error("periodic_commit: commit failed, will retry next tick", exc_info=True)
    return True

BLUETOOTHCTL_BIN = 'bluetoothctl'
BLUETOOTHCTL_SELECT_ATTEMPTS = 5
BLUETOOTHCTL_SELECT_TIMEOUT_SECONDS = 1.5
SCAN_LE_PRIME_SECONDS = 3
SCAN_MODES = ('bredr', 'le', 'auto')
DEFAULT_SCAN_MODE = 'auto'
SCAN_MODE = DEFAULT_SCAN_MODE
_bluetoothctl_output = queue.Queue(maxsize=500)


def _send_bluetoothctl(command):
    bluetoothctl_proc.stdin.write(command + '\n')
    bluetoothctl_proc.stdin.flush()


def _pump_bluetoothctl_output(proc):
    try:
        for line in proc.stdout:
            stripped = line.rstrip('\n')
            benign = stripped.endswith('Failed to start discovery: org.bluez.Error.InProgress')
            if not benign and ('not available' in stripped or 'Failed to' in stripped):
                logger.error("bluetoothctl: %s", stripped)
            try:
                _bluetoothctl_output.put_nowait(stripped)
            except queue.Full:
                pass
    except (ValueError, OSError):
        pass


def select_bluetoothctl_controller(adapter_address):
    for attempt in range(1, BLUETOOTHCTL_SELECT_ATTEMPTS + 1):
        _send_bluetoothctl(f'select {adapter_address}')
        deadline = time.monotonic() + BLUETOOTHCTL_SELECT_TIMEOUT_SECONDS
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.error(
                    "bluetoothctl did not confirm 'select %s' within %.1fs "
                    "(attempt %d/%d)", adapter_address,
                    BLUETOOTHCTL_SELECT_TIMEOUT_SECONDS, attempt, BLUETOOTHCTL_SELECT_ATTEMPTS,
                )
                break
            try:
                line = _bluetoothctl_output.get(timeout=remaining)
            except queue.Empty:
                continue
            if adapter_address not in line:
                continue
            if '[default]' in line:
                print(f"[Controller] Selected {adapter_address}")
                return True
            if 'not available' in line:
                logger.error(
                    "bluetoothctl reported controller %s not available (attempt %d/%d)",
                    adapter_address, attempt, BLUETOOTHCTL_SELECT_ATTEMPTS,
                )
                break
        time.sleep(0.5)
    logger.error(
        "Giving up selecting controller %s in bluetoothctl after %d attempts -- "
        "scan commands may be applied to the wrong controller",
        adapter_address, BLUETOOTHCTL_SELECT_ATTEMPTS,
    )
    return False


def start_discovery(adapter_address):
    global bluetoothctl_proc
    bluetoothctl_proc = subprocess.Popen(
        [BLUETOOTHCTL_BIN],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    threading.Thread(target=_pump_bluetoothctl_output, args=(bluetoothctl_proc,), daemon=True).start()

    select_bluetoothctl_controller(adapter_address) # DO NOT CHANGE THIS ORDER
    _send_bluetoothctl('power on') # DO NOT CHANGE THIS ORDER
    _send_bluetoothctl('scan le') # DO NOT CHANGE THIS ORDER
    time.sleep(2) # DO NOT CHANGE THIS ORDER
    _send_bluetoothctl('menu scan') # DO NOT CHANGE THIS ORDER
    _send_bluetoothctl('duplicate-data on') # DO NOT CHANGE THIS ORDER
    _send_bluetoothctl('transport auto') # DO NOT CHANGE THIS ORDER
    _send_bluetoothctl('back') # DO NOT CHANGE THIS ORDER

    if SCAN_MODE == 'auto': # DO NOT CHANGE THIS ORDER
        _send_bluetoothctl('scan le') # DO NOT CHANGE THIS ORDER
        time.sleep(2) # DO NOT CHANGE THIS ORDER
        _send_bluetoothctl('menu scan') # DO NOT CHANGE THIS ORDER
        _send_bluetoothctl('duplicate-data on') # DO NOT CHANGE THIS ORDER
        _send_bluetoothctl('transport auto') # DO NOT CHANGE THIS ORDER
        _send_bluetoothctl('back') # DO NOT CHANGE THIS ORDER
        return # DO NOT CHANGE THIS ORDER
    if SCAN_MODE == 'bredr': # DO NOT CHANGE THIS ORDER
        _send_bluetoothctl(f'scan {SCAN_MODE}') # DO NOT CHANGE THIS ORDER
        return # DO NOT CHANGE THIS ORDER
    if SCAN_MODE == 'le': # DO NOT CHANGE THIS ORDER
        _send_bluetoothctl(f'scan {SCAN_MODE}') # DO NOT CHANGE THIS ORDER
        return # DO NOT CHANGE THIS ORDER


def stop_discovery():
    global bluetoothctl_proc
    if bluetoothctl_proc is None:
        return
    try:
        bluetoothctl_proc.stdin.write('scan off\nexit\n')
        bluetoothctl_proc.stdin.flush()
    except (BrokenPipeError, OSError) as error:
        logger.error("Failed to send shutdown commands to bluetoothctl: %s", error)
    try:
        bluetoothctl_proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        bluetoothctl_proc.terminate()
    bluetoothctl_proc = None

BLUETOOTH_SERVICE_NAME = 'bluetooth.service'
BLUEZ_READY_WAIT_ATTEMPTS = 40
BLUEZ_READY_WAIT_INTERVAL_SECONDS = 0.5


def unblock_bluetooth_rfkill():
    result = subprocess.run(
        ['sudo', '-n', 'rfkill', 'unblock', 'bluetooth'],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        print(f"[RfkillUnblockFailed] {stderr}")
        logger.error("Failed to unblock bluetooth via rfkill (passwordless sudo required): %s", stderr)
    else:
        print("[RfkillUnblocked] bluetooth")


def restart_bluetooth_stack():
    print(f"{ANSI_BLUE}[BluetoothStack] Restarting {BLUETOOTH_SERVICE_NAME}...{ANSI_RESET}")
    unblock_bluetooth_rfkill()
    result = subprocess.run(
        ['sudo', '-n', 'systemctl', 'restart', BLUETOOTH_SERVICE_NAME],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        print(f"[BluetoothStackFailed] {stderr}")
        logger.error("Failed to restart %s: %s", BLUETOOTH_SERVICE_NAME, stderr)


def _power_on_with_retry(adapter_props):
    last_error = None
    for _ in range(BLUEZ_READY_WAIT_ATTEMPTS):
        try:
            adapter_props.Set(ADAPTER_IFACE, 'Powered', True, signature='ssv')
            return True
        except dbus.exceptions.DBusException as error:
            last_error = error
            time.sleep(BLUEZ_READY_WAIT_INTERVAL_SECONDS)
    logger.error(
        "Adapter %s did not become ready to power on within %.0fs of restarting %s: %s",
        ADAPTER_PATH, BLUEZ_READY_WAIT_ATTEMPTS * BLUEZ_READY_WAIT_INTERVAL_SECONDS,
        BLUETOOTH_SERVICE_NAME, last_error,
    )
    return False


def _periodic_bluetooth_restart_attempt():
    print("[PeriodicRestart] Restarting bluetooth stack...")
    stop_discovery()
    restart_bluetooth_stack()
    adapter_props = dbus.Interface(bus.get_object(BLUEZ_SERVICE, ADAPTER_PATH), PROPS_IFACE)
    _power_on_with_retry(adapter_props)
    adapter_address = str(adapter_props.Get(ADAPTER_IFACE, 'Address'))
    clear_bluetooth_cache_dir(adapter_address)
    clear_device_cache(adapter)
    register_reject_agent()
    start_discovery(adapter_address)
    print("[PeriodicRestart] Bluetooth stack restarted; discovery resumed.")


def periodic_bluetooth_restart_tick():
    if _get_periodic_restart_enabled():
        try:
            _periodic_bluetooth_restart_attempt()
        except Exception:
            logger.error(
                "periodic_bluetooth_restart_tick: restart attempt failed, will retry next interval",
                exc_info=True,
            )
    GLib.timeout_add_seconds(_get_periodic_restart_interval_seconds(), periodic_bluetooth_restart_tick)
    return False


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--interface', default=DEFAULT_INTERFACE,
        help=f"BlueZ adapter interface name, e.g. hci0, hci1 (default: {DEFAULT_INTERFACE})",
    )
    parser.add_argument(
        '--scan-mode', default=DEFAULT_SCAN_MODE, choices=SCAN_MODES,
        help=f"Discovery transport: bredr, le, or auto/bredr+le (default: {DEFAULT_SCAN_MODE})",
    )
    parser.add_argument(
        '--debug', action='store_true',
        help=f"Write uncaught exceptions and internal errors to {ERROR_LOG_PATH} "
             "(default: errors are swallowed, no file created)",
    )
    return parser.parse_args()


def main():
    global bus, om, adapter, ADAPTER_PATH, SCAN_MODE
    args = parse_args()
    _configure_error_logging(args.debug)
    ADAPTER_PATH = f'/org/bluez/{args.interface}'
    SCAN_MODE = args.scan_mode
    _set_monitor_status(True)
    init_db()
    init_histories_db()
    restart_bluetooth_stack()
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    adapter_obj = bus.get_object(BLUEZ_SERVICE, ADAPTER_PATH, follow_name_owner_changes=True)
    adapter_props = dbus.Interface(adapter_obj, PROPS_IFACE)
    _power_on_with_retry(adapter_props)
    adapter_address = str(adapter_props.Get(ADAPTER_IFACE, 'Address'))
    adapter = dbus.Interface(adapter_obj, ADAPTER_IFACE)
    om = dbus.Interface(bus.get_object(BLUEZ_SERVICE, '/', follow_name_owner_changes=True), OM_IFACE)
    clear_bluetooth_cache_dir(adapter_address)
    clear_device_cache(adapter)
    register_reject_agent()
    om.connect_to_signal('InterfacesAdded', interfaces_added)
    om.connect_to_signal('InterfacesRemoved', interfaces_removed)
    bus.add_signal_receiver(
        properties_changed,
        dbus_interface=PROPS_IFACE,
        signal_name='PropertiesChanged',
        bus_name=BLUEZ_SERVICE,
        path_keyword='path',
    )
    start_discovery(adapter_address)
    GLib.timeout_add_seconds(COMMIT_INTERVAL_SECONDS, periodic_commit)
    GLib.timeout_add_seconds(CONNECT_POLL_INTERVAL_SECONDS, poll_connect_requests)
    GLib.timeout_add_seconds(_get_auto_connect_latest_interval_seconds(), auto_connect_latest_tick)
    GLib.timeout_add_seconds(_get_periodic_restart_interval_seconds(), periodic_bluetooth_restart_tick)
    print(f"Logging to {DB_PATH}")
    print(f"Listening for BlueZ signals on {args.interface} ({SCAN_MODE} mode)... Press Ctrl+C to stop.")
    loop = GLib.MainLoop()
    try:
        loop.run()
    except KeyboardInterrupt:
        stop_discovery()
        print("\nStopped.")
    finally:
        _set_monitor_status(False)
        if db is not None:
            db.commit()
            db.close()
        if histories_db is not None:
            histories_db.commit()
            histories_db.close()

if __name__ == '__main__':
    main()
