#!/usr/bin/env bash
# deploy.sh - put rungs on the PATH (or vendor it) and initialise a project.
#
#   ~/rungs/deploy.sh --into /path/to/project [--agent id:skill[:role]]... [--import-arbite [PATH]]
#   ~/rungs/deploy.sh --into /path/to/project --vendor
#
# A thin wrapper: every option is passed to `bin/rungs deploy`, which holds
# the logic. Run `~/rungs/deploy.sh --help` for the options.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! command -v python3 >/dev/null 2>&1; then
  echo "deploy.sh: python3 (3.9 or newer) is required" >&2
  exit 1
fi
exec python3 "$HERE/bin/rungs" deploy "$@"
