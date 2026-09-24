#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
python_bin=${LOOK4GEO_PYTHON:-python3.12}

command -v "$python_bin" >/dev/null 2>&1 || {
  printf '%s\n' "Python 3.12 is required. Set LOOK4GEO_PYTHON to its executable path." >&2
  exit 1
}
command -v node >/dev/null 2>&1 || {
  printf '%s\n' "Node.js 22.22.0 or newer is required." >&2
  exit 1
}

node -e 'const [major, minor] = process.versions.node.split(".").map(Number); process.exit(major > 22 || (major === 22 && minor >= 22) ? 0 : 1)' || {
  printf '%s\n' "Node.js 22.22.0 or newer is required." >&2
  exit 1
}

cd "$project_root"
"$python_bin" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'

mkdir -p vendor tools/promptfoo
if [ ! -d vendor/AI-Search-Hub/.git ]; then
  git clone https://github.com/minsight-ai-info/AI-Search-Hub.git vendor/AI-Search-Hub
fi

if [ ! -f tools/promptfoo/package.json ]; then
  npm init --yes --prefix tools/promptfoo >/dev/null
fi
npm install --prefix tools/promptfoo promptfoo

printf '%s\n' "Look4GEO dependencies installed. Run .venv/bin/probe doctor next."

