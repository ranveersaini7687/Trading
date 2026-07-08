"""Bot heartbeat + activity log for the ops dashboard."""

import json
import os
from datetime import datetime

STATUS_FILE = "bot_status.json"
ACTIVITY_FILE = "activity_log.jsonl"
ROOT = os.path.dirname(os.path.abspath(__file__))


def _path(name):
    return os.path.join(ROOT, name)


def load_status():
    p = _path(STATUS_FILE)
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return {}


def write_status(**fields):
    data = load_status()
    data.update(fields)
    data["updated_at"] = datetime.now().isoformat()
    with open(_path(STATUS_FILE), "w") as f:
        json.dump(data, f, indent=2)


def log_activity(event_type, message, **extra):
    entry = {
        "ts": datetime.now().isoformat(),
        "type": event_type,
        "msg": message,
        **extra,
    }
    with open(_path(ACTIVITY_FILE), "a") as f:
        f.write(json.dumps(entry) + "\n")


def read_activity(limit=40):
    p = _path(ACTIVITY_FILE)
    if not os.path.exists(p):
        return []
    with open(p) as f:
        lines = f.readlines()
    entries = []
    for line in lines[-limit:]:
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return list(reversed(entries))


def file_age_minutes(path):
    full = path if os.path.isabs(path) else _path(path)
    if not os.path.exists(full):
        return None
    mtime = os.path.getmtime(full)
    return round((datetime.now().timestamp() - mtime) / 60, 1)
