"""Logica di magazzino: carrelli/lotti, scarichi FEFO, correzioni, modifiche entro N minuti.

Tutte le funzioni lavorano dentro una transazione aperta (db.tx()) e tengono
products.stock allineato alla somma delle righe di lotto.
"""
from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta

from fastapi import HTTPException

from .db import now, now_s, today

FEFO_ORDER = "(l.expiry IS NULL), l.expiry, c.date, l.id"


class NeedsConfirmation(HTTPException):
    def __init__(self, detail: dict):
        super().__init__(409, detail)


def qty_int(v, name: str = "Quantità") -> int:
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        raise HTTPException(400, f"{name} non valida")


def new_batch(conn, btype: str, user_id: int, reason: str | None = None, note: str | None = None,
              cart_id: int | None = None) -> str:
    pre = {"carico": "C", "vendita": "V", "scarico": "S", "correzione": "R", "inventario": "I"}[btype]
    bid = f"{pre}-{now().strftime('%y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
    conn.execute("INSERT INTO batches(id, ts, type, user_id, reason, note, cart_id) VALUES (?,?,?,?,?,?,?)",
                 (bid, now_s(), btype, user_id, reason, note, cart_id))
    return bid


def get_product(conn, pid: int, allow_proposed: bool = False) -> dict:
    p = conn.execute(
        "SELECT p.*, c.shelf_life_days AS cat_life, c.name AS category FROM products p "
        "LEFT JOIN categories c ON c.id = p.category_id WHERE p.id = ?", (pid,)).fetchone()
    if p is None or p["status"] == "archiviato":
        raise HTTPException(404, f"Prodotto {pid} non trovato o non attivo")
    if p["status"] == "proposto" and not allow_proposed:
        raise HTTPException(409, f"«{p['name']}» è in attesa di approvazione: non si può ancora movimentare")
    return dict(p)


def shelf_life(p: dict) -> int | None:
    if p.get("tracks_expiry") == 0:
        return None
    life = p.get("shelf_life_days") or p.get("cat_life")
    if p.get("tracks_expiry") == 1 and not life:
        life = 180
    return life


def estimate_expiry(p: dict, received: date | None = None) -> str | None:
    life = shelf_life(p)
    return ((received or today()) + timedelta(days=int(life))).isoformat() if life else None


def valid_date(s: str | None, name: str) -> str | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError:
        raise HTTPException(400, f"Data non valida per {name}")


def refresh_stock(conn, pid: int) -> tuple[int, int]:
    before = conn.execute("SELECT stock FROM products WHERE id=?", (pid,)).fetchone()["stock"]
    after = conn.execute("SELECT COALESCE(SUM(qty),0) FROM lots WHERE product_id=?", (pid,)).fetchone()[0]
    conn.execute("UPDATE products SET stock=? WHERE id=?", (after, pid))
    return before, after


def _movement(conn, pid, mtype, qty, batch, user_id, reason=None, parts=None) -> int:
    stock_after = conn.execute("SELECT COALESCE(SUM(qty),0) FROM lots WHERE product_id=?", (pid,)).fetchone()[0]
    mid = conn.execute(
        "INSERT INTO movements(ts, product_id, type, qty, stock_after, reason, user_id, batch) VALUES (?,?,?,?,?,?,?,?)",
        (now_s(), pid, mtype, qty, stock_after, reason, user_id, batch)).lastrowid
    for lot_id, q in parts or []:
        conn.execute("INSERT INTO movement_lots(movement_id, lot_id, qty) VALUES (?,?,?) "
                     "ON CONFLICT(movement_id, lot_id) DO UPDATE SET qty = qty + excluded.qty", (mid, lot_id, q))
    return mid


# ---------------------------------------------------------------- lotti (carrelli)

def new_cart(conn, user_id: int, note: str | None, kind: str = "carico", batch: str | None = None) -> dict:
    d = today()
    base = d.strftime("%d/%m/%Y")
    n = conn.execute("SELECT COUNT(*) FROM carts WHERE date=?", (d.isoformat(),)).fetchone()[0]
    name = base if n == 0 else f"{base}-{n + 1}"
    while conn.execute("SELECT 1 FROM carts WHERE name=?", (name,)).fetchone():
        n += 1
        name = f"{base}-{n + 1}"
    cid = conn.execute("INSERT INTO carts(name, date, kind, created_by, note, batch, created_at) VALUES (?,?,?,?,?,?,?)",
                       (name, d.isoformat(), kind, user_id, note, batch, now_s())).lastrowid
    return {"id": cid, "name": name}


def add_line(conn, cart_id: int, pid: int, q: int, expiry: str | None, estimated: bool) -> int:
    return conn.execute(
        "INSERT INTO lots(cart_id, product_id, expiry, expiry_estimated, qty_in, qty, created_at) VALUES (?,?,?,?,?,?,?)",
        (cart_id, pid, expiry, 1 if estimated else 0, q, q, now_s())).lastrowid


def check_missing_expiry(conn, lines: list[dict]) -> list[dict]:
    out = []
    for i, it in enumerate(lines):
        p = get_product(conn, int(it["product_id"]))
        if not (it.get("expiry") or "").strip() and shelf_life(p):
            out.append({"index": i, "product_id": p["id"], "name": p["name"], "estimated": estimate_expiry(p)})
    return out


def carico(conn, user_id: int, lines: list[dict], note: str | None, confirm_missing_expiry: bool) -> dict:
    """Conferma un carrello: diventa un lotto (nome = data) con una riga per prodotto."""
    if not lines:
        raise HTTPException(400, "Il carrello è vuoto")
    prepared = []
    for it in lines:
        q = qty_int(it.get("qty"))
        if q <= 0:
            raise HTTPException(400, "Le quantità devono essere maggiori di zero")
        p = get_product(conn, int(it["product_id"]))
        exp = valid_date(it.get("expiry"), p["name"])
        est = False
        if not exp and shelf_life(p):
            exp, est = estimate_expiry(p), True
        prepared.append((p, q, exp, est))
    missing = check_missing_expiry(conn, lines)
    if missing and not confirm_missing_expiry:
        raise NeedsConfirmation({"code": "missing_expiry", "lines": missing,
                                 "message": "Alcune righe non hanno la data di scadenza"})
    bid = new_batch(conn, "carico", user_id, note=note)
    cart = new_cart(conn, user_id, note, "carico", bid)
    conn.execute("UPDATE batches SET cart_id=? WHERE id=?", (cart["id"], bid))
    changed = []
    for p, q, exp, est in prepared:
        lid = add_line(conn, cart["id"], p["id"], q, exp, est)
        before, after = refresh_stock(conn, p["id"])
        _movement(conn, p["id"], "carico", q, bid, user_id, parts=[(lid, q)])
        changed.append((p["id"], before, after))
    return {"batch": bid, "cart": cart, "changed": changed}


# ---------------------------------------------------------------- uscite FEFO

def take(conn, pid: int, qty: int, lot_id: int | None = None) -> list[tuple[int, int]]:
    """Preleva pezzi: prima la riga indicata, poi quella che scade prima (FEFO)."""
    lots = conn.execute(
        f"SELECT l.id, l.qty FROM lots l JOIN carts c ON c.id = l.cart_id WHERE l.product_id=? AND l.qty>0 "
        f"ORDER BY (l.id = ?) DESC, {FEFO_ORDER}", (pid, lot_id or -1)).fetchall()
    avail = sum(l["qty"] for l in lots)
    if avail < qty:
        name = conn.execute("SELECT name FROM products WHERE id=?", (pid,)).fetchone()["name"]
        raise HTTPException(409, {"code": "insufficient", "product_id": pid,
                                  "message": f"{name}: ne restano solo {avail} (richiesti {qty})"})
    taken, rest = [], qty
    for l in lots:
        if rest <= 0:
            break
        t = min(rest, l["qty"])
        conn.execute("UPDATE lots SET qty = qty - ? WHERE id=?", (t, l["id"]))
        taken.append((l["id"], t))
        rest -= t
    return taken


def scarico(conn, user_id: int, mtype: str, items: list[dict], reason: str | None, note: str | None) -> dict:
    if mtype not in ("vendita", "scarico"):
        raise HTTPException(400, "Tipo di uscita non valido")
    agg: dict[tuple[int, int | None], int] = {}
    for it in items or []:
        q = qty_int(it.get("qty"))
        if q <= 0:
            raise HTTPException(400, "Le quantità devono essere maggiori di zero")
        key = (int(it["product_id"]), int(it["lot_id"]) if it.get("lot_id") else None)
        agg[key] = agg.get(key, 0) + q
    if not agg:
        raise HTTPException(400, "Nessun prodotto indicato")
    bid = new_batch(conn, mtype, user_id, reason=reason, note=note)
    changed = []
    for (pid, lot_id), q in agg.items():
        get_product(conn, pid)
        taken = take(conn, pid, q, lot_id)
        before, after = refresh_stock(conn, pid)
        _movement(conn, pid, mtype, -q, bid, user_id, reason, [(lid, -t) for lid, t in taken])
        changed.append((pid, before, after))
    return {"batch": bid, "changed": changed}


# ---------------------------------------------------------------- correzioni

def add_found(conn, user_id: int, pid: int, diff: int, target, bid: str, mtype: str, reason: str,
              cache: dict) -> tuple[int, str]:
    """Pezzi in più: nella riga di lotto scelta, altrimenti in un lotto di correzione (uno per documento)."""
    p = get_product(conn, pid)
    if target not in (None, "", "nuovo"):
        lot = conn.execute("SELECT l.id, c.name FROM lots l JOIN carts c ON c.id = l.cart_id "
                           "WHERE l.id=? AND l.product_id=?", (int(target), pid)).fetchone()
        if lot is None:
            raise HTTPException(400, "Il lotto scelto non contiene questo prodotto")
        conn.execute("UPDATE lots SET qty = qty + ?, qty_in = qty_in + ? WHERE id=?", (diff, diff, lot["id"]))
        return lot["id"], lot["name"]
    if "cart" not in cache:
        cache["cart"] = new_cart(conn, user_id, reason, "correzione", bid)
    exp = estimate_expiry(p)
    lid = add_line(conn, cache["cart"]["id"], pid, diff, exp, bool(exp))
    return lid, cache["cart"]["name"]


def set_stock(conn, user_id: int, pid: int, target_stock: int, mtype: str, bid: str, reason: str,
              target=None, cache: dict | None = None):
    """Porta la giacenza a target_stock. Ritorna (pid, prima, dopo, dove) o None se nessuna differenza."""
    get_product(conn, pid)
    target_stock = qty_int(target_stock)
    if target_stock < 0:
        raise HTTPException(400, "La giacenza non può essere negativa")
    cur = conn.execute("SELECT COALESCE(SUM(qty),0) FROM lots WHERE product_id=?", (pid,)).fetchone()[0]
    diff = target_stock - cur
    if diff == 0:
        return None
    where = None
    if diff < 0:
        parts = [(lid, -t) for lid, t in take(conn, pid, -diff)]
    else:
        lid, where = add_found(conn, user_id, pid, diff, target, bid, mtype, reason, cache if cache is not None else {})
        parts = [(lid, diff)]
    before, after = refresh_stock(conn, pid)
    _movement(conn, pid, mtype, diff, bid, user_id, reason, parts)
    return (pid, before, after, where)


def adjust_lot(conn, user_id: int, lot_id: int, new_qty: int, bid: str, reason: str):
    lot = conn.execute("SELECT * FROM lots WHERE id=?", (lot_id,)).fetchone()
    if lot is None:
        raise HTTPException(404, "Riga di lotto non trovata")
    get_product(conn, lot["product_id"])
    new_qty = qty_int(new_qty)
    if new_qty < 0:
        raise HTTPException(400, "Il residuo non può essere negativo")
    diff = new_qty - lot["qty"]
    if diff == 0:
        raise HTTPException(409, "Il residuo è già quello indicato")
    conn.execute("UPDATE lots SET qty=?, qty_in=MAX(qty_in, ?) WHERE id=?", (new_qty, new_qty, lot_id))
    before, after = refresh_stock(conn, lot["product_id"])
    _movement(conn, lot["product_id"], "correzione", diff, bid, user_id, reason, [(lot_id, diff)])
    return (lot["product_id"], before, after), lot["qty"], diff


# ---------------------------------------------------------------- modifica entro N minuti

def batch_lines(conn, bid: str) -> list[dict]:
    """Righe nette di un documento per prodotto (somma dei movimenti, modifiche incluse)."""
    return [dict(r) for r in conn.execute(
        "SELECT m.product_id, p.name, SUM(m.qty) AS qty FROM movements m JOIN products p ON p.id = m.product_id "
        "WHERE m.batch=? GROUP BY m.product_id HAVING SUM(m.qty) != 0 ORDER BY MIN(m.id)", (bid,)).fetchall()]


def editable(batch: dict, user: dict, minutes: int) -> tuple[bool, int]:
    if batch["type"] not in ("carico", "vendita", "scarico") or batch["user_id"] != user["id"]:
        return False, 0
    ts = datetime.fromisoformat(batch["ts"])
    left = minutes - int((now() - ts).total_seconds() // 60)
    return left > 0, max(0, left)


def edit_carico(conn, user: dict, b: dict, rows: list[dict], note: str | None) -> list[str]:
    changes: list[str] = []
    cart = conn.execute("SELECT * FROM carts WHERE id=?", (b["cart_id"],)).fetchone()
    existing = {r["id"]: dict(r) for r in conn.execute(
        "SELECT l.*, p.name FROM lots l JOIN products p ON p.id=l.product_id WHERE l.cart_id=? AND l.qty_in > 0",
        (cart["id"],)).fetchall()}
    keep = [r for r in rows if not r.get("delete")]
    if not keep:
        raise HTTPException(409, "Non puoi eliminare tutto il carico: lascia almeno una riga")
    for r in rows:
        lid = r.get("lot_id")
        if lid and int(lid) in existing:
            l = existing[int(lid)]
            used = l["qty_in"] - l["qty"]
            new_in = 0 if r.get("delete") else qty_int(r.get("qty"))
            if new_in < 0 or (not r.get("delete") and new_in <= 0):
                raise HTTPException(400, "Le quantità devono essere maggiori di zero")
            if new_in < used:
                raise HTTPException(409, f"{l['name']}: ne sono già usciti {used}, non puoi scendere sotto")
            delta = new_in - l["qty_in"]
            if delta:
                conn.execute("UPDATE lots SET qty_in = ?, qty = qty + ? WHERE id=?", (new_in, delta, l["id"]))
                refresh_stock(conn, l["product_id"])
                _movement(conn, l["product_id"], "carico", delta, b["id"], user["id"], "Modifica", [(l["id"], delta)])
                changes.append(f"{l['name']}: riga eliminata" if r.get("delete") else f"{l['name']} {l['qty_in']} → {new_in}")
            if not r.get("delete"):
                exp = valid_date(r.get("expiry"), l["name"])
                if exp and exp != l["expiry"]:
                    conn.execute("UPDATE lots SET expiry=?, expiry_estimated=0 WHERE id=?", (exp, l["id"]))
                    changes.append(f"{l['name']} scadenza {l['expiry'] or '—'} → {exp}")
        elif not lid and not r.get("delete"):
            p = get_product(conn, int(r["product_id"]))
            q = qty_int(r.get("qty"))
            if q <= 0:
                raise HTTPException(400, "Le quantità devono essere maggiori di zero")
            exp = valid_date(r.get("expiry"), p["name"])
            est = False
            if not exp and shelf_life(p):
                exp, est = estimate_expiry(p), True
            nl = add_line(conn, cart["id"], p["id"], q, exp, est)
            refresh_stock(conn, p["id"])
            _movement(conn, p["id"], "carico", q, b["id"], user["id"], "Modifica", [(nl, q)])
            changes.append(f"{p['name']} aggiunto +{q}")
    if note is not None and (note or "") != (cart["note"] or ""):
        conn.execute("UPDATE carts SET note=? WHERE id=?", (note or None, cart["id"]))
        conn.execute("UPDATE batches SET note=? WHERE id=?", (note or None, b["id"]))
        changes.append("nota modificata")
    return changes


def edit_uscita(conn, user: dict, b: dict, rows: list[dict]) -> list[str]:
    changes: list[str] = []
    orig = {r["product_id"]: -r["qty"] for r in batch_lines(conn, b["id"])}
    want: dict[int, int] = {}
    for r in rows:
        pid = int(r["product_id"])
        q = 0 if r.get("delete") else qty_int(r.get("qty"))
        if q < 0 or (not r.get("delete") and q == 0):
            raise HTTPException(400, "Le quantità devono essere maggiori di zero")
        want[pid] = want.get(pid, 0) + q
    for pid in orig:
        want.setdefault(pid, orig[pid])
    if not any(v > 0 for v in want.values()):
        raise HTTPException(409, "Non puoi eliminare tutta l'uscita: lascia almeno una riga")
    for pid, q in want.items():
        delta = q - orig.get(pid, 0)          # >0 escono altri pezzi, <0 rientrano
        if not delta:
            continue
        p = get_product(conn, pid)
        if delta > 0:
            parts = [(lid, -t) for lid, t in take(conn, pid, delta)]
        else:
            parts, rest = [], -delta
            for ml in conn.execute(
                    "SELECT ml.lot_id, -SUM(ml.qty) AS out FROM movement_lots ml JOIN movements m ON m.id = ml.movement_id "
                    "WHERE m.batch=? AND m.product_id=? GROUP BY ml.lot_id HAVING out > 0 ORDER BY ml.lot_id DESC",
                    (b["id"], pid)).fetchall():
                if rest <= 0:
                    break
                g = min(rest, ml["out"])
                conn.execute("UPDATE lots SET qty = qty + ? WHERE id=?", (g, ml["lot_id"]))
                parts.append((ml["lot_id"], g))
                rest -= g
        refresh_stock(conn, pid)
        _movement(conn, pid, b["type"], -delta, b["id"], user["id"], "Modifica", parts)
        changes.append(f"{p['name']} {orig.get(pid, 0)} → {q}")
    return changes


def merge_product(conn, src: int, dst: int) -> None:
    if src == dst:
        raise HTTPException(400, "Scegli un prodotto diverso")
    get_product(conn, src, allow_proposed=True)
    get_product(conn, dst)
    conn.execute("UPDATE barcodes SET product_id=?, pending=0 WHERE product_id=?", (dst, src))
    conn.execute("UPDATE lots SET product_id=? WHERE product_id=?", (dst, src))
    conn.execute("UPDATE movements SET product_id=? WHERE product_id=?", (dst, src))
    conn.execute("DELETE FROM inventory_lines WHERE product_id=?", (src,))
    conn.execute("DELETE FROM shop_items WHERE product_id=?", (src,))
    conn.execute("DELETE FROM products WHERE id=?", (src,))
    refresh_stock(conn, dst)
