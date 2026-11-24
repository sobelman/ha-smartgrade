"""Constants for the SmartGrade Secure (IoTech Verticals / Dealor) integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Final

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

# Slower-changing data (next scheduled action, timer list when it is not
# embedded in the device object) is refreshed at most this often.
EXTRAS_INTERVAL: Final = timedelta(minutes=5)

# product_id -> (English name, Hebrew name). English names follow the app's
# English strings, Hebrew names the app's Hebrew (values-iw) strings. Only
# product 8 has been tested live; everything else is best-effort.
PRODUCTS: Final[dict[int, tuple[str, str]]] = {
    2: ("Smart adapter for Israeli socket", "מתאם חכם לשקע ישראלי"),
    5: ("Smart switch for 16A socket", "מתג חכם לשקע בריטי 16A"),
    6: ("Boiler switch (under plaster)", "מתג חכם לדוד להתקנה תת-טיח"),
    7: ("Wi-Fi switch (box 55)", "מתג חכם WIFI לקופסא 55"),
    8: ("Boiler switch (Gewiss 3-module)", "מתג חכם לדוד לגוויס 3 מקומות"),
    9: ("Light switch, 3 gangs (Gewiss)", "מתג חכם לתאורה 3 לגוויס"),
    10: ("Light switch, 2 gangs (Gewiss)", "מתג חכם לתאורה 2 לגוויס"),
    11: ("Light switch, 1 gang (Gewiss)", "מתג חכם לתאורה 1 לגוויס"),
    12: ("Light switch, 1 gang (box 55)", "מתג חכם לתאורה 1 לקופסא 55"),
    13: ("Light switch, 2 gangs (box 55)", "מתג חכם לתאורה 2 לקופסא 55"),
    14: ("DIN rail switch, 1-phase 16A", "מתג חכם פס דין, חד פאזי 16A"),
    15: ("DIN rail switch, 3-phase 30A", "מתג חכם פס דין, תלת פאזי 30A"),
    16: ("Smart socket (box 55)", "שקע חכם לקופסא 55"),
    17: ("Smart socket (Gewiss 3-module)", "שקע חכם לגוויס 3 מקומות"),
    18: ("Smart shutter", "תריס חכם"),
    19: ("Smart shutter", "תריס חכם"),
    20: ("Smart AC panel", "פאנל מזגן חכם"),
    21: ("Smart gateway (Zigbee)", "גייטווי חכם (Zigbee)"),
    22: ("DIN rail switch, 1-phase 63A", "מתג חכם, פס דין 63A"),
    23: ("Smart power meter", "מונה חשמל חכם"),
    24: ("Smart AC panel", "פאנל מזגן חכם"),
    25: ("Smart IR controller", "אינפרא אדום חכם"),
    26: ("Smart motion sensor", "חיישן תנועה חכם"),
    27: ("Smart water leak sensor", "חיישן הצפה חכם"),
    28: ("Smart smoke sensor", "חיישן עשן חכם"),
    29: ("Smart gas sensor", "חיישן גז חכם"),
    30: ("Smart AC panel I", "פאנל מזגן I חכם"),
    31: ("Smart temperature/humidity sensor", "חיישן טמפרטורה/לחות חכם"),
    35: ("Smart IR controller", "אינפרא אדום חכם"),
    36: ("Smart shutter", "תריס חכם"),
}

# English model names, used for the device registry "model" field.
PRODUCT_NAMES: Final[dict[int, str]] = {pid: en for pid, (en, _) in PRODUCTS.items()}

# Plain on/off relays: one switch entity per relay.
SWITCH_PRODUCTS: Final = frozenset(
    {2, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 22}
)

# Shutter products drive an up and a down relay. Control is not supported yet
# (diagnostic sensors only). A cover entity can be added later, once the
# behaviour is confirmed on a real shutter.
SHUTTER_PRODUCTS: Final = frozenset({18, 19, 36})

# Products without user-facing relays: AC panels, IR, gateway, power meter
# and sensors. They still get the device and diagnostic entities.
NON_RELAY_PRODUCTS: Final = frozenset({20, 21, 23, 24, 25, 26, 27, 28, 29, 30, 31, 35})

# Products that can report power on phases 2 and 3 (watt2/watt3). On other
# devices those values are always 0, so their sensors are disabled by default.
THREE_PHASE_PRODUCTS: Final = frozenset({15, 23})


def device_product_id(device: dict[str, Any]) -> int | None:
    """Return the device's product_id as an int, or None."""
    try:
        return int(device["product_id"])
    except (KeyError, TypeError, ValueError):
        return None


def device_mac(device_id: str) -> str | None:
    """Return the MAC address encoded in a device id (12 hex digits), or None."""
    raw = device_id.replace(":", "").replace("-", "").lower()
    if len(raw) != 12 or any(c not in "0123456789abcdef" for c in raw):
        return None
    return ":".join(raw[i : i + 2] for i in range(0, 12, 2))


def product_name(product_id: int | None) -> str:
    """Return the English model name for a product_id."""
    if product_id in PRODUCT_NAMES:
        return PRODUCT_NAMES[product_id]
    return f"SmartGrade device (product {product_id})"


def relay_count(device: dict[str, Any]) -> int:
    """Return how many relays (switch_1..switch_3) a device exposes as switches.

    Known switch products always have at least one relay. Shutter products and
    products without relays get none. Unknown products get relays only when the
    cloud reports a boolean ``switch_1``.
    """
    product_id = device_product_id(device)
    if product_id in NON_RELAY_PRODUCTS or product_id in SHUTTER_PRODUCTS:
        return 0
    if product_id not in SWITCH_PRODUCTS and not isinstance(
        device.get("switch_1"), bool
    ):
        return 0
    try:
        return min(3, max(1, int(device.get("switch_num") or 1)))
    except (TypeError, ValueError):
        return 1
