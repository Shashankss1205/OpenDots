#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
demo_support="$PWD/.opendots/demo-support"
demo_run="${1:-$PWD/.opendots/showcase-$(date +%Y%m%d-%H%M%S)-$$}"
demo_codex="${OPENDOTS_CODEX_COMMAND:-${SPOTS_CODEX_COMMAND:-codex}}"
for dependency in python3 git node npm bwrap "$demo_codex"; do
  command -v "$dependency" >/dev/null || { echo "Required command missing: $dependency" >&2; exit 1; }
done
"$demo_codex" login status
mkdir -p "$demo_support"
if [[ ! -x "$demo_support/venv/bin/python" ]]; then python3 -m venv "$demo_support/venv"; fi
"$demo_support/venv/bin/python" -m pip install pytest==8.4.2
npm install --prefix "$demo_support/js" --no-audit --no-fund react@19.1.1 react-dom@19.1.1 sucrase@3.35.0 happy-dom@18.0.1 dom-accessibility-api@0.7.0 esbuild@0.25.10
if [[ ! -d "$demo_support/cachetools/.git" ]]; then
  git clone --depth 1 --branch v6.2.1 https://github.com/tkem/cachetools.git "$demo_support/cachetools"
fi
python3 scripts/prepare_live_demo.py --directory "$demo_run" --node-dependencies "$demo_support/js/node_modules" --cachetools-source "$demo_support/cachetools" --pytest-python "$demo_support/venv/bin/python" --codex-command "$demo_codex" --workers "${OPENDOTS_DEMO_WORKERS:-${SPOTS_DEMO_WORKERS:-3}}"
echo "Open http://127.0.0.1:${OPENDOTS_DEMO_PORT:-${SPOTS_DEMO_PORT:-8765}}. React starts from its actual schedule."
echo "Send a ci.failure event to Cachetools, review the exact changes, and inspect the passing checks and retained patches."
exec python3 -m opendots --config "$demo_run/config.json" serve --port "${OPENDOTS_DEMO_PORT:-${SPOTS_DEMO_PORT:-8765}}"
