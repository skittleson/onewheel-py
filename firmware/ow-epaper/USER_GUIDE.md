# ow-epaper user guide

A wrist-free display for a Pint or Pint X. The ESP32 connects to the board
over Bluetooth on its own and shows speed, battery, pitch, and temperatures on
a 200x200 e-paper screen. It needs no phone and no app.

This guide covers daily use. For building and flashing, see [README.md](README.md).

## What you need

- The ow-epaper device, charged or on USB power
- A Pint or Pint X within about 5 m
- (Optional) a microSD card for ride logs. Any size, formatted FAT32.

Only one device can connect to the board at a time. Close the Onewheel app and
stop any `live_monitor.py` session before you ride.

## First use

1. Insert a microSD card if you want ride logs. Skip this to ride without logs.
2. Power on the device. On a battery, press and release PWR. On USB it is
   already on.
3. Power on the board, or wait. The Pint wakes for Bluetooth when it senses
   motion or power.
4. Watch the status line in the top-left. It moves through SCANNING,
   CONNECTING, UNLOCKING, then RIDING.
5. The big number in the center is your speed in MPH.

The screen shows SCANNING until the board appears. A new ESP32 checks for the
board once a second for the first minute. After that it slows down to save
battery. Power on the board first and the link comes up in a second or two.

## Reading the screen

The RIDE page has four data cells plus a header.

| Area | Shows |
|------|-------|
| Top-left | Link and board status: RIDING, READY, CHARGING, FAULT xx, or NO DATA |
| Top-right | Board battery gauge |
| Center | Speed in MPH |
| Bottom-left of speed | ESP32's own battery, for example `ESP 95%` |
| Bottom-right of speed | Live spinner `\| / - \`, or `12s old` when data stops, plus `REC` while logging |
| Lower cells | Battery %, battery temp, pitch, controller/motor temp |

The spinner advances about once per second while data arrives. If it freezes
and shows `Ns old`, the board stopped sending. Check that the board is on and
in range.

### Changing pages

Press BOOT to cycle the pages. Each press does a full refresh, which clears
ghosting.

1. **RIDE** speed, battery, pitch, temps
2. **TRIP** ride time, top speed, distance, battery used, peak temps
3. **STATUS** link state, data rate, data age, current SD file, rows written,
   ESP32 voltage

### When something looks wrong

| Symptom | What it means | What to do |
|---------|---------------|------------|
| SCANNING for a long time | Board is off or out of range | Power on the board, move closer |
| `Ns old` and NO DATA | Board stopped sending | Check board power and range |
| FAULT xx | Board reported a fault code | Note the hex code, see the Onewheel app |
| ESP xx% low | ESP32 battery low | Charge over USB |
| Blank or ghosted cells | Value not read yet | Wait a few seconds, or press BOOT |

## Recording a ride

1. Insert a FAT32 microSD card before power-on.
2. Logging starts on its own when the link goes live. `REC` shows on screen.
3. Each connection writes one file, `/rides/ride_NNNN.csv`, at 5 rows per
   second.
4. The device flushes to the card every 2 seconds, so a power loss loses at
   most about 2 seconds of data.

To start a new file in the middle of a session, hold BOOT for one second. This
also resets the TRIP page.

To read the logs on a computer, eject the card and read it directly, or use
the serial commands below.

## Powering off

Hold PWR for two seconds. The device closes the ride file, leaves an OFF
summary on the screen, then cuts power. E-paper keeps the image with no power,
so the last reading stays visible.

On USB power the device cannot cut its own power, so it goes to deep sleep
instead. Press PWR to wake it.

## Charging

Plug in USB. The device runs while charging. Charge to full before a long ride.
The `ESP xx%` reading on screen is the ESP32's own battery, not the board's.

## Connecting your phone or laptop

The web app shows the same live data on your phone or laptop, with no install
and no account:

**https://skittleson.github.io/onewheel-py/**

1. Open the link in Chrome or Edge (Web Bluetooth). On Android, use Chrome.
   iOS browsers cannot do Bluetooth, so use an Android phone or a laptop.
2. Power on the ow-epaper device.
3. Press **Connect** and pick **ow-epaper** from the pop-up.
4. Press **Record** to save the live feed to a CSV file on your device.

You can also add the page to your home screen; it works like an app.

The app reads only the ESP32's own broadcast. It never connects to the
Onewheel, so it does not interfere with the device.

## Extracting your ride data

Option A, SD card (most data):

1. Power off the device and eject the microSD card.
2. Read the card on a computer. Files are `/rides/ride_NNNN.csv`.
3. Open them in Excel, Sheets, or pandas:

   ```python
   import pandas as pd
   df = pd.read_csv("rides/ride_0001.csv")
   print(df[["t_s", "mph", "batt_pct", "ctrl_c"]].describe())
   ```

Option B, serial cable (no card needed):

1. Connect USB and run `make -C firmware/ow-epaper monitor`.
2. Press `l` to list rides, then `g1` and Enter to print a ride.
3. Copy the lines between the BEGIN/END markers into a `.csv` file.

Option C, live feed from a laptop:

```bash
ow-epaper-monitor --csv feed.csv     # or: python src/epaper_monitor.py --csv feed.csv
```

## Reading logs over USB (optional)

Open the serial monitor and send commands:

```bash
make -C firmware/ow-epaper monitor
```

| Key | Action |
|-----|--------|
| `l` | List ride files |
| `g7` then Enter | Print ride 7 |
| `d` | Dump the diagnostics log |
| `c` | Clear the diagnostics log |

Opening the serial port restarts the device, which starts a new ride file.

## Watching live over Bluetooth (optional)

The device also broadcasts its own status over Bluetooth, so a laptop can
watch without a USB cable and without restarting the device:

```bash
ow-epaper-monitor          # or: python src/epaper_monitor.py
LIVE  age=0.0s  n/s=414  REC  mph=9.9  pitch=-2.2  batt=58%  temps=29/27C
```

This uses the laptop's Bluetooth. It does not connect to the Onewheel, so it
does not fight the device for the board.

## Tips

- Power on the board before the device for the fastest link.
- Keep the device within about 5 m of the board.
- A full refresh on every page change clears ghosting. Do not leave a static
  page on the screen for many minutes at a time.
- For a clean ride-per-file log, one power cycle per ride is simplest. Use
  BOOT-hold to split only when you stop and restart.
