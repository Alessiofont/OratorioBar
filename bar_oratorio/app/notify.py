"""Notifiche push con ntfy (https://ntfy.sh): un canale per i coordinatori e uno per l'amministratore."""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from datetime import timedelta

from .db import db, get_settings, now, set_setting, today

log = logging.getLogger("bar.ntfy")


def _publish(server: str, topic: str, title: str, message: str, tags: list[str] | None = None,
             priority: int = 3) -> bool:
    body = json.dumps({"topic": topic, "title": title, "message": message, "tags": tags or [],
                       "priority": priority}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(server.rstrip("/") + "/", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "BarOratorio/1.0"})
    try:
        urllib.request.urlopen(req, timeout=10).read()
        return True
    except Exception as e:
        log.warning("ntfy non raggiungibile (%s): %s", topic, e)
        return False


def send(to: list[str], title: str, message: str, tags: list[str] | None = None, wait: bool = False) -> None:
    """to: ['coord'] e/o ['admin']. Di norma in background, per non rallentare chi usa l'app."""
    with db() as conn:
        st = get_settings(conn)
    topics = [st.get("ntfy_topic_coord") if t == "coord" else st.get("ntfy_topic_admin") for t in to]

    def run():
        for t in topics:
            if t:
                _publish(st.get("ntfy_server") or "https://ntfy.sh", t, title, message, tags)
    if wait:
        run()
    else:
        threading.Thread(target=run, daemon=True).start()


def enabled(key: str) -> bool:
    with db() as conn:
        return get_settings(conn).get(key, "1") == "1"


def proposal(what: str, who: str) -> None:
    if enabled("ntfy_proposals"):
        send(["coord"], "Nuova proposta da approvare", f"{who} ha proposto: {what}", ["inbox_tray"])


def list_closed(who: str, taken: int, total: int, missing: list[str]) -> None:
    if enabled("ntfy_list_closed"):
        msg = f"{who} ha chiuso la lista della spesa: {taken} voci prese su {total}."
        if missing:
            msg += "\nNon prese: " + ", ".join(missing[:15])
        msg += "\nRicordatevi di caricare la merce con «Arriva merce»."
        send(["coord"], "Lista della spesa chiusa", msg, ["shopping_cart"])


def digest_text(conn) -> str:
    st = get_settings(conn)
    warn = int(st.get("expiry_warning_days") or 14)
    below = conn.execute(
        "SELECT name, stock, min_stock FROM products WHERE status='attivo' AND min_stock > 0 AND stock <= min_stock "
        "ORDER BY name").fetchall()
    exp = conn.execute(
        "SELECT p.name, c.name AS cart, l.qty, l.expiry, l.expiry_estimated FROM lots l "
        "JOIN products p ON p.id = l.product_id JOIN carts c ON c.id = l.cart_id "
        "WHERE l.qty > 0 AND l.expiry IS NOT NULL AND l.expiry <= ? AND p.status != 'archiviato' ORDER BY l.expiry",
        ((today() + timedelta(days=warn)).isoformat(),)).fetchall()
    parts = []
    if below:
        parts.append("Sotto la scorta minima:\n" + "\n".join(f"• {r['name']}: {r['stock']} pz (min {r['min_stock']})" for r in below))
    if exp:
        parts.append(f"In scadenza nei prossimi {warn} giorni:\n" + "\n".join(
            f"• {r['name']} – {r['qty']} pz, scade il {r['expiry'][8:10]}/{r['expiry'][5:7]}"
            f"{' (stimata)' if r['expiry_estimated'] else ''} · lotto {r['cart']}" for r in exp))
    return "\n\n".join(parts) or "Tutto in ordine: nessun prodotto sotto la scorta minima e nessuna scadenza vicina."


def send_digest(wait: bool = False) -> None:
    with db() as conn:
        text = digest_text(conn)
    send(["coord", "admin"], "Riepilogo settimanale del bar", text, ["clipboard"], wait=wait)


def _scheduler() -> None:
    while True:
        try:
            with db() as conn:
                st = get_settings(conn)
                n = now()
                hh, mm = (st.get("digest_time") or "15:00").split(":")
                due = (st.get("ntfy_digest") == "1" and n.weekday() == int(st.get("digest_weekday") or 0)
                       and (n.hour, n.minute) >= (int(hh), int(mm)) and st.get("digest_last") != n.date().isoformat())
                if due:
                    set_setting(conn, "digest_last", n.date().isoformat())
            if due:
                send_digest()
        except Exception:
            log.exception("errore nel pianificatore del riepilogo")
        time.sleep(60)


def start_scheduler() -> None:
    threading.Thread(target=_scheduler, daemon=True, name="digest").start()
