import hashlib, json, pytest, numpy as np
from tools.regression import Difference, compare_outputs, validate_manifest


def test_identical():
    data = {"a": 1, "b": [2], "c": np.array([1, 2], dtype=np.int32), "s": "ok", "t": True}
    assert compare_outputs(data, data.copy()) == []
    assert compare_outputs(42, 42) == []


def test_streaming_saved_comparison_preserves_types_bytes_and_ignored_fields(tmp_path):
    from tools.regression import write_outputs, compare_saved_outputs
    cases = [(1, 1), (1, 1.0), (True, 1), (0.0, -0.0), ([1], (1,)),
             ({'x': 1}, {'x': 2}), ({'x': 1}, {'y': 1}),
             (np.array([1], dtype='i4'), np.array([1], dtype='i8')),
             (np.array([0.0]), np.array([-0.0])),
             (np.ones((2, 3), order='C'), np.ones((2, 3), order='F')),
             (np.float32(1), np.float64(1)),
             ({'x': 'a'*70000}, {'x': 'a'*70000}),
             ({'compute_seconds': np.array([1]), 'data': 2}, {'compute_seconds': 'other', 'data': 2}),
             ({'nested': {'compute_seconds': 1}}, {'nested': {'compute_seconds': 2}})]
    for index, (left, right) in enumerate(cases):
        a, b = tmp_path / f'left-{index}', tmp_path / f'right-{index}'
        write_outputs(left, a)
        write_outputs(right, b)
        assert bool(compare_saved_outputs(a, b)) == bool(compare_outputs(left, right))


def test_streaming_saved_comparison_checks_all_chunks_and_array_references(tmp_path):
    from tools.regression import write_outputs, compare_saved_outputs
    left = {'a': np.arange(100000, dtype=np.float64), 'b': ('tail', np.int32(9))}
    right = {'a': left['a'].copy(), 'b': left['b']}
    right['a'][-1] += 1
    write_outputs(left, tmp_path / 'a')
    write_outputs(right, tmp_path / 'b')
    differences = compare_saved_outputs(tmp_path / 'a', tmp_path / 'b')
    assert [(d.path, d.reason) for d in differences] == [('/a', 'array values changed')]
    np.savez_compressed(tmp_path / 'b' / 'arrays.npz', a000000=right['a'], extra=np.zeros(1))
    with pytest.raises(ValueError, match='Unreferenced'):
        compare_saved_outputs(tmp_path / 'a', tmp_path / 'b')


@pytest.mark.parametrize("exp,act", [
    (np.array([1, 2]), np.array([1, 99])),
    ([1, 2], [1, 99]),
    (10, 20),
    (np.array([[1, 2], [3, 4]]), np.array([1, 2, 3, 4])),
    (np.array([[1, 2], [3, 4]], order="C"), np.array([[1, 2], [3, 4]], order="F")),
    (np.array([1, 2], dtype=np.int32), np.array([1, 2], dtype=np.int64)),
    (np.array([1.0], dtype=np.float32), np.array([1.0], dtype=np.float64)),
    ({"a": 1, "b": 2}, {"a": 1}),
    ({"a": 1}, {"a": 1, "b": 2}),
    (True, 1),
    (False, 0),
    (1, 1.0),
    ([1, 2, 3], [2, 1, 3]),
    ({"ts": "2026-01-01"}, {"ts": "2026-01-02"}),
    ({"sub": {"generated_at_utc": "t1"}}, {"sub": {"generated_at_utc": "t2"}}),
])
def test_comparator_mismatches(exp, act):
    diffs = compare_outputs(exp, act)
    assert len(diffs) > 0 and all(isinstance(d, Difference) for d in diffs)


@pytest.mark.parametrize("cs1,cs2,gt1,gt2", [
    (1.23, 9.87, "2026-01-01T00:00:00Z", "2026-10-27T12:00:00Z"),
    (None, "fast", 12345, None),
])
def test_permitted_metadata(cs1, cs2, gt1, gt2):
    e = {"compute_seconds": cs1, "generated_at_utc": gt1, "data": [1, 2]}
    a = {"compute_seconds": cs2, "generated_at_utc": gt2, "data": [1, 2]}
    assert compare_outputs(e, a) == []


def test_validate_manifest_valid(tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes(b"data")
    m = {"schema_version": 1, "files": [{"path": "a.txt", "sha256": hashlib.sha256(b"data").hexdigest()}]}
    p = tmp_path / "m.json"
    p.write_text(json.dumps(m))
    validate_manifest(p)


@pytest.mark.parametrize("corrupt", ["missing", "hash", "symlink", "escape", "duplicate"])
def test_validate_manifest_errors(tmp_path, corrupt):
    f = tmp_path / "f.txt"
    f.write_bytes(b"content")
    h = hashlib.sha256(b"content").hexdigest()
    if corrupt == "missing":
        files = [{"path": "ghost.txt", "sha256": h}]
    elif corrupt == "hash":
        files = [{"path": "f.txt", "sha256": "0" * 64}]
    elif corrupt == "symlink":
        link = tmp_path / "link.txt"
        link.symlink_to(f)
        files = [{"path": "link.txt", "sha256": h}]
    elif corrupt == "escape":
        files = [{"path": "../outside.txt", "sha256": h}]
    elif corrupt == "duplicate":
        files = [{"path": "f.txt", "sha256": h}, {"path": "f.txt", "sha256": h}]
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"schema_version": 1, "files": files}))
    with pytest.raises(ValueError):
        validate_manifest(p)


def test_typed_output_roundtrip(tmp_path):
    from tools.regression import write_outputs, read_outputs
    payload = {'arrays': [np.array([[1., -0.], [float('nan'), 4.]], order=order)
                          for order in ('C', 'F')],
               'tuple': (None, True, 1, 1., np.float32(2.)), 'inf': float('inf')}
    output = tmp_path / 'capture'
    write_outputs(payload, output)
    assert compare_outputs(payload, read_outputs(output)) == []
    with pytest.raises(FileExistsError):
        write_outputs(payload, output)


def test_absent_input_cannot_appear(tmp_path):
    p = tmp_path / 'manifest.json'
    p.write_text(json.dumps({'schema_version': 1, 'files': [{'path': 'optional', 'absent': True}]}))
    validate_manifest(p)
    (tmp_path / 'optional').write_text('new source')
    with pytest.raises(ValueError):
        validate_manifest(p)


def test_signed_zero_and_numpy_scalar_change():
    assert compare_outputs(0.0, -0.0)
    assert compare_outputs(np.float32(1), np.float32(2))
    assert compare_outputs(np.array([0.0]), np.array([-0.0]))

def test_model_records(tmp_path):
    from dataclasses import dataclass
    from tools.regression import model_records, write_outputs, read_outputs

    @dataclass
    class Item:
        arr: np.ndarray
        val: float

    rec = model_records(Item(np.array([1.0, 2.0]), 3.0))
    assert rec['__record_type__'] == f'{Item.__module__}.{Item.__qualname__}'
    assert rec['__record_fields__']['val'] == 3.0
    out = tmp_path / 'out'
    write_outputs(rec, out)
    assert compare_outputs(rec, read_outputs(out)) == []
    pert = model_records(Item(np.array([1.0, 9.0]), 3.0))
    assert compare_outputs(rec, pert)
    assert compare_outputs(rec, dict(rec, __record_type__='other.Item'))
