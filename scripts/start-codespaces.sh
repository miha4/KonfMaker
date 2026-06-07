#!/usr/bin/env bash
set -euo pipefail

# Backwards-compatible wrapper. The generic dev script works both locally and in Codespaces.
"$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/start-dev.sh"
