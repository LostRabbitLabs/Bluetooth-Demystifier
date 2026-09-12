<table style="border: none">
    <tr style="text-align: left; border: none">
        <th style="height: auto; width: 125px; padding-top: 55px; border: none">
            <a href="https://www.lostrabbitlabs.com"><img src="https://lostrabbitlabs.com/assets/rabbit-center-logo-transparent.png" width="400"></a>
        </th>
        <th style="border: none">
            <h1> --- Bluetooth Demystifier --- </h1>
            <p><strong>Bluetooth Demystifier</strong> is a simple bluetooth enumerator, logger, identifier, sniffer, collector & plotter, that helps monitor Bluetooth Classic/BLE devices around you. It plots their signals over time providing real-time proximity data, and collects stats on Top Mac Addresses, Aliases, Vendors, UUIDs, and more. This can assist with locating lost devices, detecting if you are being followed, and give some insight into the Bluetooth darknet that exists all around us.</p>
            <p>Have any questions 🐇? Reach out to us at [lostrabbitlabs.com](https://lostrabbitlabs.com)</p>
        </th>
    </tr>
</table>

<br>

---

**Original concept by:** Jimi Allee - <a href="https://8ed4398c7077c9509d9ea3f40d6f8cf4.blogspot.com/2018/10/derbycon-viii-bluetooth-demystifier.html">Original Bluetooth Demystifier writeup</a>

## Contributors

<strong>Jimi Allee</strong> & <strong>Will Lenzini</strong>

---

## Features

- **Realtime & historical views** — watch devices live or scrub back through past captures on a Gantt-style chart, with click-and-drag time range selection directly on the plot.
- **RSSI graphs & OUID/UUID association** — plots signal strength over time and resolves MAC OUIs and GATT/service UUIDs to known vendors and services.
- **Auto classification & tagging** — categorizes devices by vendor and alias, with custom names/tags you can assign and track over time.
- **Connect, auto-connect & auto-restart** — connect/disconnect from the UI, auto-connect to the latest device, and periodically restart the adapter for long-running captures.
- **BR/EDR, LE, or Auto scan modes** — toggle scan mode and interface on the fly.
- **Resizable stats drawer** — drill into Top MAC Vendors, Manufacturer IDs, Observed Aliases, and Top UUIDs for whatever's currently shown (same time window, filter, and Top-N cap as the chart), with a drag handle to resize the drawer.
- **Per-device CSV export** — export a device's full service/UUID history from its detail modal.
- **JSON API** — HTTP endpoints for scripting and integrations.
- **Customizable dashboard** — configurable columns and theme colors.

---

## Installation

**Requirements:** Python 3.10+ on Linux (uses BlueZ over D-Bus).

```sh
git clone https://gitlab.com/lostrabbitlabs/bluetooth-demystifier.git
cd bluetooth-demystifier
pip install -r requirements.txt
```

---

## Example Usage

```sh
sudo python3 app.py
```

Then open `http://localhost:5000`. Running as root (or with equivalent passwordless sudo privileges) is required since scanning talks to BlueZ over the system D-Bus.

---

## Filter Bar

The filter bar at the top of the dashboard narrows the chart down to matching devices. Type plain text to search broadly across a device's name, vendor, UUIDs, service data, tags, and more all at once, or use a field-specific search (below) to target just one piece of data.

### Combining searches (AND / OR)

- `+` combines two searches with **AND** a device must match both sides.
- `;` combines two searches with **OR** a device must match at least one side.
- `+` and `;` can be mixed in one query: `a;b+c` means "(match a OR match b) AND (match c)".
- To search for a literal `+` or `;` character, escape it with a backslash: `\+` or `\;`.

### Field-specific searches

Target a single field with `field_name:"search text"` the field name, a colon, then the search text in double quotes (quotes are always required). Field-specific searches can be combined with `+`/`;` just like plain searches, and freely mixed with plain (general) searches in the same query.

- A trailing `*` inside the quotes matches anything that *starts with* that text, e.g. `mac_addr:"AA:BB:CC:*"`.
- To search for a literal `"` inside the quotes, escape it with a backslash: `\"`.

**Examples:**

```
bt_type:"le"
```
Only LE devices.

```
bt_type:"le"+alias:"rabbit"
```
Only LE devices whose alias contains "rabbit".

```
bt_type:"le";alias:"rabbit"
```
LE devices, OR any device (of any type) whose alias contains "rabbit".

```
bt_type:"le"+rabbit
```
Only LE devices that also match "rabbit" somewhere in a general search field.

```
bt_type:"le";rabbit
```
LE devices, OR any device that matches "rabbit" somewhere in a general search field.

### Field definitions

- **mac_addr**: The device's MAC address. Supports a trailing `*` to match a prefix.
- **alias**: The device's name, including any name it has advertised in the past, not just its current one.
- **mac_type**: Whether the device's address is `public` or `random`.
- **manufacturer_name**: The vendor name resolved from the device's manufacturer information.
- **manufacturer_id**: The device's manufacturer ID number, matched by its hex form (e.g. `0x004C`) or decimal form (e.g. `76`).
- **oui_id**: The raw vendor ID bytes taken from the first half of the MAC address.
- **oui_name**: The vendor name resolved from the MAC address itself.
- **category_names**: The category or vendor name(s) the device has been classified under.
- **category_description**: The description text for the device's classified category.
- **color**: The highlight color assigned to the device on the chart (`red`, `yellow`, `green`, or `pink`).
- **uuid**: Any advertised or solicited service ID, or its resolved name the device has broadcast.
- **data**: The contents of any service data the device has broadcast, whether matched as raw bytes or as decoded text.
- **tags**: Any tag you've manually assigned to the device.
- **bt_type**: Whether the device connects over classic Bluetooth (`bredr`) or Bluetooth Low Energy (`le`).

---

## API

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/dots` | Presence sessions for the Gantt chart |
| GET | `/api/stats` | Top vendors/manufacturers/aliases/UUIDs for the stats drawer, scoped to the currently shown devices |
| GET | `/api/points` | Raw per-observation points |
| GET | `/api/device/<address>` | Device detail |
| GET | `/api/device/<address>/export.csv` | Export a device's service/UUID history as CSV |
| GET | `/api/device/<address>/connect-status` | Device connection status |
| GET | `/api/device-connections` | All device connection states |
| POST | `/api/device-connections/clear` | Clear connection history |
| GET | `/api/auto-connect-successes/count` | Auto-connect success count |
| POST | `/api/device/<address>/connect` | Connect to device |
| POST | `/api/device/<address>/disconnect` | Disconnect from device |
| POST | `/api/device/<address>/tags` | Add a tag to device |
| DELETE | `/api/device/<address>/tags/<tag>` | Remove a tag from device |
| GET | `/api/tags` | List tags |
| POST | `/api/tags` | Create a tag |
| PATCH | `/api/tags/<name>` | Rename a tag |
| DELETE | `/api/tags/<name>` | Delete a tag |
| GET | `/api/monitor/status` | Scanner running/stopped status |
| POST | `/api/monitor/start` | Start the scanner |
| POST | `/api/monitor/stop` | Stop the scanner |
| POST | `/api/monitor/restart` | Restart the scanner |
| POST | `/api/monitor/clear-db` | Clear the capture database |
| GET/POST | `/api/interface` | Get/set the Bluetooth adapter interface |
| GET/POST | `/api/scan-mode` | Get/set scan mode (bredr/le/auto) |
| GET/POST | `/api/auto-start` | Get/set auto-start on launch |
| GET/POST | `/api/connect-timeout` | Get/set connect timeout |
| GET/POST | `/api/auto-connect-latest` | Get/set auto-connect-to-latest |
| GET/POST | `/api/auto-connect-latest-interval` | Get/set auto-connect interval |
| GET/POST | `/api/periodic-restart` | Get/set periodic adapter restart |
| GET/POST | `/api/periodic-restart-interval` | Get/set periodic restart interval |
| GET/POST | `/api/columns` | Get/set visible dashboard columns |
| GET/POST | `/api/theme-colors` | Get/set dashboard theme colors |

---

## License

MIT License