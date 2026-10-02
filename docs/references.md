# References

## Open-source Onewheel projects

### `gemurf` (Go) — XR challenge reference
Canonical reference for the Onewheel+ / XR GemAuth handshake. Single file,
~150 lines, easy to port. Search GitHub for "gemurf onewheel".

### `pOnewheel` (Android, Java) — full app, current Pint/GT unlock
Working FOSS replacement for the official app, supports current FM
firmwares including Pint X / GT. The unlock implementation lives roughly
in `…/onewheel/SerialAuth.java` (path varies by fork). Transcribe to
Python when you need GT support.

### `OWCE` / "Onewheel Community Edition" (TypeScript / web Bluetooth)
Browser dashboard. Useful as a sanity check — open Chrome → connect → see
what fields populate without unlock vs. with.

### `Float Drive` / `Onewheel Diagnostic Tools (ODT)` — closed-source iOS
Mentioned for completeness; reverse-engineering target rather than reference.

### `ponewheel` (Android, Kotlin) — older, V1/+/XR focused
Cleaner code than `pOnewheel` for understanding the basic protocol but it
predates Pint/GT.

## Reverse-engineering writeups

Search terms that consistently surface useful posts:
- "Onewheel BLE service e659f300"
- "Onewheel GemAuth challenge"
- "Onewheel CRX unlock"
- "Future Motion firmware reverse engineering"

The Onewheel Wiki / r/Onewheel / the Float Life Discord all archive
characteristic maps and unlock progress.

## Capturing the official app's unlock for Pint / GT

End-to-end recipe to extract the response algorithm without root:

1. **Phone side** — enable HCI snoop:
   ```
   Developer Options → Enable Bluetooth HCI snoop log
   ```
   Toggle Bluetooth off and back on.

2. **Pair fresh and connect** — open the Onewheel app, connect to the board,
   leave it for ~60 s so the unlock + a bit of riding telemetry land in the log.

3. **Pull the snoop log**:
   ```
   adb bugreport bugreport.zip
   unzip -p bugreport.zip 'FS/data/misc/bluetooth/logs/btsnoop_hci.log' \
       > captures/snoop.log
   ```

4. **Open in Wireshark**, set display filter:
   ```
   btatt && (btatt.handle == HANDLE_OF_F3FE || btatt.handle == HANDLE_OF_F3FF)
   ```
   You'll see the 20-byte challenge (`Handle Value Notification` on `f3fe`)
   followed within a second by the 20-byte response (`Write Request` on `f3ff`).

5. **Diff against the XR algorithm** — copy several
   challenge/response pairs into a script, run them through `xr_response()`,
   and see whether the bytes match. If they don't, you're on the new
   Pint/GT key schedule and need the pOnewheel implementation.

## BLE tooling on Linux

- `bluetoothctl` — pair / unpair / scan from a TTY.
- `btmon` — live HCI trace from BlueZ. `sudo btmon -w capture.btsnoop`
  while running the Python scripts is the easiest way to debug.
- `nRF Connect` (Android, separate phone) — alternative GATT browser; useful
  for sanity-checking the OW's service tree without dealing with bleak.
