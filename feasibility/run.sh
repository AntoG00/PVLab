#!/usr/bin/env bash
set -euo pipefail
pv_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
pv_python="${PV_PYTHON:-$pv_dir/../.venv/bin/python}"
pv_platform="$(uname -s)"
if [[ ! -x "$pv_python" ]]; then
    pv_python=python3
    if [[ "$pv_platform" == Darwin ]]; then
        if command -v python3.12 >/dev/null 2>&1; then
            pv_python=python3.12
        elif [[ -x /opt/homebrew/bin/python3.12 ]]; then
            pv_python=/opt/homebrew/bin/python3.12
        elif [[ -x /usr/local/bin/python3.12 ]]; then
            pv_python=/usr/local/bin/python3.12
        fi
    fi
fi
cd -- "$pv_dir/.."
umask 077
export NUMBA_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
if [[ "$pv_platform" == Darwin ]]; then
    export VECLIB_MAXIMUM_THREADS=1
fi
"$pv_python" -c 'import sys; assert sys.version_info[:2] == (3, 12), "CPython 3.12 required; see docs/DEPLOYMENT.md"; from feasibility.profile_store import ProfileStore; ProfileStore()'
if [[ "${1:-}" == "--demo" ]]; then
    shift
    pv_demo_dir="$("$pv_python" -c 'from feasibility.profile_store import resolve_data_root; print(resolve_data_root() / "fictional-demo")')"
    "$pv_python" tests/fixtures/public_demo/generate.py --output "$pv_demo_dir"
    printf '%s\n' 'Fictional hourly arrays generated outside the checkout. Use household setup to enter a profile; demo files are not activated automatically.'
fi
exec "$pv_python" -X faulthandler -m feasibility.server "$@"
