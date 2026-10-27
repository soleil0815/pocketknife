"""2.31: Woher kommt die Rechenlast? – CPU je Hintergrundaufgabe und Lastspitzen mit dem, was gerade lief.

Bewusst einfach und ohne Fremdpakete: die Rechenzeit des ganzen Prozesses (alle Threads) vor und nach einer Aufgabe bzw.
zwischen zwei Takten des Schedulers. Laufen mehrere Aufgaben gleichzeitig, zählt die Zeit bei jeder – die Werte sind
ein Wegweiser („was lief, als es heiß wurde“), keine Messung je Thread.
"""
import collections
import re
import resource
import threading
import time

from . import util

PEAK_PCT = 40          # ab dieser Last (% eines Kerns, gemittelt über einen Takt) wird eine Spitze festgehalten
_lock = threading.Lock()
_tasks = {}            # Aufgabe → {"runs", "wall", "cpu", "last_cpu", "last_at"}
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
    """Je Anfrage an die Oberfläche/API: Pfad ohne IDs zählen (z. B. /api/computers/{id})."""
    with _lock:
        _req[_NUM.sub("/{id}", path or "")] += 1


def sample(running, scans=0, deployments=0):
    """Vom Scheduler je Takt aufgerufen: Last seit dem letzten Takt; ab PEAK_PCT mit Kontext merken."""
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
                           "deployments": deployments, "requests": [f"{p} ×{n}" for p, n in req]})
    return pct


def view():
    up = max(1.0, time.time() - _t0)
    with _lock:
        tasks = sorted(({"key": k, **v, "wall": round(v["wall"], 1), "cpu": round(v["cpu"], 1),
                         "last_cpu": round(v["last_cpu"], 1)} for k, v in _tasks.items()), key=lambda x: -x["cpu"])[:12]
        peaks = list(_peaks)[-10:][::-1]
    return {"avg_pct": round(100 * cpu() / up), "uptime_h": round(up / 3600, 1), "tasks": tasks, "peaks": peaks,
            "peak_pct": PEAK_PCT}
