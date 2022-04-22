from datetime import datetime, timezone
from sqlalchemy import create_engine, ForeignKey
from sqlalchemy import Column, Integer, String, Text, DateTime
from sqlalchemy.ext.declarative import as_declarative
from sqlalchemy.orm import relationship

engine = create_engine(
  'postgresql:///blt',
  echo=False,
  connect_args={
    'options': '-c timezone=utc'
  }
)

@as_declarative()
class Base(object):
  def as_dict(self):
    return {c.name: str(getattr(self, c.name)) for c in self.__table__.columns}

class Category(Base):
  __tablename__ = 'category'

  id = Column(Integer, primary_key=True)
  name = Column(String)
  description = Column(String)
  icon = Column(Text)
  color = Column(Text)

  def __init__(self, name, description, icon, color):
    self.name = name
    self.description = description
    self.icon = icon
    self.color = color

class OUID(Base):
  __tablename__ = 'ouid'

  id = Column(Integer, primary_key=True)
  cat_id = Column(Integer, ForeignKey('category.id'))
  prefix = Column(String)
  vendor = Column(String)

  category = relationship('Category', lazy='joined')

  def __init__(self, prefix, vendor, category_id):
    self.prefix = prefix
    self.vendor = vendor
    self.category_id = category_id

class Mac(Base):
  __tablename__ = 'mac'

  id = Column(Integer, primary_key=True)
  ouid = Column(Integer, ForeignKey('ouid.id'))
  addr = Column(String, unique=True, index=True)
  name = Column(String)
  tag = Column(String)

  first_seen = Column(DateTime, default=datetime.utcnow)
  last_seen = Column(DateTime, default=datetime.utcnow, index=True)
  seen = Column(Integer, default=1, index=True)

  oui = relationship('OUID', lazy='joined')
  uuids = relationship('UUID', lazy='joined')
  data = relationship('Data', lazy='dynamic')
  rssis = relationship('RSSI', lazy='dynamic')
  presence = relationship('Presence', order_by='desc(Presence.time)', lazy='dynamic')

  def __init__(self, addr, name, ouid):
    self.addr = addr
    self.name = name
    self.ouid = ouid

class Presence(Base):
  __tablename__ = 'presence'

  id = Column(Integer, primary_key=True)
  mac_id = Column(Integer, ForeignKey(Mac.id))
  type = Column(String)
  time = Column(DateTime, default=datetime.utcnow)

  def __init__(self, mac_id, type):
    self.mac_id = mac_id
    self.type = type

class UUID_Lookup(Base):
  __tablename__ = 'uuid_lookup'

  id = Column(Integer, primary_key=True)
  prefix = Column(String)
  attribute = Column(String)
  type = Column(String)

  def __init__(self, prefix, attribute, type):
    self.prefix = prefix
    self.attribute = attribute
    self.type = type

class UUID(Base):
  __tablename__ = 'uuid'

  id = Column(Integer, primary_key=True)
  mac_id = Column(Integer, ForeignKey(Mac.id))
  lookup_id = Column(Integer, ForeignKey(UUID_Lookup.id))
  time = Column(DateTime, default=datetime.utcnow)
  uuid = Column(String)
  type = Column(String, default='primary')
  flags = Column(String, default='')
  value = Column(String, default='')

  lookup = relationship('UUID_Lookup', lazy='joined')

  def __init__(self, mac_id, lookup_id, uuid, type = 'primary', flags = '', value = ''):
    self.mac_id = mac_id
    self.lookup_id = lookup_id
    self.uuid = uuid
    self.type = type
    self.flags = flags
    self.value = value

class RSSI(Base):
  __tablename__ = 'rssi'

  id = Column(Integer, primary_key=True)
  mac_id = Column(Integer, ForeignKey(Mac.id))
  time = Column(DateTime, default=datetime.utcnow)
  rssi = Column(Integer)

  def __init__(self, mac_id, rssi):
    self.mac_id = mac_id
    self.rssi = rssi

class Data(Base):
  __tablename__ = 'data'

  id = Column(Integer, primary_key=True)
  mac_id = Column(Integer, ForeignKey(Mac.id))
  time = Column(DateTime, default=datetime.utcnow)
  key = Column(String)
  value = Column(Text)

  def __init__(self, mac_id, key, value):
    self.mac_id = mac_id
    self.key = key
    self.value = value

# create tables
Base.metadata.create_all(engine)
