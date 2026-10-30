import os
import re
from pathlib import Path

# Select a private cache before test_support imports the numerical engine.
cache_root = os.environ.get('PV_TEST_NUMBA_CACHE_ROOT')
if cache_root:
    worker = os.environ.get('PYTEST_XDIST_WORKER', 'controller')
    if not re.fullmatch(r'(?:controller|gw[0-9]+)', worker):
        raise RuntimeError('Invalid test worker identifier')
    cache_dir = Path(cache_root) / worker
    cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.environ['NUMBA_CACHE_DIR'] = str(cache_dir)

from tests import test_support

test_support.initialize_test_runtime()
test_support.install_live_path_guard()
