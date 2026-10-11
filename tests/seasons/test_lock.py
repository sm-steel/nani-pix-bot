from nani_pix_bot.seasons import lock


def test_hash_ignores_crlf(tmp_path) -> None:
    lf, crlf = tmp_path / "a.py", tmp_path / "b.py"
    lf.write_bytes(b"x = 1\n")
    crlf.write_bytes(b"x = 1\r\n")
    assert lock.module_hash(lf) == lock.module_hash(crlf)


def test_problems_names_unlocked_edited_and_deleted_runs() -> None:
    locked = {"kept": "aa", "edited": "bb", "deleted": "cc"}
    current = {"kept": "aa", "edited": "zz", "new": "dd"}
    assert lock.problems(locked, current) == [
        "deleted: a locked run module is gone",
        "edited: a locked run module changed — runs are frozen once shipped",
        "new: not in runs.lock — add it (python -m nani_pix_bot.seasons.lock)",
    ]


def test_shipped_run_modules_match_runs_lock() -> None:
    assert lock.problems(lock.read_lock(lock.LOCK_FILE), lock.current(lock.RUNS_DIR)) == []
