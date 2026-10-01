#!/usr/bin/env bash
# Keep execution inside main so curl | bash receives the complete script first.
set -euo pipefail

main() (
  local install_prefix="${OPENDOTS_INSTALL_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/opendots/runtime}"
  local install_bin="${OPENDOTS_BIN_DIR:-$HOME/.local/bin}"
  local install_ref="${OPENDOTS_REF:-}"
  local install_version="${OPENDOTS_VERSION:-}"
  local install_source=""
  local install_command install_target install_temp=""

  while (($#)); do
    case "$1" in
      --prefix|--bin-dir|--version|--ref|--source)
        if (($# < 2)) || [[ -z "$2" || "$2" == --* ]]; then
          echo "Missing value for $1" >&2; return 2
        fi
        case "$1" in
          --prefix) install_prefix="$2" ;;
          --bin-dir) install_bin="$2" ;;
          --version) install_version="$2" ;;
          --ref) install_ref="$2" ;;
          --source) install_source="$2" ;;
        esac
        shift 2 ;;
      --help)
        echo 'Usage: bash install.sh [--prefix DIR] [--bin-dir DIR] [--version VERSION | --ref TAG_OR_COMMIT | --source CHECKOUT]'
        echo 'Default: install the latest opendots release from PyPI in an isolated environment.'
        return 0 ;;
      *) echo "Unknown option: $1" >&2; return 2 ;;
    esac
  done
  if [[ -n "$install_version" && ( -n "$install_ref" || -n "$install_source" ) ]] || [[ -n "$install_ref" && -n "$install_source" ]]; then
    echo 'Choose only one of --version, --ref, or --source (including environment overrides).' >&2
    return 2
  fi
  if [[ -n "$install_version" && ! "$install_version" =~ ^[0-9][a-zA-Z0-9.!+_-]*$ ]]; then
    echo 'Invalid version: use a release number such as 0.2.0.' >&2; return 2
  fi
  for install_command in python3 git; do
    command -v "$install_command" >/dev/null || { echo "Install $install_command first." >&2; return 1; }
  done
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else "Python 3.11+ is required")'
  if [[ "$(uname -s)" != Linux ]]; then
    echo 'Default isolated checks require Linux. Use a Linux host or WSL2.' >&2; return 1
  fi
  if ! command -v bwrap >/dev/null; then
    echo 'Install bubblewrap first (Ubuntu/Debian: sudo apt-get install bubblewrap python3-venv git).' >&2
    return 1
  fi
  # Absolute paths keep the command link valid when custom relative paths are used.
  install_prefix="$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$install_prefix")"
  install_bin="$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$install_bin")"
  if [[ -e "$install_prefix" || -L "$install_prefix" ]]; then
    echo "Destination exists: $install_prefix. Choose a new --prefix for a separate installation." >&2; return 1
  fi
  if [[ -e "$install_bin/opendots" || -L "$install_bin/opendots" ]]; then
    echo "Command already exists: $install_bin/opendots. Choose another --bin-dir." >&2; return 1
  fi
  if [[ -n "$install_ref" ]]; then
    install_temp="$(mktemp -d)"
    # Expand only the variable name here; paths are always quoted at cleanup time.
    trap 'rm -rf -- "$install_temp"' EXIT
    git clone --quiet https://github.com/Shashankss1205/OpenDots.git "$install_temp/source"
    git -C "$install_temp/source" checkout --quiet --detach "$install_ref"
    install_source="$install_temp/source"
  fi
  if [[ -n "$install_source" ]]; then
    install_source="$(cd "$install_source" && pwd)"
    [[ -f "$install_source/pyproject.toml" ]] || { echo 'Source must be an OpenDots checkout.' >&2; return 1; }
    install_target="$install_source"
  else
    install_target="opendots${install_version:+==$install_version}"
  fi
  echo "Installing $install_target..."
  if ! python3 -m venv "$install_prefix"; then
    echo "Could not create the environment. Install python3-venv and inspect $install_prefix before retrying." >&2
    return 1
  fi
  if [[ -n "$install_source" ]]; then
    "$install_prefix/bin/python" -m pip install --disable-pip-version-check "$install_target"
  else
    "$install_prefix/bin/python" -m pip install --disable-pip-version-check --index-url https://pypi.org/simple "$install_target"
  fi
  "$install_prefix/bin/opendots" --version
  mkdir -p "$install_bin"
  ln -s "$install_prefix/bin/opendots" "$install_bin/opendots"
  if [[ -n "$install_temp" ]]; then
    rm -rf -- "$install_temp"
    trap - EXIT
  fi
  echo "Installed command: $install_bin/opendots"
  case ":$PATH:" in
    *":$install_bin:"*) ;;
    *) printf '\nAdd this line to your shell startup file and run it in this terminal:\nexport PATH=%q:"$PATH"\n' "$install_bin" ;;
  esac
  echo
  echo "Next: opendots init --workspace /absolute/path/to/project --goal 'Your goal' --backend claude"
  echo 'Then: opendots doctor'
  echo 'Start: opendots serve'
)

main "$@"
