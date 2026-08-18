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
