"""2.60.0: the last computers a user opened - shown as tabs at the top (like a browser), the same on every PC.

Stored per user in user_prefs (key "recent_computers") as [{id, first, last}]: the order of the tabs follows the time a
computer was first opened (tabs do not jump around when switching between them); with more than MAX entries the one
used longest ago goes. Only computer IDs - names and online state are read fresh, deleted computers disappear and
OU admins only ever get computers of their area.
"""
import json

from . import access, db, util

KEY = "recent_computers"
MAX = 10


def _load(username):
    try:
        v = json.loads(db.val("SELECT value FROM user_prefs WHERE username=? AND key=?", (username, KEY)) or "[]")
    except ValueError:
        v = []
    out, seen = [], set()
    for e in v if isinstance(v, list) else []:
        if isinstance(e, dict) and str(e.get("id", "")).isdigit() and int(e["id"]) not in seen:
            seen.add(int(e["id"]))
            out.append({"id": int(e["id"]), "first": str(e.get("first") or ""), "last": str(e.get("last") or "")})
    return out


def _save(username, items):
    db.ex("INSERT INTO user_prefs(username, key, value, at) VALUES(?,?,?,?) "
          "ON CONFLICT(username, key) DO UPDATE SET value=excluded.value, at=excluded.at",
          (username, KEY, json.dumps(items[-MAX:]), util.now()))


def listing(user):
    """[{id, name, online, addr_problem, current_user}] in tab order - only existing computers the user may see."""
    items = _load(user["username"])
    if not items:
        return []
    allowed = access.computer_ids(user)
    ids = [e["id"] for e in items if allowed is None or e["id"] in allowed]
    if not ids:
        return []
    rows = {r["id"]: r for r in db.q(
        f"SELECT id, name, online, addr_problem, current_user FROM computers WHERE id IN ({','.join('?' * len(ids))})", tuple(ids))}
    return [{"id": i, "name": rows[i]["name"], "online": bool(rows[i]["online"]), "addr_problem": bool(rows[i]["addr_problem"]),
             "current_user": rows[i]["current_user"] or ""} for i in ids if i in rows]


def touch(user, cid):
    """Computer opened: new → appended as the last tab (the one used longest ago goes beyond MAX), known → stays in place."""
    cid = int(cid)
    allowed = access.computer_ids(user)
    if (allowed is not None and cid not in allowed) or not db.val("SELECT 1 FROM computers WHERE id=?", (cid,)):
        return listing(user)
    now = util.now()
    exist = set(r["id"] for r in db.q("SELECT id FROM computers"))
    # deleted computers go - and (audit 2026-11) those an OU admin may no longer see: they took places of the 10 invisibly
    items = [e for e in _load(user["username"]) if e["id"] in exist and (allowed is None or e["id"] in allowed)]
    hit = next((e for e in items if e["id"] == cid), None)
    if hit:
        hit["last"] = now
    else:
        while len(items) >= MAX:
            items.remove(min(items, key=lambda e: e["last"]))
        items.append({"id": cid, "first": now, "last": now})
    items.sort(key=lambda e: e["first"])
    _save(user["username"], items)
    return listing(user)


def close(user, cid=None):
    """Close one tab (cid) or all (None)."""
    items = [] if cid is None else [e for e in _load(user["username"]) if e["id"] != int(cid)]
    _save(user["username"], items)
    return listing(user)
