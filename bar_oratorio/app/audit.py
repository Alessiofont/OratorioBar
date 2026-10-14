"""Log dettagliato per l'amministratore: chi, quando, da quale dispositivo, cosa, prima/dopo."""
from __future__ import annotations

import json

from .db import db, now_s

CATEGORIES = {
    "accessi": "Accessi",
    "movimenti": "Movimenti",
    "correzioni": "Correzioni",
    "anagrafica": "Anagrafica",
    "proposte": "Proposte",
    "liste": "Liste spesa",
    "utenti": "Utenti",
    "impostazioni": "Impostazioni",
}


def _dump(v):
    if v is None:
        return None
    return json.dumps(v, ensure_ascii=False, default=str)


def log(user: dict | None, category: str, action: str, detail: str = "", before=None, after=None,
        conn=None, *, device: str | None = None, ip: str | None = None) -> None:
    """Scrive una voce di log. Dentro una transazione passare `conn` per restare atomici."""
    u = user or {}
    vals = (now_s(), u.get("id"), u.get("display_name"), device if device is not None else u.get("device_name"),
            ip if ip is not None else u.get("ip"), category, action, (detail or "")[:1000], _dump(before), _dump(after))
    sql = ("INSERT INTO audit_log(ts, user_id, user_name, device, ip, category, action, detail, before, after) "
           "VALUES (?,?,?,?,?,?,?,?,?,?)")
    if conn is not None:
        conn.execute(sql, vals)
    else:
        with db() as c:
            c.execute(sql, vals)


def diff(before: dict, after: dict, labels: dict | None = None) -> str:
    """Riassunto leggibile dei campi cambiati: 'scorta minima 18 → 24'."""
    parts = []
    for k, v in after.items():
        old = before.get(k)
        if old != v:
            parts.append(f"{(labels or {}).get(k, k)} {old if old not in (None, '') else '—'} → {v if v not in (None, '') else '—'}")
    return ", ".join(parts)
