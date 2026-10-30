"""Check native-library structure before handing it to the system loader."""
import importlib.util
import struct
import sys
from pathlib import Path


def check_native_runtime():
    """Bounded ELF reads catch incomplete/corrupt llvmlite copies on Linux.

    Loading a damaged ELF with ctypes can terminate Python with SIGSEGV,
    before an import exception can be raised. This does not load LLVM or
    change compilation settings, dispatch, or any numerical inputs.
    """
    if not sys.platform.startswith('linux'):
        return
    spec = importlib.util.find_spec('llvmlite')
    if spec is None or spec.origin is None:
        return  # Let the normal import explain a missing dependency.
    library = Path(spec.origin).parent / 'binding/libllvmlite.so'
    try:
        size = library.stat().st_size
        with library.open('rb') as stream:
            header = stream.read(64)
            if len(header) != 64 or header[:6] != b'\x7fELF\x02\x01':
                raise ValueError('invalid ELF64 header')
            phoff, shoff = struct.unpack_from('<QQ', header, 32)
            phsize, phcount, shsize, shcount = struct.unpack_from('<HHHH', header, 54)
            if phsize != 56 or not 0 < phcount <= 128 or phoff + phsize * phcount > size:
                raise ValueError('invalid program table')
            if shoff and (shsize != 64 or shoff + shsize * shcount > size):
                raise ValueError('incomplete section table')
            stream.seek(phoff)
            programs = stream.read(phsize * phcount)
            for offset in range(0, len(programs), phsize):
                kind, _, file_offset, _, _, file_size, _, _ = struct.unpack_from('<IIQQQQQQ', programs, offset)
                if kind != 2:  # PT_DYNAMIC
                    continue
                if not 0 < file_size <= 65536 or file_size % 16 or file_offset + file_size > size:
                    raise ValueError('invalid dynamic segment')
                stream.seek(file_offset)
                tags = {}
                for tag, value in struct.iter_unpack('<qQ', stream.read(file_size)):
                    if tag == 0:
                        break
                    tags[tag] = value
                if not all(tags.get(tag) for tag in (5, 6, 10, 11)):
                    raise ValueError('missing dynamic string/symbol tables')
                return
            raise ValueError('missing dynamic segment')
    except (OSError, ValueError, struct.error) as exc:
        raise RuntimeError(
            f'Native dependency damaged or incomplete: {library} ({exc}). '
            'Restore llvmlite using the version in its installed .dist-info/METADATA; '
            'the server has stopped before loading the damaged library.'
        ) from exc
