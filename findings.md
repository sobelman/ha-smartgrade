# SmartGrade Secure (IoTech Verticals / "Dealor") → Home Assistant: findings

Source: static analysis (jadx) of `il.co.dealor` **v3.0.5 (versionCode 124)**, APK from APKPure, decompiled into `src/`.
**Written before live testing; where this conflicts with `VERIFIED.md`, `VERIFIED.md` wins** (e.g. the device list is a bare array, `last_usage_stats` has no `kwh`, JWT lifetime is 30 days, `05XXXXXXXX` works). Confidence: **HIGH** = read directly from code; **MED** = inferred from code;
**LOW/GAP** = needs a live capture.

## 0. Existing integrations
None found: no GitHub repos (searched smartgrade / dealor / iotechv / iotech verticals), nothing on the HA community forum,
and no HACS integration. The client and integration in this folder are new.

## 1. Framework
Native Android with Kotlin. It uses Retrofit2, OkHttp, Gson, RxJava2, Dagger and Eclipse Paho MQTT. It is not Flutter or React Native, so the
code decompiles cleanly. Flavor `prodDealor`. (HIGH)

## 2. Hosts
| What | Value | Conf |
|---|---|---|
| REST base | `https://api.iotechv.com/` (AWS eu-west-1, 54.217.19.105) | HIGH |
| MQTT broker | `tcp://vernemq.iotechv.com:1883` (**plain TCP, no TLS**; VerneMQ, 63.32.36.56) | HIGH |
| Firebase | project `dealoruser` (only analytics/crashlytics/FCM, not used for auth) | HIGH |

**TLS pinning:** none found. There is no OkHttp `CertificatePinner`, no custom TrustManager and no `networkSecurityConfig`. Because there is no
network security config, the app **does not trust user-installed CAs on Android 7+**, so a plain HTTP Toolkit or mitmproxy setup on a stock phone
**will not see the traffic** unless the phone is rooted or the APK is patched (see §7). `usesCleartextTraffic="true"`. (HIGH)
The app also has `HttpLoggingInterceptor(BODY)` enabled in release, but logcat output from a release build is usually stripped or invisible without adb.

## 3. Auth flow (HIGH unless noted)
Each request carries headers set by an OkHttp interceptor (`ApplicationModule.okHttpClient`):

1. **App token**: `GET api/v1/apps/token` with `Authorization: Basic base64("dealor_app:<app password>")` (value: see `custom_components/smartgrade/const.py``.
   These are app-level credentials hardcoded in BuildConfig.
   The response is `{"token": "...", "exp": <epoch s>}`. The app re-fetches it when less than 1 h remains.
2. **Request SMS**: `POST api/v1/customers`. Headers: `X-APP-TOKEN: Bearer <appToken>`.
   Body: `{"customer":{"mobile":"05XXXXXXXX"}}` (name and email are null and omitted).
   Response: `{"success":true,"mobile":"..."}`. The only client-side validation is length > 8. The exact number format the server wants (05… vs +972…) is a **GAP**.
3. **Submit code**: `POST api/v1/users/login/code`. Headers: `X-APP-TOKEN`.
   Body: `{"user":{"code":"123456"}}`. Note that **the phone number is not sent**, so the server must link the code to the request somehow,
   probably by the code itself or the app token (MED).
   Response: `{"jwt":"<JWT>","user":{"id","mobile","name","email","created_at","mailing_confirmation"}}`.
4. **Authenticated calls** send both `Authorization: Bearer <jwt>` and `X-APP-TOKEN: Bearer <appToken>`, plus `Content-Type` and `Accept: application/json`.
5. **No refresh endpoint.** The app decodes the JWT `exp` and sends the user back through SMS registration when less than 1 h remains (`IsNeedRegisterUseCase`).
   The **JWT lifetime is a GAP**. If it is long (months), HA works without intervention. If it is short, HA needs a reauth flow, which is implemented.
6. The JWT carries `typ, sub, scopes, nbf, jti, iss, iat, exp, aud`.

**Open question (GAP):** does a new login from HA invalidate the phone's JWT, or the reverse? If it does, you would have to choose between HA and the app,
or capture and reuse a single token.

## 4. REST endpoints (all from `RetrofitApi.java`, HIGH for path, method and body shape)
Read:
- `GET api/v1/users/profile` → user (`id` = the `{uuid}` below)
- `GET api/v1/users/{uuid}/sites?shared=true` → `{"sites":[{id,name,client_id,user_id,city,country,street_name,street_number,image_id,devices:[...]}]}`
- `GET api/v1/sites/{site_id}/devices` → `{"devices":[Device...]}`
- `GET api/v1/sites/{site_id}/devices/{device_id}` → `{"device":Device}`
- `GET api/v1/devices/find/{device_id}` → `Device`
- `GET api/v1/devices/{id}/last_usage_stats` → `{"watt":f,"amper":f,"volt":f,"kwh":f}` (power sensor; the meaning of `kwh` is a GAP)
- `GET api/v1/devices/{id}/kwh?from=<s>&to=<s>&tz=<int>` → `{"kwh":{"newData":[{"date":s,"usage":f}],"oldData":[...]}}` (the unit of `tz` is a GAP)
- `GET api/v1/devices/{id}/next_action`, `/future_actions`, `/actions`, `/actions_history`, `/timers`, `/thermostat`
- `GET api/v1/devices/shared_by_me`, `/shared_with_me`, `GET api/v1/scenarios`, `GET api/v1/scenarios/{id}`

Control:
- **`POST api/v1/devices/{id}/toggle_switches` body `{"switch_1": true}`** (also `switch_2`/`switch_3`; null fields are omitted) → the same shape is echoed back.
  **This is the path the current UI uses** (`SiteListViewModel`, `DeviceRemoteViewModel`). HIGH.
- `POST api/v1/devices/{id}/power` body `{"power_on": bool}` → Device. It is still declared, but nothing in the presentation layer calls it any more, so it is probably legacy (MED).
- Also present: timers CRUD (`POST/PUT/DELETE api/v1/devices/{id}/timers[/{timer_id}]`), `POST api/v1/scenarios/{id}/start`, `POST api/v1/devices/{id}/thermostat`,
  shares, site and device CRUD, `POST api/v1/devices/reset_all` (**dangerous, do not call**), `DELETE api/v1/users/{uuid}` (**dangerous**).

Device object (`DeviceEntity`): `id, name, description, location, site_id, product_id, firmware, is_online, has_current, switch_num,
switch_1/2/3 (bool), switch_1/2/3_name, switch_type ("ud"|"du"|"lr"), max_on_min, icon_id, timers[], upgradeNow, createdDate`. HIGH.
Whether `switch_1` in the list response reflects live state is MED; the app also overlays MQTT data.

Product IDs (from app strings): 6 = boiler switch under plaster, 7 = WiFi switch for box 55, 8 = boiler switch Gewiss 3-module,
14/15/22 = DIN 1-phase / 3-phase / 63A, 2/5/16/17 = sockets, 9–13 = light switches, 18/19/36 = shutters,
20/24/30 = AC panel, 25/35 = IR, 21 = Zigbee gateway, 23 = power meter, 26–29/31 = sensors.

## 5. MQTT push (state and power, read-only in the app) (HIGH for structure)
- Broker: `vernemq.iotechv.com:1883`, no TLS, keepalive 30, cleanSession, auto-reconnect.
- **username = user id, password = user JWT, clientId = user id.** If HA uses the same clientId, it will kick the phone off the broker and the reverse.
  Whether the broker accepts a different clientId such as `<uid>-ha` is a **GAP**.
- The app **only subscribes and never publishes**; control goes over REST.
- Topics:
  - `s/{site_id}/{device_id}/power` (the app uses `s/{site_id}/+/power` on the site list): `{"switch_1":b,"switch_2":b,"switch_3":b,"switch_on":b,"current":f,"power":f,"voltage":f,"t":ms?}`
  - `s/{site_id}/online`: `{"devKey":"<device id>","online":b,"t":..}`
  - `s/{site_id}/{device_id}/sensor`: `{"alert":b,"t":..}`
  - `.../AC_status`, `.../ac`, `.../AC_lock` (`{"keypadLock":b}`), `.../pairing_code`: thermostat and IR devices.
- Payload units (W/A/V) are assumed. Whether `t` is in seconds or milliseconds is a GAP.

## 6. Local LAN protocol
No local control path exists in the app. The only LAN code is **Wi-Fi provisioning in AP mode**:
- WiFi switches: the phone joins SSID `iotech-device-<mac>` and opens TCP to `192.168.4.1:8000`, sending `{"op":"set","ssid":..,"pass":..}` then `{"op":"close"}`.
  The broker address is not configurable from the app, so it is probably baked into the firmware.
- Zigbee gateway (OWON OEM): SSID `ZB_GW_<mac>`, TCP `192.168.1.1:11500`, STX/ETX-framed JSON with default login `owon`/`123456`.
- A local option would mean redirecting the device's DNS for `vernemq.iotechv.com` to your own broker. That is very speculative, untested, and would break the app.

## 7. What still needs a live capture
1. Phone number format, and the actual responses of `customers` and `login/code`.
2. JWT `exp` (lifetime). You can decode it at jwt.io once you have the token.
3. Whether `switch_1` in the device-list response is live state, and what `toggle_switches` returns.
4. Real `last_usage_stats` values (is `kwh` today's total or lifetime?).
5. MQTT: whether a second clientId is accepted, and real payloads and timestamps.
6. Whether logging in from HA logs out the phone.

**Simplest ways to capture:**
- **No root (recommended):** run [`apk-mitm`](https://github.com/shroudedcode/apk-mitm) on the APK (`npx apk-mitm il.co.dealor.apk`).
  It adds a network-security-config that trusts user CAs. Install the patched APK (uninstall the original first, which requires logging in again).
  Then use **HTTP Toolkit** (Android app plus desktop) or mitmproxy with its CA installed as a user CA. You will see all REST traffic, including the JWT.
- **PCAPdroid** (no root) can capture the MQTT traffic as-is, because MQTT uses plain TCP 1883. Its pcap shows CONNECT (username = user id,
  password = JWT) and the PUBLISH payloads. **This alone gives you the JWT and user id without any APK patching.** It cannot decrypt HTTPS
  unless you use its TLS-decryption add-on, which has the same user-CA limitation.
- With root or an emulator, use HTTP Toolkit's "Android device via ADB" option with system-CA injection.

## 8. Files
- `smartgrade_client.py` (not part of this repository) is a sync Python research client.
- `custom_components/smartgrade/` is the Home Assistant integration: config flow (phone, then SMS code), reauth, a 30 s polling coordinator,
  switch entities (one per `switch_N`), and power/current/voltage (+ diagnostic signal strength) sensors for every device that returns usage stats. MQTT push is a TODO.
- The decompiled sources and the APK are intentionally **not** distributed.
