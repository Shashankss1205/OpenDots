#!/bin/sh
set -eu
runtime_config="${OPENDOTS_CONFIG:-/data/config.json}"
if [ ! -f "$runtime_config" ]; then
  if [ "$runtime_config" != /data/config.json ]; then
    echo "Configured file is missing: $runtime_config" >&2
    exit 1
  fi
  opendots init --directory /data
fi
exec opendots --config "$runtime_config" "$@"
