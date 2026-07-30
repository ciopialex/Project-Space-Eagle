"""Everything the eagle knows about you lives in one small JSON file. Losing it
must take more than bad luck.

Run:  .venv/bin/python -m pytest tests/ -q

THE AMNESIA THIS PINS DOWN
--------------------------
`save_memory` wrote long_term.json in place:

    MEMORY_PATH.write_text(json.dumps(memory, ...))

`open(path, "w")` truncates immediately and the bytes land afterwards. Lose
power, get OOM-killed, or close the laptop lid in that window and the file is
left empty or half-written. `load_memory` then catches the JSONDecodeError,
prints one warning line, and returns `_empty_memory()` — so the failure mode
for a one-in-a-thousand crash was silent, total, unrecoverable memory loss.
Your name, your city, your projects, gone, and the eagle cheerfully carries on.

Separately, `update_memory` did load → mutate → save with the lock held inside
each of load and save but never across the pair. Two threads — a tool saving a
fact while the proactive engine saves another — both read the same base and the
second write silently discarded the first.

Durability here is cheap: write a temp file, fsync it, rename it over the
target (rename is atomic on POSIX), and keep the previous version as a backup
so even a corrupt read has somewhere to fall back to.
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory import memory_manager as mm  # noqa: E402


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the module at a throwaway file — never the real long_term.json."""
    path = tmp_path / "long_term.json"
    monkeypatch.setattr(mm, "MEMORY_PATH", path)
    monkeypatch.setattr(mm, "BACKUP_PATH", path.with_suffix(".bak"), raising=False)
    return path


def _known_memory():
    return {"identity": {"name": {"value": "Shenny", "updated": "2026-07-28"}}}


# ── the crash window ────────────────────────────────────────────────────────

def test_a_write_that_dies_partway_leaves_the_previous_memory_intact(store, monkeypatch):
    """THE regression. An in-place write truncates first, so a crash between
    truncate and flush empties the file. Memory that survived yesterday must
    survive a failed write today."""
    mm.save_memory(_known_memory())
    assert mm.load_memory()["identity"]["name"]["value"] == "Shenny"

    real_replace = mm.os.replace

    def _die_before_rename(src, dst):
        raise OSError("simulated crash before the rename lands")

    monkeypatch.setattr(mm.os, "replace", _die_before_rename)
    with pytest.raises(OSError):
        mm.save_memory({"identity": {"name": {"value": "Overwritten"}}})

    monkeypatch.setattr(mm.os, "replace", real_replace)
    assert mm.load_memory()["identity"]["name"]["value"] == "Shenny"


def test_a_corrupt_store_falls_back_to_the_backup_rather_than_forgetting(store):
    """A truncated file used to mean a clean slate. It must mean 'use the last
    known-good copy' instead."""
    mm.save_memory(_known_memory())
    mm.save_memory({"identity": {"name": {"value": "Shenny"},
                                 "city": {"value": "Bucharest"}}})

    store.write_text('{"identity": {"na', encoding="utf-8")   # torn write

    recovered = mm.load_memory()
    assert recovered["identity"]["name"]["value"] == "Shenny"


def test_a_corrupt_store_with_no_backup_still_degrades_quietly(store):
    """No backup yet is not a crash — first run has nothing to recover."""
    store.write_text("{{{ not json", encoding="utf-8")
    assert mm.load_memory() == mm._empty_memory()


def test_no_temp_file_is_left_behind_after_a_successful_save(store):
    """Litter in the memory directory is how the next reader gets confused."""
    mm.save_memory(_known_memory())
    strays = [p.name for p in store.parent.iterdir()
              if p.suffix not in (".json", ".bak")]
    assert strays == []


# ── concurrent updates ──────────────────────────────────────────────────────

def test_concurrent_updates_do_not_overwrite_each_other(store):
    """THE second regression. load → mutate → save was not atomic as a unit, so
    a tool and the proactive engine saving at the same moment lost one fact
    silently. Every write must survive."""
    mm.save_memory(mm._empty_memory())

    keys = [f"k{i}" for i in range(12)]
    barrier = threading.Barrier(len(keys))
    errors = []

    def _writer(k):
        try:
            barrier.wait(timeout=10)          # maximise the overlap
            mm.update_memory({"notes": {k: {"value": f"v-{k}"}}})
        except Exception as e:                # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=_writer, args=(k,)) for k in keys]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert errors == []
    stored = mm.load_memory()["notes"]
    missing = [k for k in keys if k not in stored]
    assert missing == [], f"lost {len(missing)} concurrent update(s): {missing}"


def test_a_reader_never_observes_a_half_written_file(store):
    """Readers run concurrently with writers. A rename is atomic; a truncating
    write is not — a reader hitting that window used to get an empty store."""
    mm.save_memory(_known_memory())
    seen = []
    stop = threading.Event()

    def _reader():
        while not stop.is_set():
            seen.append(mm.load_memory().get("identity", {}).get("name", {}).get("value"))

    r = threading.Thread(target=_reader, daemon=True)
    r.start()
    for i in range(60):
        mm.save_memory({"identity": {"name": {"value": "Shenny", "updated": f"{i}"}}})
    stop.set()
    r.join(timeout=5)

    assert seen, "reader never ran"
    assert all(v == "Shenny" for v in seen), "a reader saw a torn file"
