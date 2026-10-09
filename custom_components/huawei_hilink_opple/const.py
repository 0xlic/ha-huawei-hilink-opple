"""Constants for Huawei HiLink Opple lights."""

from typing import Final

DOMAIN: Final = "huawei_hilink_opple"
PLATFORMS: Final = ["light"]

CONF_AUTH_CODE: Final = "auth_code"
CONF_DEVICE_ID: Final = "device_id"
CONF_MODEL: Final = "model"

DEFAULT_NAME: Final = "Huawei Opple Ceiling Light"
DEFAULT_MODEL: Final = "MX420/MX480 HiLink"
DEFAULT_POLL_INTERVAL: Final = 15
FAST_REFRESH_TIMEOUT: Final = 1.0
RECOVERY_WINDOW_SECONDS: Final = 30.0
MIN_COLOR_TEMP_KELVIN: Final = 2700
MAX_COLOR_TEMP_KELVIN: Final = 5700
