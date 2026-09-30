"""Small durable status file, without credentials, cookies or request headers."""
from .common import now, write_json


class Progress:
    def __init__(self, root):
        self.path = root / "status.json"

    def __call__(self, stage, message, **details):
        print(message, flush=True)
        write_json(self.path, {"updatedUtc": now(), "stage": stage, "message": message, **details})
