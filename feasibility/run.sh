#!/usr/bin/env bash
set -euo pipefail
pv_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
pv_python="${PV_PYTHON:-$pv_dir/../.venv/bin/python}"
if [[ ! -x "$pv_python" ]]; then pv_python=python3; fi
cd -- "$pv_dir/.."
umask 077
export NUMBA_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
"$pv_python" -c 'import sys; assert sys.version_info[:2] == (3, 12), "CPython 3.12 required; see docs/DEPLOYMENT.md"; from feasibility.profile_store import ProfileStore; ProfileStore()'
if [[ "${1:-}" == "--demo" ]]; then
    shift
    pv_demo_dir="$("$pv_python" -c 'from feasibility.profile_store import resolve_data_root; print(resolve_data_root() / "fictional-demo")')"
    "$pv_python" tests/fixtures/public_demo/generate.py --output "$pv_demo_dir"
    printf '%s\n' 'Fictional hourly arrays generated outside the checkout. Use household setup to enter a profile; demo files are not activated automatically.'
fi
exec "$pv_python" -X faulthandler -m feasibility.server "$@"
