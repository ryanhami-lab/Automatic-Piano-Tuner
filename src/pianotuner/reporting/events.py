import importlib.metadata
import json
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path


class MemorySink:
    def __init__(self):
        self.events: list[dict] = []

    def emit(self, event: dict):
        self.events.append({"event_id": len(self.events) + 1, **event})

    def finish(self, summary: dict):
        self.summary = dict(summary)


class SessionLog(MemorySink):
    """Synchronous append/flush at the worker boundary; failure propagates to Stop."""
    def __init__(self, directory: Path, metadata: dict):
        super().__init__()
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        try:
            commit = subprocess.check_output(["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL,
                                             timeout=2, text=True).strip()
        except (OSError, subprocess.SubprocessError):
            commit = "uncommitted"
        dependencies = {}
        for name in ("pianotuner", "numpy", "scipy", "sounddevice", "pyserial"):
            try:
                dependencies[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                pass
        self.metadata = {"schema_version": 1, "software_commit": commit,
                         "python": platform.python_version(), "platform": platform.platform(),
                         "dependencies": dependencies, "started_utc": datetime.now(UTC).isoformat(),
                         **metadata}
        self._write_json("metadata.json", self.metadata)
        self._file = (self.directory / "events.jsonl").open("x", encoding="utf-8")

    def _write_json(self, name, value):
        path = self.directory / name
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(path)

    def emit(self, event):
        enriched = {"event_id": len(self.events) + 1, **event}
        self._file.write(json.dumps(enriched, allow_nan=False) + "\n")
        self._file.flush()
        self.events.append(enriched)

    def finish(self, summary):
        self._file.flush()
        self.metadata["ended_utc"] = datetime.now(UTC).isoformat()
        self._write_json("metadata.json", self.metadata)
        self._file.close()
        # Publish success only after all other evidence has been persisted.
        self._write_json("summary.json", summary)
        self.summary = dict(summary)

    def close(self):
        self._file.close()
