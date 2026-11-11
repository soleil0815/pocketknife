"""2.31: Where does the CPU load come from? - CPU per background task and load peaks with whatever was running.

Deliberately simple and without third-party packages: the CPU time of the whole process (all threads) before and after
a task, or between two scheduler ticks. If several tasks run at the same time, the time counts for each of them - the
values are a pointer ("what was running when it got hot"), not a per-thread measurement.
"""
import collections
import re
import resource
import threading
import time

from . import util

PEAK_PCT = 40          # from this load (% of one core, averaged over one tick) a peak is recorded
_lock = threading.Lock()
_tasks = {}            # task -> {"runs", "wall", "cpu", "last_cpu", "last_at"}
_peaks = collections.deque(maxlen=30)
_req = collections.Counter()
_t0 = time.time()
_last = None
_NUM = re.compile(r"/\d+(?=/|$)")


def cpu():
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


class task:
    """with loadstats.task("heartbeat"): …"""

    def __init__(self, key):
        self.key = key

    def __enter__(self):
        self.c, self.w = cpu(), time.time()
        return self

    def __exit__(self, *_exc):
        dc, dw = max(0.0, cpu() - self.c), max(0.0, time.time() - self.w)
        with _lock:
            s = _tasks.setdefault(self.key, {"runs": 0, "wall": 0.0, "cpu": 0.0, "last_cpu": 0.0, "last_at": None})
            s["runs"] += 1
            s["wall"] += dw
            s["cpu"] += dc
            s["last_cpu"] = dc
            s["last_at"] = util.now()
        return False


def request(path):
    """Per request to the UI/API: count the path without IDs (e.g. /api/computers/{id})."""
    with _lock:
        _req[_NUM.sub("/{id}", path or "")] += 1


def sample(running, scans=0, deployments=0, remote=0):
    """Called by the scheduler on every tick: load since the previous tick; from PEAK_PCT on, record it with context."""
    global _last
    now, c = time.time(), cpu()
    with _lock:
        prev, _last = _last, (now, c)
        req = _req.most_common(4)
        _req.clear()
    if not prev or now - prev[0] < 5:
        return None
    pct = round(100 * (c - prev[1]) / (now - prev[0]))
    if pct >= PEAK_PCT:
        with _lock:
            _peaks.append({"at": util.now(), "pct": pct, "tasks": sorted(running), "scans": scans,
                           "deployments": deployments, "remote": remote,  # 2.44.1: open remote sessions relay the screen
                           "requests": [f"{p} ×{n}" for p, n in req]})
    return pct


def view():
    up = max(1.0, time.time() - _t0)
    with _lock:
        tasks = sorted(({"key": k, **v, "wall": round(v["wall"], 1), "cpu": round(v["cpu"], 1),
                         "last_cpu": round(v["last_cpu"], 1)} for k, v in _tasks.items()), key=lambda x: -x["cpu"])[:12]
        peaks = list(_peaks)[-10:][::-1]
    return {"avg_pct": round(100 * cpu() / up), "uptime_h": round(up / 3600, 1), "tasks": tasks, "peaks": peaks,
            "peak_pct": PEAK_PCT}
