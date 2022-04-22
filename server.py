import json
import logging
import threading
from datetime import datetime
from flask import Flask, request, render_template, abort, jsonify
from sqlalchemy import tablesample, delete
from sqlalchemy.sql.expression import func
from sqlalchemy.orm import aliased
from sqlalchemy.orm.session import sessionmaker
from inc.bt import adapter, manager, loop
from inc.util import macs2json, macToDBusPath, getDeviceForMac, macsAndPresence2json, getQueryCount, safeCommit
from inc.models import Mac, OUID, Data, RSSI, engine, Presence

Session = sessionmaker(bind=engine)
session = Session()

logging.basicConfig(format='%(asctime)s %(message)s', filename='log.txt', filemode='a', level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder='ui/templates', static_url_path='', static_folder='ui/templates')

@app.route('/')
def index():
  return render_template('index.html')

@app.route('/api/adapter')
def getAdapter():
  out = {}

  try:
    for key in adapter.get_cached_property_names():
      out[key] = adapter.get_cached_property(key).unpack()
  except Exception as e:
    logger.error(e)
    return (jsonify({
      'error': str(e)
    }), 500)

  return jsonify(out)

@app.route('/api/adapter/scan/on')
def scanOn():
  try:
    logger.info('Starting Discovery')
    adapter.StartDiscovery()
    return jsonify({
      'success': True
    })
  except Exception as e:
    logger.error(e)
    return (jsonify({
      'error': str(e)
    }), 500)

@app.route('/api/adapter/scan/off')
def scanOff():
  try:
    logger.info('Stopping Discovery')
    adapter.StopDiscovery()
    return jsonify({
      'success': True
    })
  except Exception as e:
    logger.error(e)
    return (jsonify({
      'error': str(e)
    }), 500)

@app.route('/api/query/<mac>', methods=['GET', 'DELETE'])
def queryMac(mac = False):
  macFilterQuery = Mac.addr == mac.upper() if mac else None

  if macFilterQuery is not None:
    mac = session.query(Mac).filter(macFilterQuery).first()

    if mac is None:
      return (jsonify({
        'error': 'No such Mac'
      }), 400)
  else:
    return (jsonify({
      'error': 'Invalid Mac'
    }), 400)

  if request.method == 'GET':
    objects = manager.GetManagedObjects();
    out = macs2json([mac], 'rssi,data')

    if macToDBusPath(mac.addr) in objects:
      try:
        device = getDeviceForMac(mac.addr)
        out[0]['deviceData'] = {}

        for key in device.get_cached_property_names():
          out[0]['deviceData'][key] = device.get_cached_property(key).unpack()
      except Exception as e:
        logger.error(e)

    return jsonify(out)
  elif request.method == 'DELETE':
    try:
      session.delete(mac);
      session.commit()

      try:
        adapter.RemoveDevice('(o)', macToDBusPath(mac.addr))
      except Exception as e:
        logger.error(e)
        pass

      return jsonify({
        'success': True
      })
    except Exception as e:
      session.rollback()
      logger.error(e)
      return (jsonify({
        'error': str(e)
      }), 500)
  else:
    return (jsonify({
      'error': 'Invalid HTTP Method'
    }), 400)

@app.route('/api/<mac>/tag', methods=['POST', 'DELETE'])
def tag(mac = False):
  if request.method == 'POST':
    try:
      tag = request.json['tag']

      try:
        mac = session.query(Mac).filter(Mac.addr == mac).first()
        mac.tag = tag
        session.commit()

        return jsonify({
          'success': True,
          'mac': macs2json([mac]),
          'tag': tag
        })
      except Exception as e:
        logger.critical(e)
        return (jsonify({
          'error': 'Something happened. Check the logs'
        }), 500)
    except Exception as e:
      return (jsonify({
        'error': 'Invalid tag'
      }), 400)
  elif request.method == 'DELETE':
    mac = session.query(Mac).filter(Mac.addr == mac).first()
    mac.tag = None
    session.commit()

    return jsonify({
      'success': True,
    })
  else:
    return (jsonify({
      'error': 'Invalid HTTP Method'
    }), 400)

@app.route('/api/<mac>/connect', methods=['GET'])
def connectMac(mac = False):
  try:
    objects = manager.GetManagedObjects();

    if macToDBusPath(mac) in objects:
      device = getDeviceForMac(mac)
      device.Connect()

      return (jsonify({
        'success': True
      }))
    else:
      return (jsonify({
        'error': 'MAC is not in range'
      }), 400)
  except Exception as e:
    logger.error(e)
    return (jsonify({
      'error': str(e)
    }), 500)

@app.route('/api/<mac>/disconnect', methods=['GET'])
def disconnectMac(mac = False):
  try:
    objects = manager.GetManagedObjects();

    if macToDBusPath(mac) in objects:
      device = getDeviceForMac(mac)
      device.Disconnect()

      return (jsonify({
        'success': True
      }))
    else:
      return (jsonify({
        'error': 'MAC is not in range'
      }), 400)
  except Exception as e:
    logger.error(e)
    return (jsonify({
      'error': str(e)
    }), 500)

@app.route('/api/query', methods=['GET', 'DELETE'])
def query():
  timestamp = request.args.get('time', '')
  count = request.args.get('count', False, int)
  include = request.args.get('include', False, str)
  plotAll = request.args.get('all', False, bool)

  try:
    dt = datetime.strptime(timestamp, '%Y-%m-%dT%H:%M:%S.%fZ') if timestamp else False
  except:
    return (jsonify({
      'error': 'Invalid time. Use Date.toJSON() or "%Y-%m-%dT%H:%M:%S.%fZ"'
    }), 400)

  timeFilterQuery = Mac.last_seen > dt if timestamp else None

  if timeFilterQuery is not None:
    macs = session.query(Mac).filter(timeFilterQuery)
  else:
    macs = session.query(Mac)

  if count and isinstance(count, int):
    if request.method == 'DELETE':
      return (jsonify({
        'error': 'Cannot use count with DELETE'
      }))

    macs = macs.order_by(Mac.seen.desc()).limit(count)

  if request.method == 'GET':
    return jsonify(macs2json(macs.all(), include, dt, plotAll))
  elif request.method == 'DELETE':
    try:
      session.execute("DELETE from rssi");
      logger.info("rssi delete done")
      session.execute("DELETE from uuid")
      logger.info("uuid delete done")
      session.execute("DELETE from presence")
      logger.info("presence delete done")
      session.execute("DELETE from data")
      logger.info("data delete done")
      session.execute("DELETE from mac")
      logger.info("mac delete done")
      safeCommit(session)

      try:
        objects = manager.GetManagedObjects();

        for path in objects:
          if 'dev' in path:
            adapter.RemoveDevice('(o)', path)
      except Exception as e:
        logger.error(e)
        pass

      return jsonify({
        'success': True
      })
    except Exception as e:
      session.rollback()
      logger.error(e)
      return (jsonify({
        'error': str(e)
      }), 500)
  else:
    return (jsonify({
      'error': 'Invalid HTTP Method'
    }), 400)

@app.route('/api/query/sample', methods=['GET'])
def querySample():
  timestamp = request.args.get('time', '')
  maxTarget = request.args.get('max', 20000, int)
  count = request.args.get('count', False, int)
  include = request.args.get('include', False, str)
  plotAll = request.args.get('all', False, bool)
  sampleSize = 35

  try:
    dt = datetime.strptime(timestamp, '%Y-%m-%dT%H:%M:%S.%fZ') if timestamp else False
  except:
    return (jsonify({
      'error': 'Invalid time. Use Dat.toJSON() or "%Y-%m-%dT%H:%M:%S.%fZ"'
    }), 400)

  macTimeFilterQuery = Mac.last_seen > dt if timestamp else None
  presenceTimeFilterQuery = Presence.time > dt if timestamp else None

  if macTimeFilterQuery is not None:
    macs = session.query(Mac).filter(macTimeFilterQuery)
  else:
    macs = session.query(Mac)

  if count and isinstance(count, int):
    macs = macs.order_by(Mac.seen.desc()).limit(count)

  allMacs = macs.all()
  allIds = [m.id for m in allMacs]

  if presenceTimeFilterQuery is not None and not plotAll:
    countQuery = session.query(func.count(Presence.id)).filter(Presence.mac_id.in_(allIds), presenceTimeFilterQuery)
    count = getQueryCount(countQuery)

    if count > maxTarget:
      percent = (maxTarget / count) * 100
      sampleSize = percent
    else:
      sampleSize = 100

    presenceSampled = aliased(Presence, tablesample(Presence, sampleSize))
    presenceSampleTimeFilterQuery = presenceSampled.time > dt if timestamp else None

    presences = session.query(presenceSampled).filter(presenceSampled.mac_id.in_(allIds), presenceSampleTimeFilterQuery).order_by(func.random()).all()
  else:
    countQuery = session.query(func.count(Presence.id)).filter(Presence.mac_id.in_(allIds))
    count = getQueryCount(countQuery)

    if count > maxTarget:
      percent = (maxTarget / count) * 100
      sampleSize = percent
    else:
      sampleSize = 100

    presenceSampled = aliased(Presence, tablesample(Presence, sampleSize))
    presences = session.query(presenceSampled).order_by(func.random()).filter(presenceSampled.mac_id.in_(allIds)).all()

  logger.info(f'Presence Sample Size: {sampleSize}  Macs: {len(allMacs)}   Sampled Presences: {len(presences)}')

  if request.method == 'GET':
    return jsonify(macsAndPresence2json(allMacs, presences))
  else:
    return (jsonify({
      'error': 'Invalid HTTP Method'
    }), 400)

if __name__ == '__main__':
  logger.info('Starting flask')
  threading.Thread(target=lambda: app.run(host='0.0.0.0', port=1338, debug=False, use_reloader=False)).start()

  try:
    logger.info('Starting Discovery')
    adapter.StartDiscovery()
  except Exception as e:
    logger.error(e)

  logger.info('Starting GLib.MainLoop()')
  loop.run()
