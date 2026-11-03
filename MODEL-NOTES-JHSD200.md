# Model notes & Windows guide: JHSD200 + JHSB200 (JioOS 2.0)

Validation of the full sideload chain on two models the repo was not originally
written for, plus the step-by-step Windows guide used for both. Tested by:
@kapi2800, 2026-11.

- **JHSD200** — JioOS 2.0, firmware `STB-JHSD200 11.3.24`
- **JHSB200** — JioOS 2.0, firmware `Too lazy to check but it worked...`

Both jailbroken with the same method over the complete flow: forged eMudhra
ACS chain -> pass_front -> GenieACS 1.2.13 -> TR-069 task queue -> USB
debugging -> ADB sideload.

## Result

The chain works on both boxes with no model-specific gating. The forge script
mints the EM DV TLS CA - G3B leaf (recovered scalar d=156), both boxes accept
the chain anchored on Sectigo AAA Certificate Services and the TR-069
parameter queue enables USB debugging exactly as on JHS-series boxes
documented earlier.

## Fixes required on the PC side

1. `jhs/forge_emudhra_chain.py` — on Python 3.13+, `SSLSocket.get_verified_chain()`
   returns DER-encoded `bytes`, not certificate objects. The intermediate is now
   parsed with `x509.load_der_x509_certificate()`, with a short-chain fallback to
   `openssl s_client`. Symptom before: `AttributeError: 'bytes' object has no
   attribute 'not_valid_before'`.

2. `doctor.py --simulate` step [3/4] — GenieACS 1.2 has no `GET /devices/{id}`
   NBI route (the path only carries DELETE, so GET returns 405). Device lookup
   now goes through the query API. Symptom before: `[FAIL] ... (HTTP 405)`.

3. `doctor.py --simulate` Inform payload — the original synthetic Inform (bare
   `1 BOOT`, one untyped parameter) is answered with `InformResponse` + 204 but
   GenieACS never commits the device to MongoDB. The payload is now shaped like
   a real first-boot CPE (`0 BOOTSTRAP` + `1 BOOT`, full `Device.DeviceInfo.*`
   identity block, `xsi:type`d values), which persists. Symptom before:
   `[FAIL] ... (HTTP 200)` with an empty database.

4. `doctor.py --simulate` id matching — the `drumsergio/genieacs:1.2.13.2`
   image stores device ids with percent-encoded hyphens
   (`000000-TEST%2DJioSTB-...`). Matching now normalises `%2D` before compare,
   failure dumps every stored device id for diagnosis, and cleanup tries both
   the encoded and plain spellings.

## Windows step-by-step

1. Install the four prerequisites (Docker Desktop, Python 3, Technitium DNS
   Server, Git for Windows) and clone the repo.
2. Open the Technitium DNS web UI and set it up as per the README (zone
   `jio.com`, A record `acs.oss` -> the PC's LAN IP).
3. Turn off Windows Firewall for Domain / Private / Public profiles.
4. Start the ACS stack: `cd tls` then `docker compose up -d`.
5. Use VS Code's built-in Git Bash terminal. (Just for ease.)
6. Terminal 1: `./jhs/run_acs.sh` and leave it running — it prints its banner
   and stays in the foreground; that is normal, it is the TLS front listening.
7. Terminal 2: `python doctor.py --quick` — the DNS check shows a WARN on the
   PC itself; that is expected (the PC resolves the name publicly; what matters
   is what the STB resolves through Technitium). Then `python doctor.py
   --simulate` — with the stock file it fails at step [3/4]; use the fixed
   `doctor.py` from this repo (see fixes above) and all four steps PASS.
8. `./stage1.sh --list` (run `./stage1.sh` bare to see the exact flag in the
   usage banner), then `./stage1.sh --tasks-only`. Choose **your own STB's**
   device entry — it looks like `<OUI>-JHSD200-<serial>` / `<OUI>-JHSB200-
   <serial>` and appears after the box has informed once. The `000000-TEST-...`
   entry is only the `--simulate` self-test leftover; queueing tasks to it
   never reaches a real box. Enter the STB IP when asked. If the USB-debugging
   task does not land on the first try, cold power-cycle the STB (unplug wall
   power) and retry — it goes through eventually.
9. Finish with `jhs/jhs_stage1.sh --post-setup` per the README. Happy to help
   if it does not work for you — tag me.

### Network / DNS setup used

- A reset Jio router (I did reset mine...) with the PC and the STB both connected to it.
- STB: Settings -> WiFi -> change to static, IP in the same subnet as the PC
  (192.168.xx), last octet something unused (11, 12 — anything that does not
  conflict with the router or the PC). DNS 1: the PC's IPv4 address — check
  using `ipconfig` in the terminal.

## Operational notes for JHSD200 / JHSB200 owners

- The stock `DEVICE_ID` default in `jhs/jhs_stage1.sh` is a JHSC200 example.
  Export your own before queueing tasks:
  `export DEVICE_ID=<OUI>-JHSD200-<serial>` — copy the id exactly as GenieACS
  displays it, including any `%2D` encoding.
- On --yes-disable-tr069: I ran ./stage1.sh --yes-disable-tr069 on both of my boxes and have not seen any side effects — ADB survives reboots on my firmware. It does warn you not to, though. Use your own judgement or ask @ilikechocopies.
- OTA already handled by Stage 6 (`pm disable-user` on the FOTA/update packages,
  staged updates cleared); `jhs_stage1.sh --post-setup` disables the
  `com.iwedia.*` update services as a second layer.
- If a queued parameter faults with CWMP 9007 ("Invalid parameter Value"), the
  parameter name may not exist on this firmware — use
  `jhs/jhs_stage1.sh --clu-probe` (model-agnostic) instead. This fault was also
  observed on JHSC200.
- On Linux/macOS hosts no changes are needed: the scripts are native bash,
  `adb` comes from your package manager (`stage1.sh` auto-falls back to PATH),
  and all Python is stdlib + `cryptography`/`pycryptodome`.
- Blah blah... that's it..!
