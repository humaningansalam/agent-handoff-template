from __future__ import annotations

import threading

from tools.runtime.json_io import append_jsonl_atomic_under_root


def test_append_jsonl_atomic_under_root_serializes_parallel_writers(tmp_path):
    target = tmp_path / "ops" / "agent-harness" / "latest-events.jsonl"
    errors: list[BaseException] = []

    def append(index: int) -> None:
        try:
            append_jsonl_atomic_under_root(target, {"index": index}, tmp_path)
        except BaseException as exc:  # pragma: no cover - failure captured by assertion
            errors.append(exc)

    threads = [threading.Thread(target=append, args=(index,)) for index in range(30)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 30
