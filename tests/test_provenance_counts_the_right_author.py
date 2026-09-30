import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.provenance import lines_by, _is_binary  # noqa: E402


def _git(cwd, *args, author):
    env = {"GIT_AUTHOR_NAME": author, "GIT_AUTHOR_EMAIL": f"{author}@x",
           "GIT_COMMITTER_NAME": author, "GIT_COMMITTER_EMAIL": f"{author}@x",
           "PATH": "/usr/bin:/bin", "HOME": str(cwd)}
    subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True)


def test_only_the_named_authors_non_blank_lines_count(tmp_path):
    _git(tmp_path, "init", "-q", author="a")
    (tmp_path / "f.py").write_text("x = 1\n\ny = 2\n")
    _git(tmp_path, "add", ".", author="Fatih")
    _git(tmp_path, "commit", "-qm", "one", author="FatihMakes")
    (tmp_path / "f.py").write_text("x = 1\n\ny = 3\nz = 4\n")
    _git(tmp_path, "commit", "-qam", "two", author="someone")
    assert lines_by("f.py", cwd=tmp_path) == 1
    assert lines_by("f.py", author="someone", cwd=tmp_path) == 2


def test_binary_files_return_zero(tmp_path):
    _git(tmp_path, "init", "-q", author="a")
    (tmp_path / "binary.bin").write_bytes(b"\x00\x01\x02\n\x00\xff\n")
    _git(tmp_path, "add", ".", author="Fatih")
    _git(tmp_path, "commit", "-qm", "binary", author="FatihMakes")
    assert lines_by("binary.bin", cwd=tmp_path) == 0


def test_missing_path_raises(tmp_path):
    _git(tmp_path, "init", "-q", author="a")
    (tmp_path / "f.py").write_text("x = 1\n")
    _git(tmp_path, "add", ".", author="Fatih")
    _git(tmp_path, "commit", "-qm", "init", author="FatihMakes")
    try:
        lines_by("does/not/exist.py", cwd=tmp_path)
        assert False, "should have raised"
    except RuntimeError:
        pass


def test_is_binary_detection(tmp_path):
    _git(tmp_path, "init", "-q", author="a")
    (tmp_path / "text.py").write_text("x = 1\n")
    (tmp_path / "binary.bin").write_bytes(b"\x00\x01\x02\n\x00\xff\n")
    _git(tmp_path, "add", ".", author="Fatih")
    _git(tmp_path, "commit", "-qm", "init", author="FatihMakes")
    assert _is_binary("binary.bin", cwd=tmp_path) is True
    assert _is_binary("text.py", cwd=tmp_path) is False


def test_latin1_text_file_counted(tmp_path):
    _git(tmp_path, "init", "-q", author="a")
    (tmp_path / "cafe.py").write_bytes(b"caf\xe9 = 1\n")
    _git(tmp_path, "add", ".", author="Fatih")
    _git(tmp_path, "commit", "-qm", "init", author="FatihMakes")
    assert lines_by("cafe.py", cwd=tmp_path) == 1
