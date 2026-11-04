import hashlib
import importlib.util, json, os, stat, subprocess
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools" / "export_public.py"
spec = importlib.util.spec_from_file_location("export_public", MODULE_PATH)
export_public = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export_public)
export_committed, relative_path = export_public.export_committed, export_public.relative_path


def make_repo(root: Path, allowlist_files: list[str], assets=None, extra_files=None) -> Path:
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    for k, v in [("user.name", "Fixture"), ("user.email", "fixture@example.invalid")]:
        subprocess.run(["git", "-C", str(root), "config", k, v], check=True, capture_output=True)
    (root / "tools").mkdir(parents=True, exist_ok=True)
    (root / "tools" / "export_allowlist.json").write_text(
        json.dumps({"schema_version": 1, "files": allowlist_files, "assets": assets or []}) + "\n"
    )
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text("intro\n")
    (root / "src" / "app.py").write_text("print('v1')\n")
    (root / "run.sh").write_text("#!/bin/sh\nexit 0\n")
    (root / "run.sh").chmod(0o755)
    for name, raw in (extra_files or {}).items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "update-index", "--chmod=+x", "run.sh"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "init"], check=True, capture_output=True)
    return root


def test_export_committed_bytes_modes_manifests(tmp_path):
    root = make_repo(tmp_path / "repo", ["README.md", "src/app.py", "run.sh"])
    (root / "src" / "app.py").write_text("print('working-uncommitted')\n")
    (root / "extra.txt").write_text("unreviewed\n")
    subprocess.run(["git", "-C", str(root), "add", "extra.txt"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "unreviewed"], check=True, capture_output=True)
    (root / "untracked.txt").write_text("untracked")
    dest = tmp_path / "dest"
    manifest = export_committed(root, dest)
    assert (dest / "src" / "app.py").read_text() == "print('v1')\n"
    assert not (dest / "extra.txt").exists()
    if os.name != 'nt':
        assert stat.S_IMODE((dest / "run.sh").stat().st_mode) == 0o755
    assert (dest / "PUBLIC_EXPORT_MANIFEST.json").is_file()
    assert (dest / "SOFTWARE_MANIFEST.json").is_file()
    software = json.loads((dest / "SOFTWARE_MANIFEST.json").read_text())
    assert software["runtime"] == "CPython 3.12"
    assert set(software["platforms"]) == {"Linux", "Windows x64", "macOS Apple silicon"}
    assert "native macOS validation pending" in software["platform_validation"]
    assert manifest["reviewed_source_sha256"]
    assert not (dest / "untracked.txt").exists()
    assert str(root) not in json.dumps(manifest)
    for entry in manifest["files"]:
        assert entry["sha256"] == hashlib.sha256((dest / entry["path"]).read_bytes()).hexdigest()
    assert {x["path"] for x in manifest["files"]} == {"README.md", "src/app.py", "run.sh", "SOFTWARE_MANIFEST.json"}


@pytest.mark.parametrize("bad", ["../out.py", "/etc/app.py", "src/*.py", "private/sec.py", ".git/k", ".env", "AGENTS.md", ".env.local", "docs/public-plan/plan.md", "raw.npy", "src//x.py"])
def test_relative_path_rejections(bad):
    with pytest.raises(ValueError, match="Disallowed export path"):
        relative_path(bad)


def test_rejections_destination_and_symlinks(tmp_path):
    root = make_repo(tmp_path / "repo", ["README.md", "src/app.py", "run.sh"])
    nonempty = tmp_path / "nonempty"
    nonempty.mkdir()
    (nonempty / "x.txt").write_text("data")
    with pytest.raises(ValueError, match="Destination must be new or empty"):
        export_committed(root, nonempty)
    sym_dest = tmp_path / "sym_dest"
    sym_dest.symlink_to(nonempty, target_is_directory=True)
    with pytest.raises(ValueError, match="Destination symlink rejected"):
        export_committed(root, sym_dest)
    sym_src = root / "src" / "sym.py"
    sym_src.symlink_to(root / "README.md")
    subprocess.run(["git", "-C", str(root), "config", "core.symlinks", "true"], check=True)
    (root / "tools" / "export_allowlist.json").write_text(
        json.dumps({"schema_version": 1, "files": ["README.md", "src/sym.py"], "assets": []}) + "\n"
    )
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "sym"], check=True, capture_output=True)
    with pytest.raises(ValueError, match="Source (?:must be a regular file|symlink)"):
        export_committed(root, tmp_path / "dest_sym")


def test_rejections_missing_uncommitted_and_private_content(tmp_path):
    root = make_repo(tmp_path / "repo", ["README.md", "src/app.py", "run.sh"])
    (root / "README.md").unlink()
    with pytest.raises(ValueError, match="Source must be a regular file"):
        export_committed(root, tmp_path / "d_missing")
    (root / "README.md").write_text("intro\n")
    (root / "tools" / "export_allowlist.json").write_text(
        json.dumps({"schema_version": 1, "files": ["README.md", "src/new.py"], "assets": []}) + "\n"
    )
    subprocess.run(["git", "-C", str(root), "add", "tools/export_allowlist.json"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "policy"], check=True, capture_output=True)
    (root / "src" / "new.py").write_text("new\n")
    with pytest.raises(ValueError, match="Uncommitted or missing reviewed source"):
        export_committed(root, tmp_path / "d_uncommitted")
    needle = "disallowed" + "_" + "secret" + "_" + "marker"
    root2 = make_repo(tmp_path / "repo2", ["README.md", "src/app.py", "run.sh"])
    (root2 / "src" / "app.py").write_text(f"val = '{needle}'\n")
    subprocess.run(["git", "-C", str(root2), "commit", "-am", "marker"], check=True, capture_output=True)
    with pytest.raises(ValueError, match="Export privacy/secret review failed"):
        export_committed(root2, tmp_path / "d_priv", patterns=[needle])


SYNTHETIC_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00"
    b"\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_export_approved_screenshot_success(tmp_path):
    shot = "screenshots/01-hero-verdict.png"
    digest = hashlib.sha256(SYNTHETIC_PNG).hexdigest()
    asset = {
        "path": shot,
        "sha256": digest,
        "synthetic": True,
        "public_screenshot": True,
        "reason": "approved synthetic test screenshot",
    }
    root = make_repo(tmp_path / "repo", ["README.md", shot], assets=[asset], extra_files={shot: SYNTHETIC_PNG})
    dest = tmp_path / "dest"
    manifest = export_committed(root, dest)
    assert (dest / shot).read_bytes() == SYNTHETIC_PNG
    assert any(x["path"] == shot and x["sha256"] == digest for x in manifest["files"])


@pytest.mark.parametrize(
    "asset_override",
    [
        None,  # missing asset approval
        {"sha256": "0" * 64},  # changed hash
        {"public_screenshot": False},  # false public_screenshot flag
        {"synthetic": False},  # false synthetic flag
    ],
)
def test_export_screenshot_approval_rejections(tmp_path, asset_override):
    shot = "screenshots/01-hero-verdict.png"
    digest = hashlib.sha256(SYNTHETIC_PNG).hexdigest()
    if asset_override is None:
        assets = []
    else:
        asset = {"path": shot, "sha256": digest, "synthetic": True, "public_screenshot": True, "reason": "approved"}
        asset.update(asset_override)
        assets = [asset]
    root = make_repo(tmp_path / "repo", ["README.md", shot], assets=assets, extra_files={shot: SYNTHETIC_PNG})
    with pytest.raises(ValueError, match="Export privacy/secret review failed"):
        export_committed(root, tmp_path / "dest")


def test_export_rejects_unapproved_screenshot_in_files(tmp_path):
    approved = "screenshots/01-hero-verdict.png"
    unapproved = "screenshots/unapproved.png"
    digest = hashlib.sha256(SYNTHETIC_PNG).hexdigest()
    asset = {"path": approved, "sha256": digest, "synthetic": True, "public_screenshot": True, "reason": "approved"}
    root = make_repo(
        tmp_path / "repo",
        ["README.md", approved, unapproved],
        assets=[asset],
        extra_files={approved: SYNTHETIC_PNG, unapproved: SYNTHETIC_PNG},
    )
    with pytest.raises(ValueError, match="Export privacy/secret review failed"):
        export_committed(root, tmp_path / "dest")


@pytest.mark.parametrize(
    "bad_path",
    [
        "screenshots/nested/shot.png",
        "screenshots/uploads/shot.png",
        "screenshots/shot.gif",
        "screenshots/shot.txt",
    ],
)
def test_relative_path_disallows_nested_or_non_image_screenshots(bad_path):
    with pytest.raises(ValueError, match="Disallowed export path"):
        relative_path(bad_path)


def test_relative_path_allows_direct_screenshots():
    assert relative_path("screenshots/01-hero-verdict.png") == "screenshots/01-hero-verdict.png"
    assert relative_path("screenshots/readme-example.jpg") == "screenshots/readme-example.jpg"


def test_export_rejects_nested_screenshot_path(tmp_path):
    nested = "screenshots/uploads/shot.png"
    digest = hashlib.sha256(SYNTHETIC_PNG).hexdigest()
    asset = {"path": nested, "sha256": digest, "synthetic": True, "public_screenshot": True, "reason": "approved"}
    root = make_repo(tmp_path / "repo", ["README.md", nested], assets=[asset], extra_files={nested: SYNTHETIC_PNG})
    with pytest.raises(ValueError, match="Disallowed export path"):
        export_committed(root, tmp_path / "dest")
