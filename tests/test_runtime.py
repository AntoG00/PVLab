"""A corrupt native library must fail before ctypes/system-loader access."""
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from feasibility.runtime import check_native_runtime


class RuntimeTests(unittest.TestCase):
    def test_zeroed_dynamic_segment_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'binding').mkdir()
            header = bytearray(64)
            header[:6] = b'\x7fELF\x02\x01'
            struct.pack_into('<Q', header, 32, 64)
            struct.pack_into('<HH', header, 54, 56, 1)
            program = struct.pack('<IIQQQQQQ', 2, 6, 120, 120, 120, 16, 16, 8)
            (root / 'binding/libllvmlite.so').write_bytes(header + program + bytes(16))
            spec = SimpleNamespace(origin=str(root / '__init__.py'))
            with patch('feasibility.runtime.sys.platform', 'linux'), patch(
                'feasibility.runtime.importlib.util.find_spec', return_value=spec
            ), self.assertRaisesRegex(RuntimeError, 'Native dependency damaged.*missing dynamic'):
                check_native_runtime()
