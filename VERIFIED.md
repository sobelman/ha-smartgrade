# Live verification — 2026-11-20 (maintainer's own account; personal identifiers redacted)

All confirmed against the real server https://api.iotechv.com — no emulator needed.

- App token: `GET api/v1/apps/token` with Basic `dealor_app:...` → HTTP 200. exp ~year 2046 (effectively permanent).
- SMS request: `POST api/v1/customers {"customer":{"mobile":"05XXXXXXXX"}}` → HTTP 200, SMS delivered. **05XXXXXXXX format works.**
- Code submit: `POST api/v1/users/login/code {"user":{"code":"####"}}` → returns `{jwt, user:{id:<user id>, mobile}}`.
- **JWT lifetime = exactly 30 days** (iat→exp = 2,592,000 s). HA will need a fresh SMS code ~monthly.
- Account: 1 user, 1 site, 1 device.
- Device (a boiler): id `<12-hex-char id, MAC-like>`, product_id 8 (boiler, Gewiss 3-module), 1 switch, firmware 20260410_var2_v2.9.2, online. Has a recurring timer 06:00–06:50 Sun–Thu.
- State in `GET api/v1/sites/{site}/devices` is **live** (switch_1 reflects reality). NOTE: this endpoint returns a **bare JSON array**, not {"devices":[...]}.
- Control: `POST api/v1/devices/{id}/toggle_switches {"switch_1":true/false}` **works**. The JSON it echoes back can show a stale switch_1, so read state from the device list, not from the toggle response.
- Power metering: `GET api/v1/devices/{id}/last_usage_stats` → `{volt, amper, watt, watt2, watt3, powerFactor, signalStrength, createdAt}`. **No kwh field here.** Updates ~once per minute.
  - OFF: ~241 V, 0 A, 0 W.
  - ON (test): 236.8 V, 12.29 A, **2707 W** — then turned back OFF, returned to 0 W.
- has_current was null in the device object even though metering works, so sensors are created whenever stats return, not based on has_current.

## Not verified live (needs a capture or a second account)
- Whether an HA login (new JWT) logs the phone app out, or vice versa.
- MQTT push: whether the broker accepts a clientId different from the user id. Reusing the user id as clientId disconnects the phone, so the integration polls instead.
- Devices with 2 or 3 relays (`switch_2`, `switch_3`), shutters, thermostats, IR and sensor products — only a single-relay boiler switch (product_id 8) was tested.
- Units/range of `powerFactor`, `watt2`, `watt3` (3-phase?) and the timezone/format of `createdAt`. Since v0.2.0 they are exposed best-effort (power factor as a 0–1 ratio, `createdAt` parsed as ISO 8601 or epoch, phase 2/3 power enabled by default only on 3-phase products).
- The response shapes of `next_action`, `future_actions` and `timers` (taken from the app's models, not captured live), the meaning of `upgradeNow`, and the units of the `/kwh` history (not exposed).
- Exact HTTP status codes for a wrong SMS code / rejected phone number (the integration treats any 4xx as "invalid").
- Rate limits of the cloud API (none hit at a 30 s poll interval).
