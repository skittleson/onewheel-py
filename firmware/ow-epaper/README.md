# ow-epaper — Onewheel stats on an ESP32-S3 e-paper

Standalone firmware for the **Waveshare ESP32-S3-ePaper-1.54 (V2)**, an
ESP32-S3-PICO-1-N8R8 with a 200×200 black-and-white e-paper panel. The ESP32
connects to the Onewheel over BLE by itself, replays the same Pint unlock
sequence as `src/live_monitor.py --unlock pint`, and shows live stats.

```
┌──────────────────────────┐
│ RIDING           [████ ] │  status (SCANNING / CONNECTING / UNLOCKING /
├──────────────────────────┤          RIDING / FAULT xx / NO DATA) + battery gauge
│           12.4           │
│ ESP 95%    MPH     REC / │  speed (rpm × 0.0124), ESP32 battery, live spinner
├────────────┬─────────────┤
│  BATTERY   │  BATT TEMP  │
│    75%     │    25C      │
├────────────┼─────────────┤
│   PITCH    │ CTRL/MOTOR  │
│   -2.2     │   28/30C    │
└────────────┴─────────────┘
```

## Build and flash

Prerequisites: `arduino-cli` with `esp32:esp32` ≥ 3.3, and the repo `.env`
containing `OW_ADDRESS` and `OW_UNLOCK_MAGIC` (the same file the Python tools use).

```bash
make -C firmware/ow-epaper deps     # GxEPD2 + NimBLE-Arduino (once)
make -C firmware/ow-epaper flash    # generates secrets.h, runs host tests, compiles, uploads
make -C firmware/ow-epaper monitor  # serial log ([ble] … lines)
make -C firmware/ow-epaper test     # host-only tests of ow_protocol.h (g++)
```

`PORT=/dev/ttyACM1 make … flash` overrides the port (default `/dev/ttyACM0`).
`secrets.h` is generated from `.env` and is gitignored.

Only one BLE central can hold the board. Close the Onewheel app and stop
any `live_monitor.py` session before powering this on.

## Buttons

| Button | Action | What it does |
|--------|--------|--------------|
| **BOOT** (side) | click | Next page: **RIDE** → **TRIP** → **STATUS** → RIDE (full refresh) |
| **BOOT** | hold 1 s | **New ride**: reset trip stats and start a new SD ride file |
| **PWR** (side) | hold to power on | Hardware power-on when running from the LiPo |
| **PWR** | hold 2 s | **Power off**: close the ride file, leave an `OFF` summary on screen, cut power. On USB power it deep-sleeps instead; press PWR to wake. |

Pages:
- **RIDE**: speed, battery %, battery temp, pitch, controller/motor temp.
- **TRIP**: time, top speed, distance (from the trip odometer), battery used, peak temps.
- **STATUS**: link state, notification rate, data age, current SD file, rows written, ESP32 battery voltage.

## Is it working?

- **On the screen:** the bottom-right of the speed area shows a spinner
  (`| / - \`) that advances about once a second while data is arriving.
  If data stops, it switches to `12s old` and the status changes to `NO DATA`.
  `REC` appears while the SD card is recording. The bottom-left shows the
  ESP32's own battery (`ESP 95%`).
- **From the laptop, over BLE:** the ESP32 also advertises as `ow-epaper`
  and sends a status row twice a second:

  ```bash
  ow-epaper-monitor                 # or: python src/epaper_monitor.py [--csv feed.csv]
  LIVE       age=0.0s   n/s=414  REC  mph=9.9  pitch=-2.2  batt=58%  temps=29/27C  state=3  esp=4.17V
  ```

  `age` is the time since the last notification from the Onewheel, `n/s`
  is the notification rate, and `REC` means the SD log is open. This uses the
  laptop's Bluetooth, not USB, so it doesn't restart the board.

## Ride logs (SD card)

Insert a FAT32 microSD card before powering on. If the card can't be
mounted, the firmware formats it itself. Each connection to the Onewheel
creates `/rides/ride_NNNN.csv` at 5 rows/s (columns: `t_s, rpm, mph,
pitch_deg, roll_deg, batt_pct, batt_c, amps, volts, ctrl_c, motor_c, state,
error_flags, esp_mv`; `volts` stays empty because this board's firmware locks
it). BOOT-hold starts a new file mid-session. The file is flushed every 2 s.

Serial commands (`make monitor`): `l` lists rides, `g<N>` + Enter prints
ride N, `d` dumps the diagnostics log, `c` clears it. Opening the serial port
restarts the board, which starts a new ride file.

## How it works

- **`ow_protocol.h`**: decoders (same vectors as `tests/test_ow_uuids.py`),
  the screen text model, and the e-paper refresh policy. It has no Arduino
  dependencies, so `test/test_protocol.cpp` runs on the host.
- **BLE task (core 0)**: scan → connect → prime reads (`f302`, `f318`,
  `f311`) → write token to `f3ff` → `0x0001` to `f317` → subscribe to every
  notify characteristic → write `0x00` to `f30b` every 200 ms. Battery %,
  voltage, temperatures, state and footpads are also polled every 5 s,
  because the Pint S doesn't send them as notifications. On disconnect it
  rescans.
- **Display loop (core 1)**: redraws only when the formatted text changes,
  at most once per second, using partial refresh. Every 30th refresh is a
  full one to clear ghosting. Changing pages also does a full refresh.

## Board notes

| Signal | GPIO |
|--------|------|
| EPD BUSY / RST / DC / CS / SCK / MOSI | 8 / 9 / 10 / 11 / 12 / 13 |
| EPD power (LOW = on) | 6 |
| VBAT hold (HIGH = stay on when running from the LiPo) | 17 |
| BOOT button (pressed = LOW) | 0 |
| PWR button (pressed = LOW) | 18 |

Arduino settings (from Waveshare's V2 guide, baked into the Makefile's
FQBN): ESP32S3 Dev Module, USB CDC on boot, QIO 80 MHz, 4 MB flash
setting, Huge APP partition, OPI PSRAM.

The panel uses an SSD1681-class controller and is driven with GxEPD2's
`GxEPD2_154_D67`. If the image comes out mirrored or upside down, change
`display.setRotation()` in `setup()`.
