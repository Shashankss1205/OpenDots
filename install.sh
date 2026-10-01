#!/usr/bin/env bash
set -euo pipefail
install_prefix="${OPENDOTS_INSTALL_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/opendots/runtime}"
install_ref="${OPENDOTS_REF:-main}"
install_source=""
while (($#)); do
  case "$1" in
    --prefix) install_prefix="$2"; shift 2 ;;
    --ref) install_ref="$2"; shift 2 ;;
    --source) install_source="$2"; shift 2 ;;
    --help) echo 'Usage: bash install.sh [--prefix DIR] [--ref TAG_OR_COMMIT] [--source CHECKOUT]'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
for install_command in python3 git; do
  command -v "$install_command" >/dev/null || { echo "Install $install_command first." >&2; exit 1; }
done
python3 -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ is required"'
if [[ "$(uname -s)" != Linux ]]; then
  echo 'Default isolated checks require Linux. Use a Linux host or WSL2.' >&2
  exit 1
fi
if ! command -v bwrap >/dev/null; then
  echo 'Install bubblewrap first (Ubuntu/Debian: sudo apt-get install bubblewrap python3-venv git).' >&2
  exit 1
fi
install_temp="$(mktemp -d)"
trap 'rm -rf "$install_temp"' EXIT
if [[ -z "$install_source" ]]; then
  git clone --quiet https://github.com/Shashankss1205/OpenDots.git "$install_temp/source"
  git -C "$install_temp/source" checkout --quiet --detach "$install_ref"
  install_source="$install_temp/source"
fi
install_source="$(cd "$install_source" && pwd)"
[[ -f "$install_source/pyproject.toml" ]] || { echo 'Source must be an OpenDots checkout.' >&2; exit 1; }
if [[ -e "$install_prefix" ]]; then
  echo "Destination exists: $install_prefix. Choose a new --prefix for a separate installation." >&2
  exit 1
fi
python3 -m venv "$install_prefix"
"$install_prefix/bin/python" -m pip install "$install_source"
echo "Installed OpenDots from $(git -C "$install_source" rev-parse HEAD)"
echo "Add to PATH: export PATH=\"$install_prefix/bin:\$PATH\""
echo "Next: $install_prefix/bin/opendots init"
echo "Then: $install_prefix/bin/opendots doctor"
echo "Start: $install_prefix/bin/opendots serve"
echo 'The default ref is main; use --ref with a reviewed commit for a reproducible installation.'
