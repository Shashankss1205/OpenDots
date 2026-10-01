#!/bin/sh
set -eu
runtime_config="${OPENDOTS_CONFIG:-/data/config.json}"
if [ ! -f "$runtime_config" ]; then
  if [ "$runtime_config" != /data/config.json ]; then
    echo "Configured file is missing: $runtime_config" >&2
    exit 1
  fi
  if [ "${OPENDOTS_DEMO:-0}" = 1 ]; then
    opendots init --demo --directory /data
  else
    echo 'Mount your real config and set OPENDOTS_CONFIG. Optional fixtures require OPENDOTS_DEMO=1.' >&2
    exit 1
  fi
fi
exec opendots --config "$runtime_config" "$@"
