"""Constants for the SmartGrade Secure (IoTech Verticals / Dealor) integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "smartgrade"
MANUFACTURER: Final = "IoTech Verticals (Dealor)"

BASE_URL: Final = "https://api.iotechv.com/"

# App-level credentials hardcoded in the official Android app (BuildConfig of
# il.co.dealor). They are NOT user credentials: every copy of the app ships with
# them, and they are only used for GET api/v1/apps/token, exactly like the app.
# If the vendor rotates them the integration stops working until they are updated
# here. See PUBLISHING.md for the implications.
APP_USER: Final = "dealor_app"
APP_PASSWORD: Final = "J2AQHSrNXMkuv70UDh2F"

CONF_MOBILE: Final = "mobile"
CONF_CODE: Final = "code"
CONF_JWT: Final = "jwt"
CONF_USER_ID: Final = "user_id"

SCAN_INTERVAL: Final = timedelta(seconds=30)
REQUEST_TIMEOUT: Final = 15

# product_id values (from the app's strings) that are plain on/off relays.
# Sensors (26-29, 31), IR/AC (20, 24, 25, 30, 35), shutters (18, 19, 36) and the
# gateway (21) are not exposed as switches.
SWITCH_PRODUCTS: Final = frozenset(
    {2, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 22}
)

PRODUCT_NAMES: Final = {
    2: "Smart socket",
    5: "Smart socket",
    6: "Boiler switch (under plaster)",
    7: "Wi-Fi switch (box 55)",
    8: "Boiler switch (Gewiss 3-module)",
    9: "Light switch",
    10: "Light switch",
    11: "Light switch",
    12: "Light switch",
    13: "Light switch",
    14: "DIN switch (1-phase)",
    15: "DIN switch (3-phase)",
    16: "Smart socket",
    17: "Smart socket",
    22: "DIN switch (63A)",
}
