"""Small durable status file, without credentials, cookies or request headers."""
from .common import now, write_json


class Progress:
    def __init__(self, root, echo=True):
        self.path = root / "status.json"
        self.echo = echo

    def __call__(self, stage, message, **details):
        if self.echo:
            print(message, flush=True)
        write_json(self.path, {"updatedUtc": now(), "stage": stage, "message": message, **details})
