from __future__ import annotations


def test_retry_policy_does_not_use_worker_event_completion() -> None:
    from tools.agent_harness import retry_policy

    assert not hasattr(retry_policy, "worker_stopped_after_latest")
    assert not hasattr(retry_policy, "latest_worker_stop_time")
