#!/bin/sh
# Build the password file from the MQTT_VTN_PASSWORD Fly secret at start-up,
# so no credential is stored in the image or the repo.
set -eu
: "${MQTT_VTN_PASSWORD:?MQTT_VTN_PASSWORD secret is not set}"
PW=/mosquitto/config/passwd
rm -f "$PW"
mosquitto_passwd -b -c "$PW" vtn "$MQTT_VTN_PASSWORD"
chown mosquitto:mosquitto "$PW" /mosquitto/config/acl
chmod 0700 "$PW" /mosquitto/config/acl
exec mosquitto -c /mosquitto/config/mosquitto.conf
