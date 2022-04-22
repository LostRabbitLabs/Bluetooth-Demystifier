import re
import sys
import json
import logging
from datetime import datetime, timezone
from gi.repository import Gio, GLib
from sqlalchemy.orm.session import sessionmaker

GATT_SERVICE = 'org.bluez.GattService1'
GATT_CHAR = 'org.bluez.GattCharacteristic1'
GATT_DESC = 'org.bluez.GattDescriptor1'

logger = logging.getLogger(__name__)

if __name__ == '__main__':
  logging.basicConfig(format='%(asctime)s %(message)s', level=logging.DEBUG)
  from models import Mac, OUID, Data, RSSI, Presence, UUID, UUID_Lookup, engine
  from util import macHasUUID, safeCommit, dbusPathToMac
else:
  from inc.models import Mac, OUID, Data, RSSI, Presence, UUID, UUID_Lookup, engine
  from inc.util import macHasUUID, safeCommit, dbusPathToMac

Session = sessionmaker(bind=engine)
session = Session()

bus = Gio.bus_get_sync(Gio.BusType.SYSTEM)
cancel = Gio.Cancellable.new()

"""
  Setup DBus Proxy Objects for manager and first bluetooth adapter

  https://lazka.github.io/pgi-docs/#Gio-2.0/classes/DBusProxy.html#Gio.DBusProxy.new_sync

  @param connection - DBus Connection
  @param flags - Gio.DBusProxyFlags
  @param info - Gio.DBusInterfaceInfo/None
  @param name - str/None
  @param object_path - str
  @param interface_name - str
  @param cancellable - Gio.Cancellable/None
"""
manager = Gio.DBusProxy.new_sync(
  bus,
  Gio.DBusProxyFlags.NONE,
  None,
  'org.bluez',
  '/',
  'org.freedesktop.DBus.ObjectManager',
  cancel,
)

adapter = Gio.DBusProxy.new_sync(
  bus,
  Gio.DBusProxyFlags.NONE,
  None,
  'org.bluez',
  '/org/bluez/hci0',
  'org.bluez.Adapter1',
  cancel,
)

def addHandler(*args):
  """
    addHandler

    InterfacesAdded signal

    Callable function for DBusSignalCallback
    https://lazka.github.io/pgi-docs/Gio-2.0/callbacks.html#Gio.DBusSignalCallback

    @param connection - DBus Connection
    @param sender_name - String
    @param interface_name - String
    @param signal_name - String (Also called 'member_name')
    @param parameters - Dict
    @param user_data - ?
  """
  data = args[5]
  path = data[0]
  interfaces = data[1]

  try:
    device = interfaces['org.bluez.Device1']

    addr = device['Address']
    name = device['Alias']
    addrPrefix = addr.replace(':', '')[0:6]
    ouid = None
    updated = False

    logger.info(json.dumps(device))

    for oui in session.query(OUID).filter(OUID.prefix.like(addrPrefix)):
      logger.info(f'OUID {oui.vendor}')
      ouid = oui.id

    mac = session.query(Mac).filter(Mac.addr == addr).first()

    if mac:
      logger.info(f'Seen {mac.addr} before. Updating metadata')
      mac.seen += 1
      mac.last_seen = datetime.now(timezone.utc)
      mac.ouid = ouid
      mac.name = name
    else:
      logger.info(f'Adding new mac {addr}')
      logger.info(f'{addr} {name} {ouid} {addrPrefix}')

      mac = Mac(addr, name, ouid)
      session.add(mac)
      safeCommit(session)

    presence = Presence(mac.id, 'seen')
    session.add(presence)
    safeCommit(session)

    if 'ServiceData' in device:
      data = device['ServiceData']

      for key in data:
        dataList = list(data[key])
        intString = ' '.join([str(int(v)) for v in dataList])

        logger.info(f'DATA ADD: {addr} {key} {intString}')
        data = Data(mac.id, key, intString)

        session.add(data)
        safeCommit(session)

    if 'ManufacturerData' in device:
      data = device['ManufacturerData']


      for key in data:
        dataList = list(data[key])
        intString = ' '.join([str(int(v)) for v in dataList])

        logger.info(f'DATA ADD: {addr} {key} {intString}')
        data = Data(mac.id, key, intString)

        session.add(data)
        safeCommit(session)


    if 'UUIDs' in device:
      uuids = device['UUIDs']

      for uuid in uuids:
        uuid = uuid.upper()

        if not macHasUUID(mac, uuid):
          uuid_chunk = uuid[4:8]
          uuid_lookup_id = None

          for lookup in session.query(UUID_Lookup).filter(UUID_Lookup.prefix.like(uuid_chunk)):
            uuid_lookup_id = lookup.id

            logger.info(f'UUID ADVERTISE ADD: {addr} {uuid} {uuid_lookup_id}')
            entry = UUID(mac.id, uuid_lookup_id, uuid, type='advertised')

            session.add(entry)
            safeCommit(session)
  except Exception as e:
    if GATT_SERVICE in interfaces:
      service = interfaces[GATT_SERVICE]
      uuid = service['UUID'].upper()
      addr = dbusPathToMac(service['Device'])
      mac = session.query(Mac).filter(Mac.addr == addr).first()

      if mac:
        if not macHasUUID(mac, uuid):
          uuid_chunk = uuid[4:8]
          uuid_lookup_id = None

          for lookup in session.query(UUID_Lookup).filter(UUID_Lookup.prefix.like(uuid_chunk)):
            uuid_lookup_id = lookup.id

            logger.info(f'UUID SERVICE ADD: {addr} {uuid} {uuid_lookup_id}')
            entry = UUID(mac.id, uuid_lookup_id, uuid)

            session.add(entry)
            safeCommit(session)
    if GATT_CHAR in interfaces:
      characteristic = interfaces[GATT_CHAR]
      uuid = characteristic['UUID'].upper()
      addr = dbusPathToMac(characteristic['Service'])
      flags = ','.join(characteristic['Flags'])
      value = characteristic['Value']
      mac = session.query(Mac).filter(Mac.addr == addr).first()

      if mac:
        if not macHasUUID(mac, uuid):
          uuid_chunk = uuid[4:8]
          uuid_lookup_id = None

          for lookup in session.query(UUID_Lookup).filter(UUID_Lookup.prefix.like(uuid_chunk)):
            uuid_lookup_id = lookup.id

            logger.info(f'UUID CHAR ADD: {addr} {uuid} {uuid_lookup_id} {flags} {value}')
            entry = UUID(mac.id, uuid_lookup_id, uuid, 'characteristic', flags, value)

            session.add(entry)
            safeCommit(session)
    if GATT_DESC in interfaces:
      descriptor = interfaces[GATT_DESC]
      uuid = descriptor['UUID'].upper()
      addr = dbusPathToMac(descriptor['Characteristic'])
      value = descriptor['Value']
      mac = session.query(Mac).filter(Mac.addr == addr).first()

      if mac:
        if not macHasUUID(mac, uuid):
          uuid_chunk = uuid[4:8]
          uuid_lookup_id = None

          for lookup in session.query(UUID_Lookup).filter(UUID_Lookup.prefix.like(uuid_chunk)):
            uuid_lookup_id = lookup.id

            logger.info(f'UUID DESC ADD: {addr} {uuid} {uuid_lookup_id} {value}')
            entry = UUID(mac.id, uuid_lookup_id, uuid, 'descriptor', '', value)

            session.add(entry)
            safeCommit(session)


def changeHandler(*args):
  """
    changeHandler

    PropertiesChanged signal

    Callable function for DBusSignalCallback
    https://lazka.github.io/pgi-docs/Gio-2.0/callbacks.html#Gio.DBusSignalCallback

    @param connection - DBus Connection
    @param sender_name - String
    @param interface_name - String
    @param signal_name - String (Also called 'member_name')
    @param parameters - Dict
    @param user_data - ?
  """
  path = args[2]
  data = args[5]
  interface = data[0]
  properties = data[1]

  if interface == 'org.bluez.Device1':
    addr = path[-17:].replace('_', ':')

    mac = session.query(Mac).filter(Mac.addr == addr).first()

    presenceType = None
    seen = datetime.now(timezone.utc)

    if mac:
      for chgType, val in properties.items():
        if 'Name' in chgType:
          presenceType = 'name'
          name = val

          logger.info(f'Name CHG: {addr} {name}')
          mac.name = name

          safeCommit(session)

        if 'RSSI' in chgType:
          presenceType = 'rssi'
          rssi = val

          logger.info(f'RSSI CHG: {addr} {rssi}')
          entry = RSSI(mac.id, rssi)

          session.add(entry)
          safeCommit(session)

        if 'ManufacturerData' in chgType or 'ServiceData' in chgType:
          presenceType = 'data'
          data = val

          for key in val:
            data = list(val[key])
            intString = ' '.join([str(int(v)) for v in data])

            logger.info(f'DATA CHG: {addr} {chgType} {key} {intString}')
            data = Data(mac.id, key, intString)

            session.add(data)
            safeCommit(session)

        if 'UUID' in chgType:
          presenceType = 'uuid'
          uuids = val

          for uuid in uuids:
            uuid = uuid.upper()

            if not macHasUUID(mac, uuid):
              uuid_chunk = uuid[4:8]
              uuid_lookup_id = None

              for lookup in session.query(UUID_Lookup).filter(UUID_Lookup.prefix.like(uuid_chunk)):
                uuid_lookup_id = lookup.id

              logger.info(f'UUID CHG: {addr} {uuid} {uuid_lookup_id}')
              entry = UUID(mac.id, uuid_lookup_id, uuid)

              session.add(entry)
              safeCommit(session)

        mac.seen += 1
        mac.last_seen = seen

        presence = Presence(mac.id, presenceType)
        session.add(presence)
        safeCommit(session)

"""
  Setup Signals

  https://lazka.github.io/pgi-docs/#Gio-2.0/classes/DBusConnection.html#Gio.DBusConnection.signal_subscribe

  @param sender - String
  @param interface_name - String
  @param member - String (signal name)
  @param object_path - String
  @param arg0 - str/None
  @param flags - Gio.DBusSignalFlags
  @param callback - Callable
  @param user_data - ?
"""
bus.signal_subscribe(
  None,
  'org.freedesktop.DBus.Properties',
  'PropertiesChanged',
  None,
  None,
  Gio.DBusSignalFlags.NONE,
  changeHandler,
  None
)
bus.signal_subscribe(
  None,
  'org.freedesktop.DBus.ObjectManager',
  'InterfacesAdded',
  None,
  None,
  Gio.DBusSignalFlags.NONE,
  addHandler,
  None
)

loop = GLib.MainLoop()

if __name__ == '__main__':
  loop.run()
