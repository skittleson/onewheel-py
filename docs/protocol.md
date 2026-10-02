# Onewheel BLE protocol — deeper dive

## Connection lifecycle

```
[scan] ──► find ow###### advertising service e659f300…
   │
[connect] ──► GATT MTU negotiated (Pint/GT typically up to 247)
   │
[service discovery] ──► one primary service: e659f300…
   │                    + standard Generic Access / Generic Attribute
   │
[subscribe] ──► CCCD writes on every characteristic you want notifications for
   │
[challenge]  board → app  on f3fe (20 bytes)        ◄── Pint+/XR/GT only
[response]   app → board on f3ff (20 bytes, write-with-response)
   │
[stream] ──► notifications flow ~10–50 Hz on the live channels
```

## Packet shapes

Almost everything is a 2-byte big-endian payload. Some characteristics
return a single byte (battery percent on some firmware revs, low-battery
flags). A few (battery cells, hardware/firmware versions) pack multi-byte
data.

### `f307` Pitch / `f308` Roll / `f309` Yaw

Two encodings have been observed across firmware:

1. **Offset uint16**: raw value with `0x8000` as zero. Degrees = `(raw - 0x8000) / 10`.
   Range ≈ ±90° pitch/roll, full 360° yaw.
2. **Signed int16**: degrees = `int16(raw) / 10`.

`ow_uuids.decode_pitch_roll_yaw()` heuristically handles both.

### `f30b` RPM

Big-endian uint16. Wheel rpm. Convert to mph with circumference:
- Pint / GT (10.5" tire): mph ≈ rpm × 0.0124
- XR (11.5" tire): mph ≈ rpm × 0.01359

### `f312` BatteryVoltage

uint16 centivolts (volts × 10). XR pack rests around 580–630 (58–63 V).
GT around 740–840 (74–84 V).

### `f320` CurrentAmps

int16, scaling depends on gen. Observed:
- Positive = motor pulling current (acceleration / hill climb)
- Negative = regen (braking / coasting downhill)
- Divide by 1000 on XR, by 100 on some Pint firmware. Calibrate against the
  app once if you need precision.

### `f30f` StatusError

uint16, two sub-fields:
- low byte = state machine
  - 0x00 init, 0x01 boardOff, 0x02 boardOn, 0x03 riding, 0x04 charging,
    0x05 chargedFully, 0x06 shuttingDown
- high byte = fault flags (bitmask) — most-significant bits set on
  pushback / overcurrent / cell imbalance / etc. The exact bit map varies by
  gen and isn't fully published; treat anything non-zero as "something to
  log + look up against `LastErrorCode` (`f319`)".

### `f318` BatteryCells

Read in chunks (longer than MTU on older firmware). 16 cells × 2 bytes each
= 32-byte payload. Each cell is uint16 millivolts; healthy is 3500–4200 mV.

### `f31b` StanceFootpads

uint8, encodes which footpad sensors are active:
- 0 = neither (board off / dismounted)
- 1 = front pad only
- 2 = both engaged (riding)
- 3 = back pad only

Going from 0 → 1 → 2 is the "engagement" sequence the board uses to decide
whether to spin up the motor.

## Notification cadence

When fully unlocked and riding, expect roughly:
- Pitch / Roll / RPM / Amps — 25–50 Hz
- Battery V / Battery % — 1–4 Hz
- Temperatures — 1 Hz
- StatusError / Footpads — on-change only

Without unlock on Pint/GT you typically still get StatusError + Battery%
notifications but Pitch/RPM/Amps stop after a few seconds.

## Writing values

The app uses `f302` (RidingMode) to switch shaping profiles, and `f30c…f30e`
to control lights. **Do not write while riding** — it's possible to nose-dive
yourself. Writes should be `write-with-response` (0x12 ATT op).
