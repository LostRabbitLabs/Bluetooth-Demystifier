from gi.repository import Gio
from sqlalchemy.sql.expression import func
from .models import Mac, OUID, Data, RSSI, Presence

def safeCommit(session):
  """
    getDeviceForMac

    Gets a DBusProxy device interface
    for the given mac address

    @oaram address - String of mac addr
    @return device - DBusProxy
  """
  try:
    session.commit()
  except Exception as e:
    session.rollback()

def getDeviceForMac(address):
  """
    getDeviceForMac

    Gets a DBusProxy device interface
    for the given mac address

    @oaram address - String of mac addr
    @return device - DBusProxy
  """
  bus = Gio.bus_get_sync(Gio.BusType.SYSTEM)
  path = macToDBusPath(address)

  device = Gio.DBusProxy.new_sync(
    bus,
    Gio.DBusProxyFlags.NONE,
    None,
    'org.bluez',
    path,
    'org.bluez.Device1',
    None
  )

  return device

def macToDBusPath(mac):
  """
    macToDBusPath

    Transforms a mac to its DBus Proxy Object

    Ex: 55:32:23:21 -> /org/bluez/hci0/dev_55_32_23

    @param mac - String
    @return
  """
  return  f'/org/bluez/hci0/dev_{mac.replace(":", "_")}'

def dbusPathToMac(path):
  """
    dbusPathToPath

    Transforms a dbus path to a mac address

    Ex: /org/bluez/hci0/dev_22_22 -> 22:22
    @param path - String
    @return string
  """
  return path[20:37].replace('_', ':')

def shouldInclude(include, what):
  """
    shouldInclude

    Description: Checks the supplied `include` parameter
    for the supplied key

    @param include - String "rssi,data"
    @param what - String
  """
  return what in include

def macsAndPresence2json(macs, presences):
  """
    macs2json

    Description: Takes a response from SQLAlchemy and transforms it
    into a structure that can be passed to `jsonify`

    @param macs - Array of mac dicts
    @param include - String/Bool to include data/rssi
  """
  out = []
  odict = {}

  for mac in macs:
    item = mac.as_dict()
    item['data'] = []
    item['rssi'] = []
    item['uuid'] = []
    item['presence'] = []

    if mac.oui:
      oui = mac.oui.as_dict()

      if mac.oui.category:
        oui['category'] = mac.oui.category.as_dict()

      item['ouid'] = oui


    try:
      for uuid in mac.uuids:
        uuidObj = uuid.as_dict()
        uuidObj['lookup'] = {}

        try:
          if uuid.lookup:
            uuidObj['lookup'] = uuid.lookup.as_dict()
        except:
          pass

        item['uuid'].append(uuidObj)

    except:
      pass

    odict[item['id']] = item

  for presence in presences:
    try:
      odict[str(presence.mac_id)]['presence'].append(presence.as_dict())
    except:
      pass

  for key in odict:
    out.append(odict[key])

  return out

def macs2json(macs, include = False, timestamp = False, plotAll = False):
  """
    macs2json

    Description: Takes a response from SQLAlchemy and transforms it
    into a structure that can be passed to `jsonify`

    @param macs - Array of mac dicts
    @param include - String/Bool to include data/rssi
  """
  out = []

  for mac in macs:
    item = mac.as_dict()
    item['data'] = []
    item['rssi'] = []
    item['uuid'] = []
    item['presence'] = []

    if mac.oui:
      oui = mac.oui.as_dict()

      if mac.oui.category:
        oui['category'] = mac.oui.category.as_dict()

      item['ouid'] = oui

    try:
      if timestamp and not plotAll:
        query = Presence.time > timestamp

        for presence in mac.presence.filter(query).all():
          item['presence'].append(presence.as_dict())
      else:
        for presence in mac.presence.all():
          item['presence'].append(presence.as_dict())
    except Exception as e:
      print(e);
      pass

    if include and shouldInclude(include, 'data'):
      try:
        if timestamp and not plotAll:
          query = Data.time > timestamp

          for data in mac.data.filter(query).all():
            item['data'].append(data.as_dict())
        else:
          for data in mac.data.all():
            item['data'].append(data.as_dict())
      except:
        pass

    if include and shouldInclude(include, 'rssi'):
      try:
        if timestamp and not plotAll:
          query = RSSI.time > timestamp

          for rssi in mac.rssis.filter(query).all():
            item['rssi'].append(rssi.as_dict())
        else:
          for rssi in mac.rssis.all():
            item['rssi'].append(rssi.as_dict())
      except:
        pass

    try:
      for uuid in mac.uuids:
        uuidObj = uuid.as_dict()
        uuidObj['lookup'] = {}

        try:
          if uuid.lookup:
            uuidObj['lookup'] = uuid.lookup.as_dict()
        except:
          pass

        item['uuid'].append(uuidObj)

    except:
      pass

    out.append(item)

  return out

def macHasUUID(mac, uuid):
  """
    macHasUUID

    Description: Returns bool if the supplied Mac has the specified
    UUID.

    @param mac - Mac sqlAlchemy
    @param uuid - String
  """
  try:
    for uuidObj in mac.uuids:
      if uuidObj.uuid == uuid:
        return True
  except:
    return False

  return False

def getQueryCount(query):
  """
    getQueryCount

    from: https://gist.github.com/hest/8798884

    @param query - orm query
    @return count - int
  """
  count_q = query.statement.with_only_columns([func.count()]).order_by(None)
  count = query.session.execute(count_q).scalar()

  return count
