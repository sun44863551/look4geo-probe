#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
python_bin="$project_root/.venv/bin/python"

if [ ! -x "$python_bin" ]; then
  printf '%s\n' "Look4GEO virtual environment is missing. Run scripts/bootstrap.sh first." >&2
  exit 1
fi

export XDG_CACHE_HOME="$project_root/data/camoufox/cache"
mkdir -p "$XDG_CACHE_HOME" "$project_root/data/camoufox/profiles/gemini"

"$python_bin" -m pip install -e "$project_root[camoufox]"
"$python_bin" -m camoufox fetch official/stable/152.0.4-beta.30
"$python_bin" -m camoufox version

printf '%s\n' "Camoufox installed in the Look4GEO project runtime."
