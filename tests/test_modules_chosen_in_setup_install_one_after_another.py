from core.setup_modules import install_chosen


def _run(names, installed=(), fail=()):
    events = []

    def install(name, progress):
        progress("working")
        if name in fail:
            raise RuntimeError("no network\nmore detail")

    install_chosen(names, installed=set(installed), install=install,
                   report=lambda n, s, t: events.append((n, s, t)))
    return events


def test_each_chosen_module_goes_installing_then_ready():
    assert _run(["trade"]) == [("trade", "installing", "Starting…"),
                               ("trade", "installing", "working"),
                               ("trade", "ready", "")]


def test_a_module_already_here_is_not_installed_again():
    assert _run(["trade"], installed=["trade"]) == [("trade", "ready", "")]


def test_one_failure_is_reported_in_a_sentence_and_the_next_still_installs():
    events = _run(["3d", "trade"], fail=["3d"])
    assert ("3d", "failed", "no network") in events
    assert ("trade", "ready", "") in events


def test_choosing_nothing_does_nothing():
    assert _run([]) == []
