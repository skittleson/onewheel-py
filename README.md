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

If you ever change boards, redo the snoop capture in `docs/references.md`,
extract the new token with `src/parse_snoop.py`, and update
`OW_UNLOCK_MAGIC` in `.env`.

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
(`e659fXXX-ea98-11e3-ac10-0800200c9a66`). From `f310` onward the names follow
[OWCE](https://github.com/OnewheelCommunityEdition/OWCE_App) (`OWBoard.cs`), checked
against the official app's reads in `captures/snoop_decoded.txt`.

| UUID prefix | Name              | Access     | Decode                        |
|-------------|-------------------|------------|-------------------------------|
| `f301`      | SerialNumber      | read       | uint16                        |
| `f302`      | RidingMode        | read/write | 1=Sequoia/Redwood … 9=Custom  |
| `f303`      | BatteryRemaining  | read/notify| uint8 percent                 |
| `f304`      | BatteryLow5       | read/notify| bool                          |
| `f305`      | BatteryLow20      | read/notify| bool                          |
| `f306`      | BatterySerial     | read       | uint16                        |
| `f307`     ★| Pitch             | read/notify| uint16, degrees = (v − 1800)/10 (Pint S) |
| `f308`     ★| Roll              | read/notify| int16, same scaling           |
| `f309`     ★| Yaw               | read/notify| int16, same scaling           |
| `f30a`      | TripOdometer      | read/notify| uint16 wheel rotations        |
| `f30b`     ★| RpmSpeed          | read/notify| uint16 rpm; mph ≈ rpm × 0.0124 |
| `f30c`      | LightingMode      | read/write |                               |
| `f30d`      | LightsFront       | read/write |                               |
| `f30e`      | LightsBack        | read/write |                               |
| `f30f`     ★| StatusError       | read/notify| bitfield — board state + faults |
| `f310`     ★| Temperature       | read/notify| byte 0 = motor °C, byte 1 = controller °C |
| `f311`      | FirmwareRevision  | read       | uint16 (this board: 5200)     |
| `f312`     ★| CurrentAmps       | read/notify| int16 × 0.002 A (Pint, per OWCE); negative = regen |
| `f313`      | TripAmpHours      | read/notify| uint16                        |
| `f314`      | TripRegenAmpHours | read/notify| uint16                        |
| `f315`     ★| BatteryTemp       | read/notify| byte 0 = °C                   |
| `f316`      | BatteryVoltage    | read/notify| uint16 × 0.1 V — **reads 0 on this Pint S** (locked by firmware) |
| `f317`      | SafetyHeadroom    | read/write | the unlock sequence writes `0x0001` here |
| `f318`      | HardwareRevision  | read       | uint16 range → model (this board: 7323) |
| `f319`      | LifetimeOdometer  | read       | uint16                        |
| `f31a`      | LifetimeAmpHours  | read       | uint16                        |
| `f31b`      | BatteryCells      | read/notify| cell id in top nibble, mV × 1.1 in low 12 bits |
| `f31c`      | LastErrorCode     | read/notify| uint16                        |
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

**Pint / Pint S (this board)**: no per-session algorithm is needed. The
app writes a **static 20-byte token** to `f3ff` that is unique per board and
identical across sessions. Capture it once with an HCI snoop log (see below),
put it in `.env` as `OW_UNLOCK_MAGIC`, and run `live_monitor.py --unlock pint`.
The full sequence the app uses (and this project replays) is:

1. Read `f302` RidingMode, `f318` HardwareRevision, `f311` FirmwareRevision
2. Write the token to `f3ff`
3. Write `0x0001` to `f317` (SafetyHeadroom)
4. Write `0x00` to `f30b` every ~200 ms as a keepalive

**GT / Pint X**: untested here. Try the same snoop-and-replay approach first;
if the token changes per session, see `pOnewheel` (Android, Java) in
`docs/references.md`. `gemurf` (Go) is the canonical XR reference and
`owpy` / `onewheel-bleak` are XR-era Python wrappers.

The discovery + read scripts in this project work **without** unlock.

## Project layout

```
onewheel-py/
├── README.md                  ← you are here
├── pyproject.toml             ← package metadata, ow-* console scripts, pytest config
├── requirements.txt           ← exact tested versions
├── .env.example               ← copy to .env: OW_ADDRESS, OW_UNLOCK_MAGIC
├── src/
│   ├── ow_uuids.py            ← all GATT UUIDs + decoders
│   ├── ow_env.py              ← OW_ADDRESS / OW_UNLOCK_MAGIC loading (env or .env)
│   ├── discover.py            ← scan, find your board, dump GATT table
│   ├── read_static.py         ← one-shot read of every readable char
│   ├── live_monitor.py        ← subscribe + rolling dashboard; --unlock none|xr|pint, --csv
│   ├── unlock_pint.py         ← shortcut for `live_monitor.py --unlock pint`
│   ├── unlock_xr.py           ← XR challenge solver (+/XR)
│   ├── probe_challenge.py     ← experiment: try to provoke the f3fe challenge
│   └── parse_snoop.py         ← btsnoop_hci.log → OW reads/writes/notifies (find the token)
├── tests/                     ← pytest; fixtures/ holds raw bytes from a real Pint S ride
├── firmware/ow-epaper/        ← standalone ESP32-S3 e-paper stats display (see its README)
├── docs/
│   ├── protocol.md            ← deeper dive on byte formats + status bitfield
│   └── references.md          ← links to all upstream reverse-engineering work
└── captures/                  ← drop pcap/btsnoop logs here (gitignored)
```

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'          # or: pip install -r requirements.txt
cp .env.example .env             # fill in OW_ADDRESS (+ OW_UNLOCK_MAGIC for Pint)

# 1. Find your board and dump its GATT table
ow-discover                      # == python src/discover.py

# 2. Once-off read of every static value (works on every gen, no unlock)
ow-read                          # == python src/read_static.py [--address ...]

# 3. Live stream of pitch/RPM/battery/amps/temp
ow-monitor --unlock pint --csv ride.csv   # Pint / Pint S
ow-monitor --unlock xr                    # Onewheel+ XR
ow-monitor                                # V1 / +

# Tests
pytest
```

`--address` is optional everywhere; it falls back to `OW_ADDRESS`. On a
Pint without `--unlock pint`, expect most live values to read `0` and
notifications to stay quiet.

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
