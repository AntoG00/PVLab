import hashlib
import importlib.util, json, stat, subprocess
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools" / "export_public.py"
spec = importlib.util.spec_from_file_location("export_public", MODULE_PATH)
export_public = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export_public)
export_committed, relative_path = export_public.export_committed, export_public.relative_path


def make_repo(root: Path, allowlist_files: list[str]) -> Path:
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    for k, v in [("user.name", "Fixture"), ("user.email", "fixture@example.invalid")]:
        subprocess.run(["git", "-C", str(root), "config", k, v], check=True, capture_output=True)
    (root / "tools").mkdir(parents=True, exist_ok=True)
    (root / "tools" / "export_allowlist.json").write_text(
        json.dumps({"schema_version": 1, "files": allowlist_files, "assets": []}) + "\n"
    )
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text("intro\n")
    (root / "src" / "app.py").write_text("print('v1')\n")
    (root / "run.sh").write_text("#!/bin/sh\nexit 0\n")
    (root / "run.sh").chmod(0o755)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True)
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
    assert oct(stat.S_IMODE((dest / "run.sh").stat().st_mode)) == oct(0o755)
    assert (dest / "PUBLIC_EXPORT_MANIFEST.json").is_file()
    assert (dest / "SOFTWARE_MANIFEST.json").is_file()
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
    sym_dest.symlink_to(nonempty)
    with pytest.raises(ValueError, match="Destination symlink rejected"):
        export_committed(root, sym_dest)
    sym_src = root / "src" / "sym.py"
    sym_src.symlink_to(root / "README.md")
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
