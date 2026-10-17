"""Thread-safe process-local engine metrics."""

from threading import Lock


class Feedback:
    """Maintain counters safely across worker threads and event loops."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._metrics = dict.fromkeys(
            (
                "completed_tasks",
                "failed_tasks",
                "cycles",
                "agent_failures",
                "knowledge_entries",
            ),
            0,
        )

    def increment(self, **counts: int) -> None:
        """Atomically increment named counters by nonnegative integer amounts."""
        if any(
            key not in self._metrics or type(value) is not int or value < 0
            for key, value in counts.items()
        ):
            raise ValueError("Invalid feedback counter update")
        with self._lock:
            for key, value in counts.items():
                self._metrics[key] += value

    def set_knowledge_entries(self, count: int) -> None:
        """Synchronize the persisted knowledge gauge rather than increment it."""
        if type(count) is not int or count < 0:
            raise ValueError("Knowledge count must be a nonnegative integer")
        with self._lock:
            self._metrics["knowledge_entries"] = count

    def snapshot(self) -> dict[str, int | float]:
        """Return a detached, consistent JSON-serializable metrics snapshot."""
        with self._lock:
            return {key: value for key, value in self._metrics.items()}


__all__ = ["Feedback"]
