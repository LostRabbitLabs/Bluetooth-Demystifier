# Bluetooth Demystifier
Bluetooth Demystifier is a visual into the world of Bluetooth/BLE devices.

![Screenshot](/ui/templates/dist/img/btdm_demo.png?raw=true "Bluetooth Demystifier Screenshot")

[[_TOC_]]

---

## Features

* Rich and Interactive UI
* Realtime/Historical view of Bluetooth/BLE devices
* RSSI Graphs and Data Collection
* OUID/UUID association
* Custom Device Name Tagging
* Intuitive filtering
* BLE Connect/Disconnect
* GATT Services discovery
* CSV Export
* HTTP JSON API


---
## Kali2022/Parrot OS/Raspberian (Raspberry Pi) Setup
Follow these instructions to get this code running on modern Debian based distros.

> This should work on any newer Debian based system.

### System Dependencies
```
sudo apt install postgresql pkg-config libcairo2-dev libgirepository1.0-dev libpq-dev python3-dev python3-pip python3-sqlalchemy python3-flask python3-psycopg2 python3-gi
```

### PostgreSQL Setup
The `prep.sh` script will create the database, user, and load up the database with the OUI/category and UUID lookup information.

```
sudo ./scripts/prep.sh
```

### Running
This will start the Flask API/UI server and setup the DBus signals to ingest bluetooth data.

```
sudo ./scripts/init.sh
```

### Web Interface
The web application is currently located on port `1338`. Here is a link you can click :)

[http://localhost:1338/](http://localhost:1338/)

---
## HTTP JSON API
Currently there is no authentication on the API.

### Endpoints

`GET /api/adapter` Return information about the first adapter (hci0).

`GET /api/adapter/scan/on` Starts the bluetooth adapter's StartDiscovery() method.

`GET /api/adapter/scan/off` Stops the bluethooth adapters discovery.

`GET /api/adapter/transport` Returns the Discovery Transport

`POST /api/adapter/transport`
* transport (Required) - String Enum of [bredr, auto, le]

`GET /api/query` Returns all mac addresses.
* count (Optional) - Integer
  * Returns top seen devices
* time (Optional) - Timestamp format "%Y-%m-%dT%H:%M:%S.%fZ"
  * Returns macs seen since time
  * Ex Javascript: `&time=${(new Date().toJSON())}`
* include (Optional) - String
  * Include rssi and/or data for each mac. (Performance hit)
  * Examples
    * Ex: `&include=rssi,data` to include both rssi/data
    * Ex: `&include=rssi` to include just the rssi
* all (Optional) - Boolean
  * To include all presence plots or only in the `time` parameter

`GET /api/query/sample` Returns all macs matching the criteria with sampled presence points
* count (Optional) - Integer
  * Returns top seen devices
* time (Optional) - Timestamp format "%Y-%m-%dT%H:%M:%S.%fZ"
  * Returns macs seen since time
  * Ex Javascript: `&time=${(new Date().toJSON())}`
* maxTarget (Optional) - Integer default 20000
  * Max Number of presence points before % sampling kicks in
* all (Optional) - Boolean
  * To include all presence plots outside of time. Sampled

`DELETE /api/query` Deletes all the collected data for the query
* time (Optional) - Timestamp format "%Y-%m-%dT%H:%M:%S.%fZ"
  * Only deletes macs last seen since time

`GET /api/query/<MAC>` Returns all data for the given `<MAC>` address
* mac (Required) - String of Mac Address

`DELETE /api/query/<MAC>` Deletes all tdata for the given `<MAC>` address
* mac (Required) - String of Mac Address

`POST /api/<MAC>/tag` Sets a tag for the supplied `<MAC>` address
* mac (Required) - String of Mac Address
* tag (Required) - String to tag Mac

`DELETE /api/<MAC>/tag` Removes the tag for the supplied `<MAC>` address
* mac (Required) - String of Mac Address
