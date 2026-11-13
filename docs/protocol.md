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
[challenge]  board → app  on f3fe (20 bytes)        ◄── XR: answer with MD5 solver
[response]   app → board on f3ff (20 bytes, write-with-response)
             Pint S: no challenge to an un-bonded central — write the
             static per-board token to f3ff, then 0x0001 → f317, then
             0x00 → f30b every ~200 ms as keepalive
   │
[stream] ──► notifications flow ~10–50 Hz on the live channels
```

## Packet shapes

Almost everything is a 2-byte big-endian payload. Some characteristics
return a single byte (battery percent on some firmware revs, low-battery
flags). A few (battery cells, hardware/firmware versions) pack multi-byte
data.

### `f307` Pitch / `f308` Roll / `f309` Yaw

Big-endian uint16 with **1800** as zero: degrees = `(raw - 1800) / 10`.
Verified on the Pint S (`0x0709` = 1801 → +0.1° at near-level engagement;
`tests/fixtures/ride_10s_sample.csv` stays within ±20° for a short ride).
That is what `ow_uuids.decode_pitch_roll_yaw()` implements.

Older community docs describe a `0x8000` offset or plain signed int16 on
earlier generations. Neither is implemented here; if you add support for
another board, make the decoder model-aware and add a capture fixture.

### `f30b` RPM

Big-endian uint16. Wheel rpm. Convert to mph with circumference:
- Pint / GT (10.5" tire): mph ≈ rpm × 0.0124
- XR (11.5" tire): mph ≈ rpm × 0.01359

### `f312` BatteryVoltage

uint16 centivolts (volts × 10). XR pack rests around 580–630 (58–63 V).
GT around 740–840 (74–84 V).

### `f312` CurrentAmps

int16, two's complement. OWCE scales by 0.002 A per count on Pint / Pint X / GT
(0.0018 on Plus, 0.0009 on V1). Positive = motor pulling current, negative =
regen. Reads 0 at rest.

> Earlier versions of this project mislabelled `f310`–`f320` (for example
> `f312` as BatteryVoltage, which is why the e-paper's VOLTS read 0). The
> README table now follows OWCE; `f316` is the real BatteryVoltage and reads
> 0 on this board's firmware, even for the official app.

### `f30f` StatusError

uint16, two sub-fields:
- low byte = state machine
  - 0x00 init, 0x01 boardOff, 0x02 boardOn, 0x03 riding, 0x04 charging,
    0x05 chargedFully, 0x06 shuttingDown
- high byte = fault flags (bitmask) — most-significant bits set on
  pushback / overcurrent / cell imbalance / etc. The exact bit map varies by
  gen and isn't fully published; treat anything non-zero as "something to
  log + look up against `LastErrorCode` (`f31c`)".

### `f31b` BatteryCells

Per OWCE, on firmware ≥ 4141 each value carries a cell id in the top 4 bits
and the cell voltage in the low 12 bits (× 0.0011 V). It read 0 on this
board. There is no known footpad characteristic on the Pint S; footpad state
is probably encoded in `f30f` StatusError bits.

## Notification cadence

When fully unlocked and riding, expect roughly:
- Pitch / Roll / RPM / Amps — 25–50 Hz
- Battery V / Battery % — 1–4 Hz
- Temperatures — 1 Hz
- StatusError / Footpads — on-change only

Observed on the Pint S after unlock: `f3fe` (UART) dominates at ~200 Hz;
Pitch / Roll / RPM arrive at ~5 Hz each. Without unlock, live reads return
`0x0000` and notifications do not fire. `live_monitor.py` redraws its
dashboard at most ~20 Hz regardless of notification rate.

Unmapped characteristics `f31c` and `f31e` also notify on the Pint S; they
are logged as raw hex. `f304` (BatteryLow5) and `f313` (SafetyHeadroom)
carry values that change constantly on the Pint S, so their documented
meanings (bool / % derate) likely do not apply to this board.

## Writing values

The app uses `f302` (RidingMode) to switch shaping profiles, and `f30c…f30e`
to control lights. **Do not write while riding** — it's possible to nose-dive
yourself. Writes should be `write-with-response` (0x12 ATT op).
