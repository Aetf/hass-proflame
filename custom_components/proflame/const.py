"""Constants for the SIT Proflame integration."""

DOMAIN = "proflame"

CONF_TRANSMITTER = "transmitter"
CONF_FREQUENCY = "frequency"
CONF_SERIAL1 = "serial1"
CONF_SERIAL2 = "serial2"
CONF_VERSION = "protocol_version"
CONF_KEY1 = "key1"
CONF_KEY2 = "key2"

#: Dispatcher signal the hackrf_proxy transmitter re-broadcasts frames on,
#: formatted with its config entry id. Interim, until Home Assistant has a
#: receiver platform of its own.
SIGNAL_RX_FRAME = "hackrf_proxy_rx_frame_{}"

#: How long the config flow listens for a button press while learning which
#: handset it is talking to.
LEARN_TIMEOUT = 60

CONF_TEMPERATURE_SENSOR = "temperature_sensor"

#: Auto-off durations offered by the timer, in minutes. "none" disarms it.
AUTO_OFF_NONE = "none"
AUTO_OFF_OPTIONS: dict[str, int] = {
    "30_minutes": 30,
    "1_hour": 60,
    "2_hours": 120,
    "3_hours": 180,
    "4_hours": 240,
}
