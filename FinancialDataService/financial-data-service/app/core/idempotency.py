import hashlib
import json
from typing import Any


def calculate_request_hash(data: Any) -> str:
    if isinstance(data, (dict, list)):
        serialized = json.dumps(data, sort_keys=True, separators=(",", ":"))
    elif isinstance(data, bytes):
        serialized = data.decode("utf-8", errors="replace")
    elif isinstance(data, str):
        serialized = data
    else:
        serialized = str(data)

    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
