# onewheel-py

Talk directly to a Onewheel over BLE from Linux/Python — no Onewheel app, no
cloud, no Future Motion login. Pull live telemetry (RPM, pitch/roll, battery
voltage, motor amps, controller temp, etc.) straight off the board.

## Status: WORKING

**Connectivity, unlock, and live data all confirmed working from Linux** as of
2026-05-28.

- BLE scan + connect: ✅
- Full GATT enumeration: ✅
- Unlock from Linux (no phone, no app): ✅ — `src/unlock_pint.py`
- Live notification stream: ✅ — ~200 Hz on `f3fe`, plus per-characteristic
  notifies for Pitch / RPM / etc.
- Sample capture: `captures/first_successful_unlock.csv` (4,738 events / 23 s)

The unlock token (`REDACTED`, 20 bytes)
was extracted from a Bluetooth HCI snoop log of the official app and turned
out to be a **static signature for this board** — same bytes worked across
multiple sessions and between two different BLE centrals (phone + Linux laptop).

If you ever change boards, redo the snoop capture in `docs/references.md`
and update `UNLOCK_MAGIC` in `src/unlock_pint.py`.

## Your board

Captured live from this board:

| Field             | Value                                          |
|-------------------|------------------------------------------------|
| Model             | **Onewheel Pint S** (confirmed by owner)       |
| Advertised name   | *(see `.env` → `OW_ADDRESS`)*                  |
| MAC address       | *(see `.env` → `OW_ADDRESS`)*                  |
| Internal serial   | `24017` (`f301 SerialNumber`, leaks pre-unlock)|
| BatteryTemp pre-unlock | 20 °C (one of the few unmasked reads)     |
| Lockout state     | Active — most reads return `0x0000` until unlock |

Confirmed empirically: connecting from Linux without unlock returns
`0x0000` for `HardwareRevision`, `FirmwareRevision`, `BatteryRemaining`,
`BatteryVoltage`, `Temperature`, `Pitch`, `Roll`, `Yaw`, `RPM`,
`StatusError`, etc. Notifications never fire even with the board powered
and footpads engaged. The challenge byte is **not** sent unsolicited to
an un-bonded central — FM gates it on a paired/bonded relationship plus
a specific app-side trigger.

## What you can pull live (no app)

The Onewheel exposes a single primary GATT service with ~30 characteristics
covering basically every signal the official app shows. The protocol was
reverse-engineered years ago and has been stable across generations
(characteristic UUIDs identical from V1 → GT). The values are mostly big-endian
`uint16` (some signed) packed into 2-byte notifications.

### Service UUID
```
e659f300-ea98-11e3-ac10-0800200c9a66
```

### Characteristics (the useful ones)

All UUIDs share the same base; only the 4-digit prefix changes
(`e659fXXX-ea98-11e3-ac10-0800200c9a66`).

| UUID prefix | Name              | Access     | Decode                        |
|-------------|-------------------|------------|-------------------------------|
| `f301`      | SerialNumber      | read       | uint16                        |
| `f302`      | RidingMode        | read/write | 1=Sequoia/Redwood … 9=Custom  |
| `f303`      | BatteryRemaining  | read/notify| uint8 percent                 |
| `f304`      | BatteryLow5       | read/notify| bool                          |
| `f305`      | BatteryLow20      | read/notify| bool                          |
| `f306`      | BatterySerial     | read       | uint16                        |
| `f307`     ★| Pitch             | read/notify| int16, degrees = (v − 0x8000)/10 |
| `f308`     ★| Roll              | read/notify| int16, same scaling           |
| `f309`     ★| Yaw               | read/notify| int16, same scaling           |
| `f30a`      | TripOdometer      | read/notify| uint16 wheel rotations        |
| `f30b`     ★| RpmSpeed          | read/notify| uint16 rpm; mph ≈ rpm × 0.0124 |
| `f30c`      | LightingMode      | read/write |                               |
| `f30d`      | LightsFront       | read/write |                               |
| `f30e`      | LightsBack        | read/write |                               |
| `f30f`     ★| StatusError       | read/notify| bitfield — board state + faults |
| `f310`     ★| Temperature       | read/notify| controller °C (low byte) / motor °C (high byte) |
| `f311`      | BatteryTemp       | read       | °C                            |
| `f312`     ★| BatteryVoltage    | read/notify| uint16 centivolts (÷10 = V)   |
| `f313`      | SafetyHeadroom    | read/notify| % derate                      |
| `f314`      | HardwareRevision  | read       | uint16 → maps to model/gen    |
| `f315`      | FirmwareRevision  | read       | uint16                        |
| `f316`      | LifetimeOdometer  | read       | uint16 (count of "trip rollovers") |
| `f317`      | LifetimeAmpHours  | read       | uint16                        |
| `f318`      | BatteryCells      | read       | 16 cell voltages, 2-bytes each (read in chunks) |
| `f319`      | LastErrorCode     | read       | uint16 — last fault code      |
| `f31a`      | (reserved)        |            |                               |
| `f31b`      | StanceFootpads    | read/notify| 0=off, 1=front only, 2=both, 3=back |
| `f320`     ★| CurrentAmps       | read/notify| int16 motor amps (signed, ÷1000?) |
| `f3fe`     ★| UartSerialRead    | notify     | challenge from board (20B)    |
| `f3ff`     ★| UartSerialWrite   | write      | challenge response (20B)      |

★ = signals you almost certainly want for live ride monitoring.

### The "GemAuth" / unlock challenge — **important for newer boards**

Starting with the **Onewheel+ XR** (and continued on Pint/Pint X/GT), Future
Motion added a handshake: a few seconds after you connect, the board emits a
20-byte challenge on `f3fe`. The app has ~30 s to write a correct 20-byte
response on `f3ff`. If you don't respond:

- **XR**: live notifications keep flowing for a while, then can throttle.
- **Pint / Pint X / GT**: the board **stops sending most live data** until
  unlocked — you'll still see static reads (battery %, hardware rev) but
  not the streaming pitch/RPM/amps.

**XR algorithm** (open-sourced, works on +, XR):
```
response = b"\x43\x52\x58" + md5(b"\x43\x52\x58" + challenge[3:]).digest()[:16] + checksum
```
where `challenge[0:3]` is the magic `b"\x43\x52\x58"` ("CRX") echo and
`checksum` = XOR of the 19 prior bytes.

**Pint / GT algorithm**: changed. The community has working implementations
but Future Motion has tightened it. See the references at the bottom — the
two reliable open-source landing spots right now are:

- **`pOnewheel` (Android, Java)** — has a current Pint/GT unlock.
- **`gemurf`** — Go, the canonical reference for the XR challenge.
- **`owpy` / `onewheel-bleak`** — Python wrappers (XR-era).

If your board is a **GT**, plan on either:
1. Sniffing the official app's `f3ff` write once via Android HCI snoop log,
   then replaying the algorithm by transcribing the Java unlock code from
   pOnewheel into Python; or
2. Living with read-only access to the static characteristics (battery %,
   firmware/hardware rev, last error, lifetime stats) — which is still
   useful for a dashboard.

The discovery + read scripts in this project work **without** unlock.
The live-stream script will work fully on XR and earlier; on Pint/GT it
will get partial data until you wire in the matching unlock.

## Project layout

```
onewheel-py/
├── README.md                  ← you are here
├── requirements.txt
├── src/
│   ├── ow_uuids.py            ← all GATT UUIDs + decoders
│   ├── discover.py            ← scan, find your board, dump GATT table
│   ├── read_static.py         ← one-shot read of every readable char
│   ├── live_monitor.py        ← subscribe to notifications, print rolling state
│   └── unlock_xr.py           ← XR challenge solver (works on +/XR; reference for porting)
├── docs/
│   ├── protocol.md            ← deeper dive on byte formats + status bitfield
│   └── references.md          ← links to all upstream reverse-engineering work
└── captures/                  ← drop pcap/btsnoop logs here
```

## Quick start

```bash
cd ~/Projects/onewheel-py
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 1. Find your board and dump its GATT table
python src/discover.py

# 2. Once-off read of every static value (works on every gen, no unlock)
python src/read_static.py --address XX:XX:XX:XX:FD:B0

# 3. Live stream of pitch/RPM/battery/amps/temp
python src/live_monitor.py --address XX:XX:XX:XX:FD:B0
```

If `live_monitor.py` shows updates for ~5 seconds and then goes quiet on a
Pint/GT, that's the unlock kicking in — see `docs/references.md` to wire
in the matching response.

## Linux BLE prerequisites

- `bluez` ≥ 5.50 (Ubuntu 22.04+ is fine).
- The user running the script needs to be in the `bluetooth` group, or run
  with `sudo`. `bleak` uses BlueZ's D-Bus API.
- If you're on a desktop with the board paired in `bluetoothctl`, **un-pair
  it from the desktop before running** — only one BLE central can hold a
  GATT connection at a time. Same applies to your phone: kill the Onewheel
  app or put the phone in airplane mode while testing from Linux.

## Capturing the official app's traffic (for reverse-engineering unlock)

On your phone:
```
Settings → Developer Options → Enable Bluetooth HCI snoop log → toggle BT off/on
```
Ride / open the app / let it connect, then:
```
adb bugreport bugreport.zip
unzip -p bugreport.zip "FS/data/misc/bluetooth/logs/btsnoop_hci.log" > captures/snoop.log
wireshark captures/snoop.log
```
Filter on `btatt.handle == <handle of f3ff>` to see the exact bytes the app
writes in response to the challenge.
