"""Tests for tools/privacy_check.py CLI contract."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


TOOL_PATH = Path(__file__).resolve().parent.parent / "tools" / "privacy_check.py"


def init_repo(repo_dir: Path) -> Path:
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(repo_dir)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.name", "Fixture"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo_dir), "config", "user.email", "fixture@example.invalid"],
        check=True,
        capture_output=True,
    )
    return repo_dir


def assert_secret_not_leaked(
    secret: str,
    proc: subprocess.CompletedProcess[str],
    report_path: Path | None = None,
    private_dir: Path | None = None,
) -> None:
    found_in_stdout = secret in proc.stdout
    assert not found_in_stdout, "Secret leaked in stdout"
    found_in_stderr = secret in proc.stderr
    assert not found_in_stderr, "Secret leaked in stderr"
    if report_path is not None and report_path.exists():
        content = report_path.read_text(encoding="utf-8")
        found_in_report = secret in content
        assert not found_in_report, "Secret leaked in report"
    if private_dir is not None and private_dir.exists():
        for p in private_dir.glob("*"):
            if p.is_file() and p.name != "personal-patterns.txt":
                text = p.read_text(encoding="utf-8", errors="replace")
                found_in_private_artifact = secret in text
                assert not found_in_private_artifact, "Secret leaked in private-dir artifact"


def load_audit_report(proc: subprocess.CompletedProcess[str], private_dir: Path) -> dict:
    for p in sorted(private_dir.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if "findings" in data:
                return data
        except Exception:
            continue
    stdout_str = proc.stdout.strip()
    if "{" in stdout_str and "}" in stdout_str:
        json_part = stdout_str[stdout_str.find("{") : stdout_str.rfind("}") + 1]
        try:
            return json.loads(json_part)
        except Exception:
            pass
    return json.loads(proc.stdout)


def test_audit_deleted_history(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    private_dir = tmp_path / "private"
    private_dir.mkdir(parents=True, exist_ok=True)

    secret_value = "DELETED_SECRET_" + "XYZ12345"
    (private_dir / "personal-patterns.txt").write_text(secret_value + "\n", encoding="utf-8")

    secret_file = repo / "credential.txt"
    secret_file.write_text(f"api_key={secret_value}\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "credential.txt"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "add credential"], check=True, capture_output=True)

    subprocess.run(["git", "-C", str(repo), "rm", "credential.txt"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "delete credential"], check=True, capture_output=True)

    assert not secret_file.exists()

    proc = subprocess.run(
        [sys.executable, str(TOOL_PATH), "audit", "--root", str(repo), "--private-dir", str(private_dir)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert_secret_not_leaked(secret_value, proc, private_dir=private_dir)
    report_data = load_audit_report(proc, private_dir)
    findings = report_data.get("findings", [])
    has_finding = any(
        f.get("reason") == "private-literal" and f.get("type") in ("blob", "commit")
        for f in findings
    )
    assert has_finding, "Expected private-literal finding for deleted history object"


def test_audit_dangling_blob(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    private_dir = tmp_path / "private"
    private_dir.mkdir(parents=True, exist_ok=True)

    secret_value = "DANGLING_BLOB_" + "SECRET999"
    (private_dir / "personal-patterns.txt").write_text(secret_value + "\n", encoding="utf-8")

    subprocess.run(
        ["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
        input=f"dangling_token={secret_value}\n".encode("utf-8"),
        check=True,
        capture_output=True,
    )

    proc = subprocess.run(
        [sys.executable, str(TOOL_PATH), "audit", "--root", str(repo), "--private-dir", str(private_dir)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert_secret_not_leaked(secret_value, proc, private_dir=private_dir)
    report_data = load_audit_report(proc, private_dir)
    findings = report_data.get("findings", [])
    has_finding = any(
        f.get("reason") == "private-literal" and f.get("type") == "blob"
        for f in findings
    )
    assert has_finding, "Expected private-literal finding for dangling blob"


def test_audit_author_email(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    private_dir = tmp_path / "private"
    private_dir.mkdir(parents=True, exist_ok=True)

    private_email = "secret_author_" + "person@example.invalid"
    (private_dir / "personal-patterns.txt").write_text(private_email + "\n", encoding="utf-8")

    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "commit",
            "--allow-empty",
            "-m",
            "commit with private author email",
            "--author",
            f"Secret Contributor <{private_email}>",
        ],
        check=True,
        capture_output=True,
    )

    proc = subprocess.run(
        [sys.executable, str(TOOL_PATH), "audit", "--root", str(repo), "--private-dir", str(private_dir)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert_secret_not_leaked(private_email, proc, private_dir=private_dir)
    report_data = load_audit_report(proc, private_dir)
    findings = report_data.get("findings", [])
    has_finding = any(
        f.get("reason") == "private-literal" and f.get("type") == "commit"
        for f in findings
    )
    assert has_finding, "Expected private-literal finding for commit author email"


def test_tree_binary_review_required(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    patterns_file = tmp_path / "patterns.txt"
    patterns_file.write_text("dummy_pattern_non_matching\n", encoding="utf-8")
    report_file = tmp_path / "report.json"

    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
    (repo / "sample.png").write_bytes(png_bytes)

    proc = subprocess.run(
        [
            sys.executable,
            str(TOOL_PATH),
            "tree",
            "--root",
            str(repo),
            "--patterns",
            str(patterns_file),
            "--report",
            str(report_file),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert report_file.exists()
    report_data = json.loads(report_file.read_text(encoding="utf-8"))
    findings = report_data.get("findings", [])
    has_binary_review = any(
        f.get("reason") == "manual-review-required" and "sample.png" in f.get("path", "")
        for f in findings
    )
    assert has_binary_review, "Expected manual-review-required finding for binary file"


def test_staged_scans_staged_blob_with_unstaged_clean(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    patterns_file = tmp_path / "patterns.txt"
    report_file = tmp_path / "report.json"

    secret_value = "STAGED_PRIVATE_" + "TOKEN456"
    patterns_file.write_text(secret_value + "\n", encoding="utf-8")

    target_file = repo / "config.txt"
    target_file.write_text(f"token={secret_value}\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "config.txt"], check=True, capture_output=True)

    target_file.write_text("token=clean_and_safe_value\n", encoding="utf-8")

    proc = subprocess.run(
        [
            sys.executable,
            str(TOOL_PATH),
            "staged",
            "--root",
            str(repo),
            "--patterns",
            str(patterns_file),
            "--report",
            str(report_file),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert_secret_not_leaked(secret_value, proc, report_path=report_file)
    assert report_file.exists()
    report_data = json.loads(report_file.read_text(encoding="utf-8"))
    findings = report_data.get("findings", [])
    has_finding = any(
        f.get("reason") == "private-literal" and "config.txt" in f.get("path", "")
        for f in findings
    )
    assert has_finding, "Expected private-literal finding for staged blob"


def test_tree_includes_ignored_files(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    patterns_file = tmp_path / "patterns.txt"
    report_file = tmp_path / "report.json"

    secret_value = "IGNORED_PRIVATE_" + "VAR789"
    patterns_file.write_text(secret_value + "\n", encoding="utf-8")

    (repo / ".gitignore").write_text("secrets.env\n", encoding="utf-8")
    (repo / "secrets.env").write_text(f"SECRET_VAR={secret_value}\n", encoding="utf-8")

    proc = subprocess.run(
        [
            sys.executable,
            str(TOOL_PATH),
            "tree",
            "--root",
            str(repo),
            "--patterns",
            str(patterns_file),
            "--report",
            str(report_file),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert_secret_not_leaked(secret_value, proc, report_path=report_file)
    assert report_file.exists()
    report_data = json.loads(report_file.read_text(encoding="utf-8"))
    findings = report_data.get("findings", [])
    has_finding = any(
        f.get("reason") == "private-literal" and "secrets.env" in f.get("path", "")
        for f in findings
    )
    assert has_finding, "Expected private-literal finding for ignored file"


def test_generic_fake_key(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    patterns_file = tmp_path / "patterns.txt"
    patterns_file.write_text("non_matching_dummy_pattern_only\n", encoding="utf-8")
    report_file = tmp_path / "report.json"

    clean_proc = subprocess.run(
        [
            sys.executable,
            str(TOOL_PATH),
            "tree",
            "--root",
            str(repo),
            "--patterns",
            str(patterns_file),
            "--report",
            str(report_file),
        ],
        capture_output=True,
        text=True,
    )
    assert clean_proc.returncode == 0

    fake_key = "AKIA" + "0123456789ABCDEF"
    (repo / "aws_creds.env").write_text(f"AWS_ACCESS_KEY_ID={fake_key}\n", encoding="utf-8")

    proc = subprocess.run(
        [
            sys.executable,
            str(TOOL_PATH),
            "tree",
            "--root",
            str(repo),
            "--patterns",
            str(patterns_file),
            "--report",
            str(report_file),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert_secret_not_leaked(fake_key, proc, report_path=report_file)
    assert report_file.exists()
    report_data = json.loads(report_file.read_text(encoding="utf-8"))
    findings = report_data.get("findings", [])
    has_credential_candidate = any(
        f.get("reason") == "credential-candidate" for f in findings
    )
    assert has_credential_candidate, "Expected credential-candidate finding"


def test_oversized_file_finding(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    patterns_file = tmp_path / "patterns.txt"
    patterns_file.write_text("non_matching_dummy_pattern\n", encoding="utf-8")
    report_file = tmp_path / "report.json"

    oversized_file = repo / "large_payload.bin"
    oversized_file.write_bytes(b"A" * (1024 * 1024 + 1024))

    proc = subprocess.run(
        [
            sys.executable,
            str(TOOL_PATH),
            "tree",
            "--root",
            str(repo),
            "--patterns",
            str(patterns_file),
            "--report",
            str(report_file),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert report_file.exists()
    report_data = json.loads(report_file.read_text(encoding="utf-8"))
    findings = report_data.get("findings", [])
    has_oversized_finding = any(f.get("reason") == "large-artifact" and "large_payload.bin" in f.get("path", "") for f in findings)
    assert has_oversized_finding, "Expected finding for oversized file"


def test_precise_coordinate_and_personal_path_are_not_fixture_exemptions():
    """An exact synthetic-asset record cannot waive private content findings."""
    import hashlib
    import importlib.util
    spec=importlib.util.spec_from_file_location('privacy_scanner_test',TOOL_PATH)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    coordinate='45.'+'123456'
    private_path='/'+'home'+'/fictional-person/household.csv'
    raw=json.dumps(dict(latitude=float(coordinate),path=private_path)).encode()
    asset=dict(path='fixture.json',sha256=hashlib.sha256(raw).hexdigest(),synthetic=True,
               reason='Planted synthetic asset for negative test')
    scanner=module.Scanner([coordinate],allowlist=[asset])
    scanner.scan(raw,'fixture.json')
    reasons={f['reason'] for f in scanner.findings}
    assert {'private-literal','precise-coordinate','personal-filesystem-path'} <= reasons
    assert coordinate not in json.dumps(scanner.findings)
    assert private_path not in json.dumps(scanner.findings)
    changed=raw.replace(coordinate.encode(),('46.'+'654321').encode())
    second=module.Scanner([],allowlist=[asset]);second.scan(changed,'fixture.json')
    assert 'precise-coordinate' in {f['reason'] for f in second.findings}


def test_approved_copyright_is_exact_file_and_line_only():
    import hashlib
    import importlib.util
    spec=importlib.util.spec_from_file_location('public_notice_scan',TOOL_PATH)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    line=b'Required Notice: Copyright 2026 Fictional Holder'
    raw=line+b'\nOther notice text.\n'
    asset=dict(path='NOTICE',sha256=hashlib.sha256(raw).hexdigest(),public_metadata=True,
               line_sha256=[hashlib.sha256(line).hexdigest()],reason='Synthetic approved public holder')
    def reasons(data,path='NOTICE',records=None):
        scanner=module.Scanner(['Fictional Holder','private-household'],records or [asset])
        scanner.scan(data,path)
        return {f['reason'] for f in scanner.findings}
    assert not reasons(raw)
    assert 'private-literal' in reasons(raw,'elsewhere.txt')
    assert 'private-literal' in reasons(raw+b'private-household\n')
    poisoned=raw+b'private-household\n'
    changed={**asset,'sha256':hashlib.sha256(poisoned).hexdigest()}
    assert 'private-literal' in reasons(poisoned,records=[changed])
    dangerous=raw+b'password='+b'A'*24+b'\n'
    changed={**asset,'sha256':hashlib.sha256(dangerous).hexdigest()}
    assert 'credential-candidate' in reasons(dangerous,records=[changed])


def test_approved_git_identity_applies_to_headers_only():
    import hashlib
    import importlib.util
    spec=importlib.util.spec_from_file_location('public_identity_scan',TOOL_PATH)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    identity=b'Fictional Author <fictional@example.invalid>'
    asset=dict(public_git_identity_sha256=hashlib.sha256(identity).hexdigest(),reason='Synthetic approved identity')
    raw=b'tree '+b'a'*40+b'\nauthor '+identity+b' 123456 +0000\ncommitter '+identity+b' 123456 +0000\n\ninitial\n'
    def reasons(data,kind='commit'):
        scanner=module.Scanner(['Fictional Author','private-household'],[asset]);scanner.scan(data,'object/test',kind)
        return {f['reason'] for f in scanner.findings}
    assert not reasons(raw)
    assert 'private-literal' in reasons(raw,'blob')
    assert 'private-literal' in reasons(raw+b'Fictional Author\n')
    assert 'private-literal' in reasons(raw.replace(b'fictional@',b'changed@'))
    assert 'private-literal' in reasons(raw+b'private-household\n')


def test_staged_public_metadata_uses_index_policy_not_unstaged_approval(tmp_path):
    import hashlib
    repo=init_repo(tmp_path/'repo');(repo/'tools').mkdir()
    line=b'Required Notice: Copyright 2026 Fictional Holder';raw=line+b'\n'
    (repo/'NOTICE').write_bytes(raw)
    policy=repo/'tools/export_allowlist.json';policy.write_text(json.dumps(dict(assets=[])))
    subprocess.run(['git','-C',str(repo),'add','NOTICE','tools/export_allowlist.json'],check=True)
    record=dict(path='NOTICE',sha256=hashlib.sha256(raw).hexdigest(),public_metadata=True,
                line_sha256=[hashlib.sha256(line).hexdigest()],reason='Synthetic approved notice')
    policy.write_text(json.dumps(dict(assets=[record])))
    patterns=tmp_path/'patterns.txt';patterns.write_text('Fictional Holder\n')
    argv=[sys.executable,str(TOOL_PATH),'staged','--root',str(repo),'--patterns',str(patterns),'--report',str(tmp_path/'report.json')]
    assert subprocess.run(argv,capture_output=True).returncode==1
    subprocess.run(['git','-C',str(repo),'add','tools/export_allowlist.json'],check=True)
    assert subprocess.run(argv,capture_output=True).returncode==0
