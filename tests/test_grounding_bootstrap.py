from pathlib import Path

from actions.grounding import bootstrap


class FakeLinker:
    def __init__(self):
        self.links = []

    def __call__(self, src, dst):
        self.links.append((str(src), str(dst)))


def test_find_system_package_returns_first_existing_candidate(tmp_path):
    missing = tmp_path / "nope" / "gi"
    present = tmp_path / "yes" / "gi"
    present.mkdir(parents=True)
    found = bootstrap.find_system_package("gi", candidates=[missing, present])
    assert found == present


def test_find_system_package_returns_none_when_absent(tmp_path):
    assert bootstrap.find_system_package(
        "gi", candidates=[tmp_path / "a", tmp_path / "b"]) is None


def test_link_refuses_when_target_already_exists(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    site = tmp_path / "site"
    site.mkdir()
    (site / "gi").mkdir()          # already a real directory
    linker = FakeLinker()
    assert bootstrap.link_into(src, site, "gi", symlink=linker) is False
    assert linker.links == []


def test_link_creates_the_symlink(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    site = tmp_path / "site"
    site.mkdir()
    linker = FakeLinker()
    assert bootstrap.link_into(src, site, "gi", symlink=linker) is True
    assert linker.links == [(str(src), str(site / "gi"))]


def test_link_never_raises_when_symlink_fails(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    site = tmp_path / "site"
    site.mkdir()

    def boom(a, b):
        raise OSError("read-only filesystem")

    assert bootstrap.link_into(src, site, "gi", symlink=boom) is False


def test_abi_matches_only_when_extension_suffix_agrees(tmp_path):
    pkg = tmp_path / "gi"
    pkg.mkdir()
    (pkg / "_gi.cpython-312-x86_64-linux-gnu.so").touch()
    assert bootstrap.abi_matches(pkg, ".cpython-312-x86_64-linux-gnu.so") is True
    assert bootstrap.abi_matches(pkg, ".cpython-39-x86_64-linux-gnu.so") is False


def test_abi_matches_is_permissive_when_no_extension_modules(tmp_path):
    """Pure-python packages have no ABI to disagree about."""
    pkg = tmp_path / "gi"
    pkg.mkdir()
    assert bootstrap.abi_matches(pkg, ".cpython-312-x86_64-linux-gnu.so") is True


def test_ensure_short_circuits_when_already_importable():
    result = bootstrap.ensure_accessibility(importer=lambda name: True)
    assert result["ok"] is True
    assert result["method"] == "already-importable"


def test_ensure_reports_unavailable_when_nothing_to_link():
    result = bootstrap.ensure_accessibility(
        importer=lambda name: False,
        finder=lambda name: None,
        site_packages=lambda: Path("/tmp/whatever"),
    )
    assert result["ok"] is False
    assert result["method"] == "unavailable"
    assert "python3-gi" in result["detail"]


def test_ensure_reports_unavailable_outside_a_venv(tmp_path):
    result = bootstrap.ensure_accessibility(
        importer=lambda name: False,
        finder=lambda name: tmp_path,
        site_packages=lambda: None,
    )
    assert result["ok"] is False
    assert result["method"] == "unavailable"


def test_ensure_links_and_reports_success(tmp_path):
    src = tmp_path / "gi"
    src.mkdir()
    site = tmp_path / "site"
    site.mkdir()
    seen = {"n": 0}

    def importer(name):
        # fails first, succeeds after the link
        seen["n"] += 1
        return seen["n"] > 1

    result = bootstrap.ensure_accessibility(
        importer=importer,
        finder=lambda name: src if name == "gi" else None,
        site_packages=lambda: site,
        symlink=FakeLinker(),
    )
    assert result["ok"] is True
    assert result["method"] == "linked"


def test_ensure_never_raises_on_unexpected_failure():
    def boom(name):
        raise RuntimeError("everything is on fire")

    result = bootstrap.ensure_accessibility(importer=boom)
    assert result["ok"] is False
    assert isinstance(result["detail"], str)


def test_live_walker_bootstraps_bindings_once(monkeypatch):
    """The fast path must self-heal, and must not re-probe on every lookup."""
    from actions.grounding import atspi

    calls = []
    monkeypatch.setattr(atspi, "_BOOTSTRAP", None)
    monkeypatch.setattr(
        "actions.grounding.bootstrap.ensure_accessibility",
        lambda **kw: calls.append(1) or {"ok": True, "method": "linked",
                                         "detail": ""},
    )
    atspi._ensure_bindings_once()
    atspi._ensure_bindings_once()
    atspi._ensure_bindings_once()
    assert len(calls) == 1
