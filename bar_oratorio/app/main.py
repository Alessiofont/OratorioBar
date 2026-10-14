"""Bar Oratorio – API e frontend."""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
from datetime import date, timedelta

from fastapi import Body, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import audit, auth, config, notify, photos, projection, stock
from .auth import LEVEL, current_user, require
from .db import PHOTO_DIR, db, get_settings, init_db, now, now_s, rows, set_setting, today, tx

log = logging.getLogger("bar")
STATIC = os.path.join(os.path.dirname(__file__), "static")

app = FastAPI(title="Bar Oratorio", docs_url=None, redoc_url=None, openapi_url=None)


@app.on_event("startup")
def _startup():
    init_db()
    auth.ensure_builtin_admin()
    if os.environ.get("BAR_SCHEDULER", "1") == "1":
        notify.start_scheduler()


@app.middleware("http")
async def _security(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        try:
            auth.check_csrf(request)
        except HTTPException as e:
            return JSONResponse({"detail": e.detail}, status_code=e.status_code)
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


User = Depends(current_user)


def is_staff(user) -> bool:
    return LEVEL[user["role"]] >= 2


def _int(v, name: str, minimum: int | None = 0, allow_none: bool = False):
    if v is None or v == "":
        if allow_none:
            return None
        raise HTTPException(400, f"Campo obbligatorio: {name}")
    try:
        i = int(round(float(v)))
    except (TypeError, ValueError):
        raise HTTPException(400, f"Valore non valido: {name}")
    if minimum is not None and i < minimum:
        raise HTTPException(400, f"{name}: minimo {minimum}")
    return i


def _str(v, name: str, maxlen: int = 120, required: bool = False):
    s = "" if v is None else str(v).strip()
    if required and not s:
        raise HTTPException(400, f"Campo obbligatorio: {name}")
    return s[:maxlen] or None


def _code(v) -> str:
    c = re.sub(r"\s+", "", str(v or ""))
    if not re.fullmatch(r"[0-9A-Za-z\-\.]{4,32}", c):
        raise HTTPException(400, "Barcode non valido")
    return c


def _names(conn, lines) -> str:
    out = []
    for pid, q in lines:
        n = conn.execute("SELECT name FROM products WHERE id=?", (pid,)).fetchone()
        out.append(f"{n['name'] if n else pid} {'+' if q > 0 else ''}{q}")
    return ", ".join(out)


# =================================================================== accesso

@app.post("/api/login")
def login(request: Request, response: Response, body: dict = Body(...)):
    token, user = auth.login(body.get("username", ""), str(body.get("secret", "")), request.headers.get("user-agent"),
                             body.get("device_name"), auth.client_ip(request))
    days = int(config.OPTIONS.get("session_days") or 365)
    response.set_cookie(auth.COOKIE, token, max_age=days * 86400, httponly=True, samesite="lax",
                        secure=request.headers.get("x-forwarded-proto") == "https", path="/")
    return {"user": user}


@app.post("/api/login/check")
def login_check(body: dict = Body(...)):
    """Primo passo dell'accesso: dice se all'utente serve il PIN o la password (senza rivelare se esiste)."""
    with db() as conn:
        u = conn.execute("SELECT role FROM users WHERE username=? AND active=1", ((body.get("username") or "").strip(),)).fetchone()
    return {"method": "password" if u and u["role"] == "admin" else "pin"}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    try:
        u = current_user(request)
        audit.log(u, "accessi", "Uscita")
    except HTTPException:
        pass
    auth.logout(request.cookies.get(auth.COOKIE))
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@app.get("/api/me")
def me(user=User):
    return user


@app.get("/api/health")
def health():
    return {"ok": True, "version": config.VERSION}


# =================================================================== bootstrap / anagrafica

PRODUCT_SQL = """
SELECT p.*, c.name AS category, c.color AS cat_color, c.shelf_life_days AS cat_life,
       u.display_name AS proposed_by_name,
       (SELECT GROUP_CONCAT(code, ' ') FROM barcodes b WHERE b.product_id = p.id AND b.pending = 0) AS barcodes,
       (SELECT l.expiry FROM lots l WHERE l.product_id = p.id AND l.qty > 0 AND l.expiry IS NOT NULL
         ORDER BY l.expiry LIMIT 1) AS next_expiry,
       (SELECT l.expiry_estimated FROM lots l WHERE l.product_id = p.id AND l.qty > 0 AND l.expiry IS NOT NULL
         ORDER BY l.expiry LIMIT 1) AS next_expiry_estimated
FROM products p
LEFT JOIN categories c ON c.id = p.category_id
LEFT JOIN users u ON u.id = p.proposed_by
"""


def _product_out(p) -> dict:
    p = dict(p)
    p["barcodes"] = (p.get("barcodes") or "").split()
    p["tracks"] = bool(stock.shelf_life(p))
    return p


def list_products(conn, include_archived=False) -> list[dict]:
    where = "" if include_archived else "WHERE p.status != 'archiviato'"
    return [_product_out(r) for r in conn.execute(
        f"{PRODUCT_SQL} {where} ORDER BY COALESCE(c.sort, 999), c.name, p.sort, p.name").fetchall()]


PUBLIC_SETTINGS = {"bar_name", "expiry_warning_days", "edit_minutes"}


@app.get("/api/bootstrap")
def bootstrap(user=User):
    with db() as conn:
        st = get_settings(conn)
        warn = int(st.get("expiry_warning_days") or 14)
        limit = (today() + timedelta(days=warn)).isoformat()
        counts = {
            "expiring": conn.execute("SELECT COUNT(*) FROM lots WHERE qty>0 AND expiry IS NOT NULL AND expiry <= ?",
                                     (limit,)).fetchone()[0],
            "below_min": conn.execute("SELECT COUNT(*) FROM products WHERE status='attivo' AND min_stock>0 "
                                      "AND stock <= min_stock").fetchone()[0],
            "shop_open": conn.execute("SELECT COUNT(*) FROM shop_items").fetchone()[0],
            "my_draft": len(json.loads((conn.execute("SELECT lines FROM drafts WHERE user_id=?", (user["id"],)).fetchone()
                                         or {"lines": "[]"})["lines"])),
            "my_editable": 0,
        }
        mins = int(st.get("edit_minutes") or 30)
        for b in conn.execute("SELECT * FROM batches WHERE user_id=? AND ts >= ?",
                              (user["id"], (now() - timedelta(minutes=mins)).isoformat(sep=" "))).fetchall():
            if stock.editable(dict(b), user, mins)[0]:
                counts["my_editable"] += 1
        if is_staff(user):
            counts["proposals"] = conn.execute("SELECT COUNT(*) FROM products WHERE status='proposto'").fetchone()[0] + \
                conn.execute("SELECT COUNT(*) FROM barcodes b JOIN products p ON p.id=b.product_id "
                             "WHERE b.pending=1 AND p.status='attivo'").fetchone()[0]
            counts["drafts_others"] = conn.execute(
                "SELECT COUNT(*) FROM drafts WHERE user_id != ? AND lines != '[]'", (user["id"],)).fetchone()[0]
        inv = conn.execute("SELECT id, started_at FROM inventories WHERE status='aperto'").fetchone()
        settings = st if is_staff(user) else {k: v for k, v in st.items() if k in PUBLIC_SETTINGS}
        return {
            "user": user, "version": config.VERSION, "settings": settings,
            "categories": rows(conn.execute("SELECT * FROM categories ORDER BY sort, name")),
            "products": list_products(conn), "counts": counts,
            "open_inventory": dict(inv) if inv else None, "today": today().isoformat(),
        }


@app.get("/api/products")
def products(include_archived: bool = False, user=User):
    with db() as conn:
        return list_products(conn, include_archived and is_staff(user))


@app.get("/api/products/{pid}")
def product_detail(pid: int, user=User):
    with db() as conn:
        p = conn.execute(f"{PRODUCT_SQL} WHERE p.id = ?", (pid,)).fetchone()
        if p is None:
            raise HTTPException(404, "Prodotto non trovato")
        out = _product_out(p)
        out["barcode_list"] = rows(conn.execute(
            "SELECT code, pending, added_at FROM barcodes WHERE product_id=? ORDER BY added_at", (pid,)))
        if is_staff(user):
            out["projection"] = projection.compute(conn, "fine_mese", product_ids=[pid])["items"][:1] or None
        return out


@app.get("/api/products/{pid}/lots")
def product_lots(pid: int, user=User):
    """Righe di lotto di un prodotto (per scegliere dove mettere i pezzi in più)."""
    require(user, "coordinatore")
    with db() as conn:
        return rows(conn.execute(
            "SELECT l.id, l.qty, l.qty_in, l.expiry, l.expiry_estimated, c.name AS cart FROM lots l "
            "JOIN carts c ON c.id = l.cart_id WHERE l.product_id=? AND l.qty_in > 0 ORDER BY c.date DESC, l.id DESC", (pid,)))


FIELD_LABELS = {"name": "nome", "category_id": "categoria", "min_stock": "scorta minima", "ideal_stock": "scorta ideale",
                "shelf_life_days": "durata", "tracks_expiry": "scadenza", "note": "note", "sort": "ordine"}


def _product_fields(body: dict, conn, partial: bool = False) -> dict:
    f = {}
    if not partial or "name" in body:
        f["name"] = _str(body.get("name"), "nome", 80, required=True)
    if "category_id" in body:
        cid = _int(body.get("category_id"), "categoria", allow_none=True)
        if cid and not conn.execute("SELECT 1 FROM categories WHERE id=?", (cid,)).fetchone():
            raise HTTPException(400, "Categoria inesistente")
        f["category_id"] = cid
    if "min_stock" in body:
        f["min_stock"] = _int(body.get("min_stock"), "scorta minima", allow_none=True) or 0
    if "ideal_stock" in body:
        f["ideal_stock"] = _int(body.get("ideal_stock"), "scorta ideale", allow_none=True)
    if "shelf_life_days" in body:
        f["shelf_life_days"] = _int(body.get("shelf_life_days"), "durata", minimum=1, allow_none=True)
    if "tracks_expiry" in body:
        v = body.get("tracks_expiry")
        f["tracks_expiry"] = None if v in (None, "", "auto") else (1 if v in (1, True, "1") else 0)
    if "note" in body:
        f["note"] = _str(body.get("note"), "note", 500)
    if "sort" in body:
        f["sort"] = _int(body.get("sort"), "ordine", minimum=None, allow_none=True) or 0
    return f


def _check_dup_name(conn, name: str, exclude: int | None = None):
    r = conn.execute("SELECT status FROM products WHERE name = ? COLLATE NOCASE AND status != 'archiviato' AND id != ?",
                     (name, exclude or -1)).fetchone()
    if r:
        raise HTTPException(409, "Esiste già un prodotto con questo nome" + (" (proposto)" if r["status"] == "proposto" else ""))


def _add_barcodes(conn, pid: int, codes, user: dict, pending: bool):
    for c in codes or []:
        c = _code(c)
        ex = conn.execute("SELECT p.name FROM barcodes b JOIN products p ON p.id=b.product_id WHERE b.code=?", (c,)).fetchone()
        if ex:
            raise HTTPException(409, f"Il barcode {c} è già associato a «{ex['name']}»")
        conn.execute("INSERT INTO barcodes(code, product_id, pending, added_by, added_at) VALUES (?,?,?,?,?)",
                     (c, pid, 1 if pending else 0, user["id"], now_s()))


@app.post("/api/products")
def create_product(body: dict = Body(...), user=User):
    """Coordinatore/admin: prodotto attivo. Operatore: proposta, bloccata finché non viene approvata."""
    staff = is_staff(user)
    with tx() as conn:
        f = _product_fields(body, conn)
        _check_dup_name(conn, f["name"])
        f.setdefault("min_stock", 0)
        status = "attivo" if staff else "proposto"
        cols = list(f) + ["status", "created_at", "updated_at"]
        vals = list(f.values()) + [status, now_s(), now_s()]
        if not staff:
            cols += ["proposed_by", "proposed_at", "proposal_note"]
            vals += [user["id"], now_s(), _str(body.get("proposal_note"), "nota", 500)]
        pid = conn.execute(f"INSERT INTO products({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals).lastrowid
        _add_barcodes(conn, pid, body.get("barcodes"), user, pending=not staff)
        codes = ", ".join(body.get("barcodes") or [])
        if staff:
            audit.log(user, "anagrafica", "Nuovo prodotto", f["name"] + (f" · barcode {codes}" if codes else ""), None, f, conn=conn)
        else:
            audit.log(user, "proposte", "Proposta nuovo prodotto", f["name"] + (f" · codice {codes}" if codes else ""), conn=conn)
    if body.get("photo_url"):
        try:
            photos.download_to_product(pid, body["photo_url"], "off-scelta")
        except Exception as e:
            log.info("foto scelta non scaricata: %s", e)
    else:
        photos.auto_photo_async(pid)
    if not staff:
        notify.proposal(f["name"], user["display_name"])
    return {"id": pid, "status": status}


@app.put("/api/products/{pid}")
def update_product(pid: int, body: dict = Body(...), user=User):
    with tx() as conn:
        p = stock.get_product(conn, pid, allow_proposed=True)
        if not (p["status"] == "proposto" and p["proposed_by"] == user["id"]):
            require(user, "coordinatore")
        f = _product_fields(body, conn, partial=True)
        if "name" in f:
            _check_dup_name(conn, f["name"], pid)
        changed = {k: v for k, v in f.items() if p.get(k) != v}
        if changed:
            changed["updated_at"] = now_s()
            conn.execute(f"UPDATE products SET {', '.join(k + '=?' for k in changed)} WHERE id=?", [*changed.values(), pid])
            before = {k: p.get(k) for k in changed if k != "updated_at"}
            after = {k: v for k, v in changed.items() if k != "updated_at"}
            audit.log(user, "anagrafica", f"Modifica prodotto {p['name']}", audit.diff(before, after, FIELD_LABELS),
                      before, after, conn=conn)
    return {"ok": True}


@app.post("/api/products/{pid}/approve")
def approve_product(pid: int, body: dict = Body(default={}), user=User):
    require(user, "coordinatore")
    with tx() as conn:
        p = stock.get_product(conn, pid, allow_proposed=True)
        if p["status"] != "proposto":
            raise HTTPException(409, "Il prodotto non è in attesa di approvazione")
        f = _product_fields(body, conn, partial=True) if body else {}
        if "name" in f:
            _check_dup_name(conn, f["name"], pid)
        f.update({"status": "attivo", "updated_at": now_s()})
        conn.execute(f"UPDATE products SET {', '.join(k + '=?' for k in f)} WHERE id=?", [*f.values(), pid])
        conn.execute("UPDATE barcodes SET pending=0 WHERE product_id=?", (pid,))
        audit.log(user, "proposte", "Proposta approvata", f"{f.get('name', p['name'])} · scorta minima {f.get('min_stock', p['min_stock'])}",
                  conn=conn)
    return {"ok": True}


@app.post("/api/products/{pid}/merge")
def merge_product(pid: int, body: dict = Body(...), user=User):
    require(user, "coordinatore")
    target = _int(body.get("target_id"), "prodotto di destinazione", minimum=1)
    with tx() as conn:
        src = stock.get_product(conn, pid, allow_proposed=True)
        dst = stock.get_product(conn, target)
        stock.merge_product(conn, pid, target)
        audit.log(user, "proposte", "Proposta unita a prodotto esistente", f"{src['name']} → {dst['name']}", conn=conn)
    return {"ok": True, "id": target}


@app.delete("/api/products/{pid}")
def delete_product(pid: int, user=User):
    with tx() as conn:
        p = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
        if p is None:
            raise HTTPException(404, "Prodotto non trovato")
        own = p["status"] == "proposto" and p["proposed_by"] == user["id"]
        if not own:
            require(user, "coordinatore")
        has_mov = conn.execute("SELECT 1 FROM movements WHERE product_id=? LIMIT 1", (pid,)).fetchone()
        if not has_mov:
            for t in ("inventory_lines", "shop_items"):
                conn.execute(f"DELETE FROM {t} WHERE product_id=?", (pid,))
            conn.execute("DELETE FROM products WHERE id=?", (pid,))
            if p["photo"]:
                try:
                    os.remove(os.path.join(PHOTO_DIR, p["photo"]))
                except OSError:
                    pass
            cat = "proposte" if p["status"] == "proposto" else "anagrafica"
            audit.log(user, cat, "Proposta rifiutata" if p["status"] == "proposto" else "Prodotto eliminato", p["name"], conn=conn)
            return {"ok": True, "deleted": True}
        if p["stock"] != 0:
            raise HTTPException(409, f"«{p['name']}» ha ancora {p['stock']} pz: azzerali con «Correggi quantità» "
                                     f"oppure unisci il prodotto a un altro")
        conn.execute("UPDATE products SET status='archiviato', updated_at=? WHERE id=?", (now_s(), pid))
        conn.execute("DELETE FROM barcodes WHERE product_id=?", (pid,))
        audit.log(user, "anagrafica", "Prodotto archiviato", p["name"], conn=conn)
        return {"ok": True, "archived": True}


@app.post("/api/products/{pid}/restore")
def restore_product(pid: int, user=User):
    require(user, "coordinatore")
    with db() as conn:
        p = conn.execute("SELECT name FROM products WHERE id=?", (pid,)).fetchone()
        conn.execute("UPDATE products SET status='attivo', updated_at=? WHERE id=? AND status='archiviato'", (now_s(), pid))
        audit.log(user, "anagrafica", "Prodotto ripristinato", p["name"] if p else str(pid), conn=conn)
    return {"ok": True}


# ---------- barcode e proposte

@app.get("/api/lookup/{code}")
def lookup(code: str, online: bool = True, user=User):
    code = _code(code)
    with db() as conn:
        r = conn.execute("SELECT product_id, pending FROM barcodes WHERE code=?", (code,)).fetchone()
        if r:
            p = conn.execute(f"{PRODUCT_SQL} WHERE p.id=?", (r["product_id"],)).fetchone()
            return {"code": code, "product": _product_out(p), "pending": bool(r["pending"]) or p["status"] == "proposto"}
    return {"code": code, "product": None, "off": photos.lookup_barcode(code) if online else None}


@app.post("/api/products/{pid}/barcodes")
def add_barcode(pid: int, body: dict = Body(...), user=User):
    staff = is_staff(user)
    with tx() as conn:
        p = stock.get_product(conn, pid, allow_proposed=True)
        code = _code(body.get("code"))
        _add_barcodes(conn, pid, [code], user, pending=not staff)
        audit.log(user, "anagrafica" if staff else "proposte", "Barcode aggiunto" if staff else "Proposta barcode",
                  f"{code} → {p['name']}", conn=conn)
    if not staff:
        notify.proposal(f"barcode {code} per {p['name']}", user["display_name"])
    return {"ok": True, "pending": not staff}


@app.post("/api/barcodes/{code}/approve")
def approve_barcode(code: str, user=User):
    require(user, "coordinatore")
    with db() as conn:
        r = conn.execute("SELECT p.name FROM barcodes b JOIN products p ON p.id=b.product_id WHERE b.code=?", (code,)).fetchone()
        conn.execute("UPDATE barcodes SET pending=0 WHERE code=?", (code,))
        audit.log(user, "proposte", "Barcode approvato", f"{code} → {r['name'] if r else '?'}", conn=conn)
    return {"ok": True}


@app.delete("/api/barcodes/{code}")
def delete_barcode(code: str, user=User):
    require(user, "coordinatore")
    with db() as conn:
        r = conn.execute("SELECT b.pending, p.name FROM barcodes b JOIN products p ON p.id=b.product_id WHERE b.code=?", (code,)).fetchone()
        conn.execute("DELETE FROM barcodes WHERE code=?", (code,))
        if r:
            audit.log(user, "proposte" if r["pending"] else "anagrafica", "Barcode rifiutato" if r["pending"] else "Barcode rimosso",
                      f"{code} ({r['name']})", conn=conn)
    return {"ok": True}


@app.get("/api/proposals")
def proposals(user=User):
    require(user, "coordinatore")
    with db() as conn:
        return {
            "products": [_product_out(r) for r in conn.execute(
                f"{PRODUCT_SQL} WHERE p.status='proposto' ORDER BY p.proposed_at").fetchall()],
            "barcodes": rows(conn.execute(
                "SELECT b.code, b.added_at, b.product_id, p.name AS product, u.display_name AS added_by FROM barcodes b "
                "JOIN products p ON p.id=b.product_id LEFT JOIN users u ON u.id=b.added_by "
                "WHERE b.pending=1 AND p.status='attivo' ORDER BY b.added_at")),
        }


# ---------- foto

@app.get("/photos/{fname}")
def photo(fname: str, user=User):
    if not re.fullmatch(r"p\d+_[0-9a-f]{8}\.(jpg|png|webp)", fname):
        raise HTTPException(404)
    path = os.path.join(PHOTO_DIR, fname)
    if not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path, headers={"Cache-Control": "private, max-age=31536000, immutable"})


def _can_edit_photo(conn, pid, user):
    p = stock.get_product(conn, pid, allow_proposed=True)
    if not (p["status"] == "proposto" and p["proposed_by"] == user["id"]):
        require(user, "coordinatore")
    return p


@app.get("/api/photos/search")
def photo_search(q: str, user=User):
    return {"results": photos.search(q)}


@app.post("/api/products/{pid}/photo")
def set_photo(pid: int, body: dict = Body(...), user=User):
    with db() as conn:
        p = _can_edit_photo(conn, pid, user)
    try:
        if body.get("data_url"):
            fname = photos.save_upload(pid, body["data_url"])
        elif body.get("url"):
            fname = photos.download_to_product(pid, body["url"], "off-scelta")
        else:
            raise HTTPException(400, "Nessuna immagine")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Download non riuscito: {e}")
    audit.log(user, "anagrafica", "Foto cambiata", p["name"])
    return {"photo": fname}


@app.delete("/api/products/{pid}/photo")
def delete_photo(pid: int, user=User):
    with db() as conn:
        p = _can_edit_photo(conn, pid, user)
        conn.execute("UPDATE products SET photo=NULL, photo_source=NULL WHERE id=?", (pid,))
        audit.log(user, "anagrafica", "Foto rimossa", p["name"], conn=conn)
    if p.get("photo"):
        try:
            os.remove(os.path.join(PHOTO_DIR, p["photo"]))
        except OSError:
            pass
    return {"ok": True}


@app.post("/api/photos/fill-missing")
def fill_missing(user=User):
    require(user, "coordinatore")
    return {"queued": photos.fill_missing_async()}


# ---------- categorie

def _cat_fields(body):
    return (_str(body.get("name"), "nome", 40, True), _str(body.get("color"), "colore", 9),
            _int(body.get("sort"), "ordine", None, True) or 0, _int(body.get("shelf_life_days"), "durata", 1, True))


@app.post("/api/categories")
def create_category(body: dict = Body(...), user=User):
    require(user, "coordinatore")
    f = _cat_fields(body)
    with db() as conn:
        try:
            cid = conn.execute("INSERT INTO categories(name, color, sort, shelf_life_days) VALUES (?,?,?,?)", f).lastrowid
        except Exception:
            raise HTTPException(409, "Categoria già esistente")
        audit.log(user, "anagrafica", "Nuova categoria", f"{f[0]} · durata {f[3] or 'non scade'}", conn=conn)
        return {"id": cid}


@app.put("/api/categories/{cid}")
def update_category(cid: int, body: dict = Body(...), user=User):
    require(user, "coordinatore")
    f = _cat_fields(body)
    with db() as conn:
        old = conn.execute("SELECT * FROM categories WHERE id=?", (cid,)).fetchone()
        if old is None:
            raise HTTPException(404, "Categoria non trovata")
        conn.execute("UPDATE categories SET name=?, color=?, sort=?, shelf_life_days=? WHERE id=?", (*f, cid))
        before = {"nome": old["name"], "durata": old["shelf_life_days"]}
        after = {"nome": f[0], "durata": f[3]}
        audit.log(user, "anagrafica", f"Modifica categoria {old['name']}", audit.diff(before, after), before, after, conn=conn)
    return {"ok": True}


@app.delete("/api/categories/{cid}")
def delete_category(cid: int, user=User):
    require(user, "coordinatore")
    with db() as conn:
        n = conn.execute("SELECT COUNT(*) FROM products WHERE category_id=? AND status!='archiviato'", (cid,)).fetchone()[0]
        if n:
            raise HTTPException(409, f"La categoria contiene {n} prodotti: spostali prima")
        old = conn.execute("SELECT name FROM categories WHERE id=?", (cid,)).fetchone()
        conn.execute("DELETE FROM categories WHERE id=?", (cid,))
        audit.log(user, "anagrafica", "Categoria eliminata", old["name"] if old else str(cid), conn=conn)
    return {"ok": True}


# =================================================================== carrello in bozza

def _draft(conn, uid: int) -> dict:
    r = conn.execute("SELECT * FROM drafts WHERE user_id=?", (uid,)).fetchone()
    return {"lines": json.loads(r["lines"]) if r else [], "note": r["note"] if r else "", "updated_at": r["updated_at"] if r else None}


@app.get("/api/draft")
def get_draft(user=User):
    with db() as conn:
        return _draft(conn, user["id"])


@app.put("/api/draft")
def put_draft(body: dict = Body(...), user=User):
    """Salva il carrello in bozza (chiamato a ogni modifica: niente si perde chiudendo l'app)."""
    lines = []
    for it in body.get("lines") or []:
        q = _int(it.get("qty"), "quantità", minimum=1)
        lines.append({"product_id": int(it["product_id"]), "qty": q, "expiry": (it.get("expiry") or "")[:10]})
    with db() as conn:
        for it in lines:
            stock.get_product(conn, it["product_id"])
        conn.execute("INSERT INTO drafts(user_id, lines, note, updated_at) VALUES (?,?,?,?) "
                     "ON CONFLICT(user_id) DO UPDATE SET lines=excluded.lines, note=excluded.note, updated_at=excluded.updated_at",
                     (user["id"], json.dumps(lines), _str(body.get("note"), "nota", 300), now_s()))
        return _draft(conn, user["id"])


@app.delete("/api/draft")
def delete_draft(user=User):
    with db() as conn:
        d = _draft(conn, user["id"])
        conn.execute("DELETE FROM drafts WHERE user_id=?", (user["id"],))
        if d["lines"]:
            audit.log(user, "movimenti", "Carrello in bozza svuotato", f"{len(d['lines'])} righe", conn=conn)
    return {"ok": True}


@app.get("/api/drafts")
def other_drafts(user=User):
    require(user, "coordinatore")
    with db() as conn:
        out = []
        for r in conn.execute("SELECT d.*, u.display_name FROM drafts d JOIN users u ON u.id=d.user_id "
                              "WHERE d.user_id != ? AND d.lines != '[]' ORDER BY d.updated_at DESC", (user["id"],)).fetchall():
            lines = json.loads(r["lines"])
            for l in lines:
                n = conn.execute("SELECT name FROM products WHERE id=?", (l["product_id"],)).fetchone()
                l["name"] = n["name"] if n else "?"
            out.append({"user_id": r["user_id"], "user": r["display_name"], "lines": lines, "note": r["note"], "updated_at": r["updated_at"]})
        return out


@app.post("/api/drafts/{uid}/take")
def take_draft(uid: int, user=User):
    require(user, "coordinatore")
    with tx() as conn:
        if _draft(conn, user["id"])["lines"]:
            raise HTTPException(409, "Hai già un carrello in bozza: confermalo o svuotalo prima")
        d = _draft(conn, uid)
        if not d["lines"]:
            raise HTTPException(404, "Bozza non trovata")
        owner = conn.execute("SELECT display_name FROM users WHERE id=?", (uid,)).fetchone()
        conn.execute("DELETE FROM drafts WHERE user_id=?", (user["id"],))
        conn.execute("UPDATE drafts SET user_id=?, updated_at=? WHERE user_id=?", (user["id"], now_s(), uid))
        audit.log(user, "movimenti", f"Ripreso carrello in bozza di {owner['display_name']}", f"{len(d['lines'])} righe", conn=conn)
    return {"ok": True}


@app.delete("/api/drafts/{uid}")
def drop_draft(uid: int, user=User):
    require(user, "coordinatore")
    with db() as conn:
        d = _draft(conn, uid)
        owner = conn.execute("SELECT display_name FROM users WHERE id=?", (uid,)).fetchone()
        conn.execute("DELETE FROM drafts WHERE user_id=?", (uid,))
        audit.log(user, "movimenti", f"Eliminato carrello in bozza di {owner['display_name'] if owner else uid}",
                  f"{len(d['lines'])} righe", conn=conn)
    return {"ok": True}


# =================================================================== movimenti

@app.post("/api/carico")
def do_carico(body: dict = Body(default={}), user=User):
    """Conferma il proprio carrello in bozza: diventa un lotto con la data di oggi."""
    with tx() as conn:
        d = _draft(conn, user["id"])
        res = stock.carico(conn, user["id"], d["lines"], d["note"], bool(body.get("confirm_missing_expiry")))
        conn.execute("DELETE FROM drafts WHERE user_id=?", (user["id"],))
        lines = [(l["product_id"], l["qty"]) for l in d["lines"]]
        audit.log(user, "movimenti", f"Carico lotto {res['cart']['name']}",
                  _names(conn, lines) + (f" · nota: {d['note']}" if d["note"] else ""), conn=conn)
    return {"batch": res["batch"], "cart": res["cart"]}


@app.post("/api/scarico")
def do_scarico(body: dict = Body(...), user=User):
    mtype = body.get("type") or "scarico"
    reason = _str(body.get("reason"), "causale", 80) or ("Venduto al bar" if mtype == "vendita" else None)
    if mtype == "scarico" and not reason:
        raise HTTPException(400, "Indica il motivo dell'uscita")
    with tx() as conn:
        res = stock.scarico(conn, user["id"], mtype, body.get("items") or [], reason, _str(body.get("note"), "note", 300))
        lines = [(r["product_id"], r["qty"]) for r in stock.batch_lines(conn, res["batch"])]
        audit.log(user, "movimenti", ("Vendita" if mtype == "vendita" else f"Scarico ({reason})"), _names(conn, lines), conn=conn)
    return {"batch": res["batch"]}


def _batch_out(conn, b: dict, with_names: bool) -> dict:
    out = {k: b[k] for k in ("id", "ts", "type", "reason", "note", "edited_at", "cart_id")}
    out["lines"] = stock.batch_lines(conn, b["id"])
    if b.get("cart_id"):
        c = conn.execute("SELECT name FROM carts WHERE id=?", (b["cart_id"],)).fetchone()
        out["cart"] = c["name"] if c else None
    if with_names:
        u = conn.execute("SELECT display_name FROM users WHERE id=?", (b["user_id"],)).fetchone()
        out["user"] = u["display_name"] if u else None
    return out


@app.get("/api/my/batches")
def my_batches(user=User):
    """I miei movimenti: carichi e uscite degli ultimi 30 giorni, con i minuti rimasti per modificarli."""
    with db() as conn:
        mins = int(get_settings(conn).get("edit_minutes") or 30)
        out = []
        for b in conn.execute("SELECT * FROM batches WHERE user_id=? AND type IN ('carico','vendita','scarico') "
                              "AND ts >= ? ORDER BY ts DESC LIMIT 100",
                              (user["id"], (now() - timedelta(days=30)).isoformat(sep=" "))).fetchall():
            o = _batch_out(conn, dict(b), False)
            o["editable"], o["minutes_left"] = stock.editable(dict(b), user, mins)
            out.append(o)
        return {"edit_minutes": mins, "items": out}


@app.get("/api/batches/{bid}/edit")
def batch_edit_data(bid: str, user=User):
    with db() as conn:
        b = conn.execute("SELECT * FROM batches WHERE id=?", (bid,)).fetchone()
        if b is None:
            raise HTTPException(404, "Documento non trovato")
        mins = int(get_settings(conn).get("edit_minutes") or 30)
        ok, left = stock.editable(dict(b), user, mins)
        if not ok:
            raise HTTPException(403, f"Puoi modificare solo i tuoi movimenti nei primi {mins} minuti")
        out = {"id": bid, "type": b["type"], "minutes_left": left, "note": b["note"]}
        if b["type"] == "carico":
            out["cart"] = conn.execute("SELECT name, note FROM carts WHERE id=?", (b["cart_id"],)).fetchone()["name"]
            out["note"] = conn.execute("SELECT note FROM carts WHERE id=?", (b["cart_id"],)).fetchone()["note"]
            out["rows"] = rows(conn.execute(
                "SELECT l.id AS lot_id, l.product_id, p.name, l.qty_in AS qty, l.qty_in - l.qty AS used, l.expiry, "
                "l.expiry_estimated FROM lots l JOIN products p ON p.id=l.product_id WHERE l.cart_id=? AND l.qty_in > 0 "
                "ORDER BY l.id", (b["cart_id"],)))
        else:
            out["rows"] = [{"product_id": r["product_id"], "name": r["name"], "qty": -r["qty"]}
                           for r in stock.batch_lines(conn, bid)]
        return out


@app.put("/api/batches/{bid}")
def batch_edit(bid: str, body: dict = Body(...), user=User):
    """Modifica di un proprio carico o uscita entro N minuti. Il documento intero non si elimina."""
    with tx() as conn:
        b = conn.execute("SELECT * FROM batches WHERE id=?", (bid,)).fetchone()
        if b is None:
            raise HTTPException(404, "Documento non trovato")
        b = dict(b)
        mins = int(get_settings(conn).get("edit_minutes") or 30)
        if not stock.editable(b, user, mins)[0]:
            raise HTTPException(403, f"Sono passati più di {mins} minuti: chiedi al coordinatore")
        if b["type"] == "carico":
            changes = stock.edit_carico(conn, user, b, body.get("rows") or [], body.get("note"))
            what = "carico lotto " + conn.execute("SELECT name FROM carts WHERE id=?", (b["cart_id"],)).fetchone()["name"]
        else:
            changes = stock.edit_uscita(conn, user, b, body.get("rows") or [])
            what = ("vendita " if b["type"] == "vendita" else "scarico ") + b["ts"][11:16]
        if changes:
            conn.execute("UPDATE batches SET edited_at=? WHERE id=?", (now_s(), bid))
            audit.log(user, "correzioni", f"Modifica {what} (entro {mins} min)", " · ".join(changes), conn=conn)
    return {"ok": True, "changes": changes}


@app.get("/api/history")
def history(type: str | None = None, product_id: int | None = None, date_from: str | None = None,
            date_to: str | None = None, limit: int = 100, offset: int = 0, user=User):
    """Storico movimenti senza i nomi di chi li ha fatti (i nomi sono nel log dell'admin)."""
    require(user, "coordinatore")
    q, a = ["1=1"], []
    if type:
        q.append("b.type = ?")
        a.append(type)
    if product_id:
        q.append("b.id IN (SELECT batch FROM movements WHERE product_id = ?)")
        a.append(product_id)
    if date_from:
        q.append("b.ts >= ?")
        a.append(date_from)
    if date_to:
        q.append("b.ts < date(?, '+1 day')")
        a.append(date_to)
    with db() as conn:
        bs = conn.execute(f"SELECT b.* FROM batches b WHERE {' AND '.join(q)} ORDER BY b.ts DESC LIMIT ? OFFSET ?",
                          (*a, max(1, min(limit, 500)), offset)).fetchall()
        return {"items": [_batch_out(conn, dict(b), False) for b in bs]}


# =================================================================== lotti (carrelli)

@app.get("/api/carts")
def carts(status: str = "attivi", q: str | None = None, estimated: bool = False, user=User):
    require(user, "coordinatore")
    with db() as conn:
        warn = int(get_settings(conn).get("expiry_warning_days") or 14)
        lim, td = (today() + timedelta(days=warn)).isoformat(), today().isoformat()
        out = []
        for c in conn.execute("SELECT c.*, u.display_name AS created_by_name FROM carts c "
                              "LEFT JOIN users u ON u.id=c.created_by ORDER BY c.date DESC, c.id DESC").fetchall():
            ls = rows(conn.execute("SELECT l.*, p.name FROM lots l JOIN products p ON p.id=l.product_id "
                                   "WHERE l.cart_id=? AND l.qty_in > 0 ORDER BY l.id", (c["id"],)))
            if not ls:
                continue
            res = sum(l["qty"] for l in ls)
            exp_lines = [l for l in ls if l["qty"] > 0 and l["expiry"]]
            state = ("esaurito" if res == 0 else "scaduto" if any(l["expiry"] < td for l in exp_lines)
                     else "in_scadenza" if any(l["expiry"] <= lim for l in exp_lines) else "attivo")
            if status == "attivi" and state == "esaurito":
                continue
            if status == "esauriti" and state != "esaurito":
                continue
            if status == "scad" and state not in ("in_scadenza", "scaduto"):
                continue
            if estimated and not any(l["expiry_estimated"] and l["qty"] > 0 for l in ls):
                continue
            if q:
                ql = q.lower()
                if ql not in c["name"].lower() and ql not in (c["note"] or "").lower() and not any(ql in l["name"].lower() for l in ls):
                    continue
            first = min(exp_lines, key=lambda l: l["expiry"]) if exp_lines else None
            out.append({"id": c["id"], "name": c["name"], "date": c["date"], "kind": c["kind"], "note": c["note"],
                        "created_by": c["created_by_name"], "state": state, "qty_in": sum(l["qty_in"] for l in ls),
                        "qty": res, "products": [l["name"] for l in ls],
                        "first_expiry": first["expiry"] if first else None,
                        "first_expiry_estimated": bool(first and first["expiry_estimated"])})
        return {"warning_days": warn, "items": out}


@app.get("/api/carts/{cid}")
def cart_detail(cid: int, user=User):
    require(user, "coordinatore")
    with db() as conn:
        c = conn.execute("SELECT c.*, u.display_name AS created_by_name FROM carts c LEFT JOIN users u ON u.id=c.created_by "
                         "WHERE c.id=?", (cid,)).fetchone()
        if c is None:
            raise HTTPException(404, "Lotto non trovato")
        out = dict(c)
        out["lines"] = rows(conn.execute("SELECT l.*, p.name FROM lots l JOIN products p ON p.id=l.product_id "
                                         "WHERE l.cart_id=? AND l.qty_in > 0 ORDER BY l.id", (cid,)))
        for l in out["lines"]:
            l["history"] = rows(conn.execute(
                "SELECT m.ts, m.type, ml.qty, m.reason FROM movement_lots ml JOIN movements m ON m.id = ml.movement_id "
                "WHERE ml.lot_id=? ORDER BY m.id", (l["id"],)))
        return out


@app.put("/api/carts/{cid}")
def cart_note(cid: int, body: dict = Body(...), user=User):
    require(user, "coordinatore")
    with db() as conn:
        c = conn.execute("SELECT name, note FROM carts WHERE id=?", (cid,)).fetchone()
        if c is None:
            raise HTTPException(404, "Lotto non trovato")
        note = _str(body.get("note"), "nota", 300)
        conn.execute("UPDATE carts SET note=? WHERE id=?", (note, cid))
        audit.log(user, "correzioni", f"Nota del lotto {c['name']}", f"{c['note'] or '—'} → {note or '—'}", conn=conn)
    return {"ok": True}


@app.put("/api/lots/{lot_id}")
def lot_expiry(lot_id: int, body: dict = Body(...), user=User):
    require(user, "coordinatore")
    with db() as conn:
        l = conn.execute("SELECT l.*, p.name, c.name AS cart FROM lots l JOIN products p ON p.id=l.product_id "
                         "JOIN carts c ON c.id=l.cart_id WHERE l.id=?", (lot_id,)).fetchone()
        if l is None:
            raise HTTPException(404, "Riga non trovata")
        exp = stock.valid_date(body.get("expiry"), l["name"])
        conn.execute("UPDATE lots SET expiry=?, expiry_estimated=0 WHERE id=?", (exp, lot_id))
        audit.log(user, "correzioni", "Scadenza corretta",
                  f"lotto {l['cart']} · {l['name']} {l['expiry'] or '—'}{' (stimata)' if l['expiry_estimated'] else ''} → {exp or '—'}",
                  conn=conn)
    return {"ok": True}


@app.post("/api/lots/{lot_id}/adjust")
def lot_adjust(lot_id: int, body: dict = Body(...), user=User):
    require(user, "coordinatore")
    reason = _str(body.get("reason"), "motivo", 120, required=True)
    with tx() as conn:
        info = conn.execute("SELECT p.name, c.name AS cart FROM lots l JOIN products p ON p.id=l.product_id "
                            "JOIN carts c ON c.id=l.cart_id WHERE l.id=?", (lot_id,)).fetchone()
        if info is None:
            raise HTTPException(404, "Riga non trovata")
        bid = stock.new_batch(conn, "correzione", user["id"], reason=f"{reason} (lotto {info['cart']})")
        _, old, diff = stock.adjust_lot(conn, user["id"], lot_id, body.get("qty"), bid, reason)
        audit.log(user, "correzioni", "Correzione riga di lotto",
                  f"lotto {info['cart']} · {info['name']} {old} → {old + diff} · {reason}", conn=conn)
    return {"batch": bid}


@app.post("/api/lots/{lot_id}/toss")
def lot_toss(lot_id: int, user=User):
    """Scarica tutto il residuo di una riga di lotto come scaduto."""
    require(user, "coordinatore")
    with tx() as conn:
        l = conn.execute("SELECT l.product_id, l.qty, p.name, c.name AS cart FROM lots l JOIN products p ON p.id=l.product_id "
                         "JOIN carts c ON c.id=l.cart_id WHERE l.id=?", (lot_id,)).fetchone()
        if l is None or l["qty"] <= 0:
            raise HTTPException(409, "Riga vuota o inesistente")
        res = stock.scarico(conn, user["id"], "scarico", [{"product_id": l["product_id"], "qty": l["qty"], "lot_id": lot_id}],
                            "Scaduto", f"lotto {l['cart']}")
        audit.log(user, "movimenti", "Scarico (Scaduto)", f"{l['name']} −{l['qty']} dal lotto {l['cart']}", conn=conn)
    return {"batch": res["batch"]}


@app.get("/api/expiring")
def expiring(user=User):
    with db() as conn:
        warn = int(get_settings(conn).get("expiry_warning_days") or 14)
        return {"warning_days": warn, "items": rows(conn.execute(
            "SELECT l.id, l.qty, l.expiry, l.expiry_estimated, l.cart_id, p.name, p.id AS product_id, c.name AS cart, "
            "CAST(julianday(l.expiry) - julianday(?) AS INTEGER) AS days_left FROM lots l "
            "JOIN products p ON p.id=l.product_id JOIN carts c ON c.id=l.cart_id "
            "WHERE l.qty>0 AND l.expiry IS NOT NULL AND l.expiry <= ? AND p.status != 'archiviato' ORDER BY l.expiry",
            (today().isoformat(), (today() + timedelta(days=warn)).isoformat())))}


# =================================================================== correggi quantità e fare inventario

@app.post("/api/correggi")
def correggi(body: dict = Body(...), user=User):
    require(user, "coordinatore")
    pid = _int(body.get("product_id"), "prodotto", 1)
    target = _int(body.get("stock"), "quantità reale")
    reason = _str(body.get("reason"), "motivo", 120, required=True)
    with tx() as conn:
        bid = stock.new_batch(conn, "correzione", user["id"], reason=reason)
        ch = stock.set_stock(conn, user["id"], pid, target, "correzione", bid, reason, body.get("target"), {})
        if ch is None:
            raise HTTPException(409, "La quantità è già quella indicata")
        name = conn.execute("SELECT name FROM products WHERE id=?", (pid,)).fetchone()["name"]
        audit.log(user, "correzioni", "Correggi quantità",
                  f"{name} {ch[1]} → {ch[2]}" + (f" in lotto {ch[3]}" if ch[3] else "") + f" · {reason}", conn=conn)
    return {"batch": bid}


def _inventory(conn, inv_id: int) -> dict:
    inv = conn.execute("SELECT i.*, u.display_name AS started_by_name FROM inventories i "
                       "LEFT JOIN users u ON u.id=i.started_by WHERE i.id=?", (inv_id,)).fetchone()
    if inv is None:
        raise HTTPException(404, "Inventario non trovato")
    out = dict(inv)
    out["lines"] = rows(conn.execute(
        "SELECT p.id AS product_id, p.name, c.name AS category, p.stock, l.counted, l.target, l.expected "
        "FROM products p LEFT JOIN categories c ON c.id=p.category_id "
        "LEFT JOIN inventory_lines l ON l.product_id=p.id AND l.inventory_id=? "
        "WHERE p.status = 'attivo' OR l.inventory_id IS NOT NULL ORDER BY COALESCE(c.sort,999), c.name, p.sort, p.name",
        (inv_id,)))
    return out


@app.get("/api/inventory/current")
def inventory_current(user=User):
    with db() as conn:
        r = conn.execute("SELECT id FROM inventories WHERE status='aperto'").fetchone()
        return _inventory(conn, r["id"]) if r else None


@app.get("/api/inventory")
def inventory_list(user=User):
    require(user, "coordinatore")
    with db() as conn:
        return rows(conn.execute(
            "SELECT i.*, (SELECT COUNT(*) FROM inventory_lines l WHERE l.inventory_id=i.id AND l.counted IS NOT NULL) AS counted, "
            "(SELECT COUNT(*) FROM movements m WHERE m.batch=i.batch) AS differences "
            "FROM inventories i WHERE status='chiuso' ORDER BY i.id DESC LIMIT 50"))


@app.post("/api/inventory/start")
def inventory_start(user=User):
    require(user, "coordinatore")
    with tx() as conn:
        if conn.execute("SELECT 1 FROM inventories WHERE status='aperto'").fetchone():
            raise HTTPException(409, "C'è già un inventario in corso")
        iid = conn.execute("INSERT INTO inventories(started_at, started_by, status) VALUES (?,?,'aperto')",
                           (now_s(), user["id"])).lastrowid
        audit.log(user, "correzioni", "Inventario iniziato", conn=conn)
        return {"id": iid}


@app.put("/api/inventory/{inv_id}/line")
def inventory_line(inv_id: int, body: dict = Body(...), user=User):
    pid = _int(body.get("product_id"), "prodotto", 1)
    counted = _int(body.get("counted"), "quantità contata", allow_none=True)
    target = _str(body.get("target"), "lotto", 20)
    with tx() as conn:
        inv = conn.execute("SELECT status FROM inventories WHERE id=?", (inv_id,)).fetchone()
        if inv is None or inv["status"] != "aperto":
            raise HTTPException(409, "Inventario non in corso")
        stock.get_product(conn, pid)
        if target and target != "nuovo" and not is_staff(user):
            target = None
        conn.execute(
            "INSERT INTO inventory_lines(inventory_id, product_id, counted, target, counted_by, counted_at) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(inventory_id, product_id) DO UPDATE SET counted=excluded.counted, "
            "target=COALESCE(excluded.target, inventory_lines.target), counted_by=excluded.counted_by, counted_at=excluded.counted_at",
            (inv_id, pid, counted, target, user["id"], now_s()))
    return {"ok": True}


@app.post("/api/inventory/{inv_id}/close")
def inventory_close(inv_id: int, user=User):
    require(user, "coordinatore")
    det = []
    with tx() as conn:
        inv = conn.execute("SELECT * FROM inventories WHERE id=?", (inv_id,)).fetchone()
        if inv is None or inv["status"] != "aperto":
            raise HTTPException(409, "Inventario non in corso")
        bid = stock.new_batch(conn, "inventario", user["id"], reason="Inventario")
        cache: dict = {}
        lines = conn.execute("SELECT l.product_id, l.counted, l.target, p.stock, p.name FROM inventory_lines l "
                             "JOIN products p ON p.id=l.product_id WHERE l.inventory_id=? AND l.counted IS NOT NULL "
                             "AND p.status='attivo'", (inv_id,)).fetchall()
        for l in lines:
            conn.execute("UPDATE inventory_lines SET expected=? WHERE inventory_id=? AND product_id=?",
                         (l["stock"], inv_id, l["product_id"]))
            ch = stock.set_stock(conn, user["id"], l["product_id"], l["counted"], "inventario", bid, "Inventario", l["target"], cache)
            if ch:
                d = ch[2] - ch[1]
                det.append(f"{l['name']} {'+' if d > 0 else ''}{d}" + (f" in {ch[3]}" if ch[3] else ""))
        conn.execute("UPDATE inventories SET status='chiuso', closed_at=?, closed_by=?, batch=? WHERE id=?",
                     (now_s(), user["id"], bid, inv_id))
        audit.log(user, "correzioni", "Inventario chiuso", f"{len(lines)} contati, {len(det)} differenze: " + ", ".join(det), conn=conn)
    return {"ok": True, "counted": len(lines), "differences": len(det)}


@app.post("/api/inventory/{inv_id}/cancel")
def inventory_cancel(inv_id: int, user=User):
    require(user, "coordinatore")
    with db() as conn:
        conn.execute("UPDATE inventories SET status='annullato', closed_at=?, closed_by=? WHERE id=? AND status='aperto'",
                     (now_s(), user["id"], inv_id))
        audit.log(user, "correzioni", "Inventario annullato", conn=conn)
    return {"ok": True}


# =================================================================== prospetto

@app.get("/api/projection")
def get_projection(horizon: str = "fine_mese", user=User):
    require(user, "coordinatore")
    with db() as conn:
        return projection.compute(conn, horizon)


@app.get("/api/monthly")
def get_monthly(months: int = 6, user=User):
    require(user, "coordinatore")
    with db() as conn:
        return projection.monthly(conn, months)


# =================================================================== lista della spesa

def _shop_items(conn) -> list[dict]:
    return rows(conn.execute(
        "SELECT s.*, p.name AS product FROM shop_items s LEFT JOIN products p ON p.id = s.product_id ORDER BY s.sort, s.id"))


@app.get("/api/shop")
def shop(user=User):
    with db() as conn:
        items = _shop_items(conn)
        out = {"items": items}
        if is_staff(user):
            inlist = {i["product_id"] for i in items if i["product_id"]}
            pr = projection.compute(conn, "fine_mese")
            out["suggestions"] = [{"product_id": i["product_id"], "name": i["name"], "qty": i["suggested"],
                                   "status": i["status"], "stockout_date": i["stockout_date"]}
                                  for i in pr["items"] if i["suggested"] > 0 and i["product_id"] not in inlist]
        return out


@app.post("/api/shop/items")
def shop_add(body: dict = Body(...), user=User):
    require(user, "coordinatore")
    pid = _int(body.get("product_id"), "prodotto", 1, allow_none=True)
    text = _str(body.get("text"), "voce", 80)
    qty = _str(body.get("qty"), "quantità", 30)
    with db() as conn:
        if pid:
            p = stock.get_product(conn, pid)
            if conn.execute("SELECT 1 FROM shop_items WHERE product_id=?", (pid,)).fetchone():
                raise HTTPException(409, f"{p['name']} è già nella lista")
            label = p["name"]
        elif text:
            label = text
        else:
            raise HTTPException(400, "Scegli un prodotto o scrivi cosa comprare")
        iid = conn.execute("INSERT INTO shop_items(product_id, text, qty_text, added_by, added_at, sort) VALUES (?,?,?,?,?,"
                           "(SELECT COALESCE(MAX(sort),0)+1 FROM shop_items))",
                           (pid, None if pid else text, qty, user["id"], now_s())).lastrowid
        audit.log(user, "liste", "Aggiunto alla lista" + ("" if pid else " (voce libera)"), f"{label}{' · ' + qty if qty else ''}", conn=conn)
        return {"id": iid}


@app.put("/api/shop/items/{iid}")
def shop_update(iid: int, body: dict = Body(...), user=User):
    require(user, "coordinatore")
    with db() as conn:
        conn.execute("UPDATE shop_items SET qty_text=? WHERE id=?", (_str(body.get("qty"), "quantità", 30), iid))
    return {"ok": True}


@app.delete("/api/shop/items/{iid}")
def shop_delete(iid: int, user=User):
    require(user, "coordinatore")
    with db() as conn:
        it = conn.execute("SELECT s.text, p.name FROM shop_items s LEFT JOIN products p ON p.id=s.product_id WHERE s.id=?", (iid,)).fetchone()
        conn.execute("DELETE FROM shop_items WHERE id=?", (iid,))
        if it:
            audit.log(user, "liste", "Tolto dalla lista", it["name"] or it["text"], conn=conn)
    return {"ok": True}


@app.post("/api/shop/items/{iid}/toggle")
def shop_toggle(iid: int, user=User):
    with db() as conn:
        it = conn.execute("SELECT checked FROM shop_items WHERE id=?", (iid,)).fetchone()
        if it is None:
            raise HTTPException(404, "Voce non trovata")
        conn.execute("UPDATE shop_items SET checked=?, checked_by=? WHERE id=?", (0 if it["checked"] else 1, user["id"], iid))
    return {"ok": True}


@app.post("/api/shop/close")
def shop_close(user=User):
    """Chiude la lista: va in archivio, le voci non prese non vengono riportate, i coordinatori ricevono un avviso."""
    with tx() as conn:
        items = _shop_items(conn)
        if not items:
            raise HTTPException(409, "Non c'è nessuna lista aperta")
        snap = [{"label": i["product"] or i["text"], "qty": i["qty_text"], "checked": bool(i["checked"])} for i in items]
        conn.execute("INSERT INTO shop_lists(closed_at, closed_by, items) VALUES (?,?,?)",
                     (now_s(), user["id"], json.dumps(snap, ensure_ascii=False)))
        conn.execute("DELETE FROM shop_items")
        taken = sum(1 for s in snap if s["checked"])
        audit.log(user, "liste", "Lista della spesa chiusa", f"{taken} di {len(snap)} voci prese", conn=conn)
    notify.list_closed(user["display_name"], taken, len(snap), [s["label"] for s in snap if not s["checked"]])
    return {"ok": True, "taken": taken, "total": len(snap)}


@app.get("/api/shop/archive")
def shop_archive(user=User):
    require(user, "coordinatore")
    with db() as conn:
        out = rows(conn.execute("SELECT s.*, u.display_name AS closed_by_name FROM shop_lists s "
                                "LEFT JOIN users u ON u.id=s.closed_by ORDER BY s.id DESC LIMIT 100"))
    for o in out:
        o["items"] = json.loads(o["items"])
    return out


# =================================================================== utenti e dispositivi (solo admin)

@app.get("/api/users")
def users(user=User):
    require(user, "admin")
    with db() as conn:
        return rows(conn.execute(
            "SELECT u.id, u.username, u.display_name, u.role, u.builtin, u.active, u.last_login, u.locked_until, "
            "u.created_at, (SELECT COUNT(*) FROM sessions s WHERE s.user_id=u.id AND s.expires_at >= ?) AS devices "
            "FROM users u ORDER BY u.builtin DESC, u.active DESC, u.display_name", (now_s(),)))


@app.post("/api/users")
def create_user(body: dict = Body(...), user=User):
    require(user, "admin")
    username = (_str(body.get("username"), "nome utente", 32, True) or "").lower()
    if not re.fullmatch(r"[a-z0-9._-]{2,32}", username):
        raise HTTPException(400, "Nome utente: solo lettere minuscole, numeri, punto, trattino")
    role = body.get("role")
    if role not in ("coordinatore", "operatore"):
        raise HTTPException(400, "Ruolo non valido")
    pin = str(body.get("pin") or "")
    if not auth.valid_pin(pin):
        raise HTTPException(400, "Il PIN deve avere da 4 a 8 cifre")
    name = _str(body.get("display_name"), "nome", 60, True)
    with db() as conn:
        try:
            uid = conn.execute("INSERT INTO users(username, display_name, role, secret_hash, created_at) VALUES (?,?,?,?,?)",
                               (username, name, role, auth.hash_secret(pin), now_s())).lastrowid
        except Exception:
            raise HTTPException(409, "Nome utente già usato")
        audit.log(user, "utenti", "Nuovo utente", f"{name} ({username}) · {role}", conn=conn)
        return {"id": uid}


@app.put("/api/users/{uid}")
def update_user(uid: int, body: dict = Body(...), user=User):
    require(user, "admin")
    with db() as conn:
        u = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if u is None:
            raise HTTPException(404, "Utente non trovato")
        f, acts = {}, []
        if u["builtin"] and any(k in body for k in ("role", "active", "pin")):
            raise HTTPException(409, "L'amministratore cambia la propria password da «Cambia password»")
        if "display_name" in body:
            f["display_name"] = _str(body.get("display_name"), "nome", 60, True)
            acts.append(f"nome {u['display_name']} → {f['display_name']}")
        if "role" in body:
            if body["role"] not in ("coordinatore", "operatore"):
                raise HTTPException(400, "Ruolo non valido")
            f["role"] = body["role"]
            acts.append(f"ruolo {u['role']} → {body['role']}")
        if "active" in body:
            f["active"] = 1 if body["active"] else 0
            acts.append("riattivato" if f["active"] else "disattivato")
        if body.get("pin"):
            if not auth.valid_pin(str(body["pin"])):
                raise HTTPException(400, "Il PIN deve avere da 4 a 8 cifre")
            f.update(secret_hash=auth.hash_secret(str(body["pin"])), failed=0, locked_until=None)
            acts.append("PIN reimpostato")
        if body.get("unlock"):
            f.update(failed=0, locked_until=None)
        if f:
            conn.execute(f"UPDATE users SET {', '.join(k + '=?' for k in f)} WHERE id=?", [*f.values(), uid])
        if "secret_hash" in f or f.get("active") == 0 or "role" in f:
            conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        if body.get("unlock"):
            audit.log(user, "accessi", "Utente sbloccato", u["display_name"], conn=conn)
        if acts:
            audit.log(user, "utenti", "Modifica utente " + u["display_name"], ", ".join(acts), conn=conn)
    return {"ok": True}


@app.get("/api/users/{uid}/sessions")
def user_sessions(uid: int, user=User):
    if uid != user["id"]:
        require(user, "admin")
    with db() as conn:
        out = rows(conn.execute("SELECT id, device_name, device, ip, created_at, last_seen FROM sessions "
                                "WHERE user_id=? AND expires_at >= ? ORDER BY last_seen DESC", (uid, now_s())))
    for s in out:
        s["current"] = s["id"] == user.get("session_id")
    return out


@app.delete("/api/sessions/{sid}")
def delete_session(sid: int, user=User):
    with db() as conn:
        s = conn.execute("SELECT s.user_id, s.device_name, u.display_name FROM sessions s JOIN users u ON u.id=s.user_id "
                         "WHERE s.id=?", (sid,)).fetchone()
        if s is None:
            raise HTTPException(404, "Dispositivo non trovato")
        if s["user_id"] != user["id"]:
            require(user, "admin")
        conn.execute("DELETE FROM sessions WHERE id=?", (sid,))
        audit.log(user, "accessi", "Dispositivo disconnesso", f"{s['display_name']} · {s['device_name']}", conn=conn)
    return {"ok": True}


@app.delete("/api/users/{uid}/sessions")
def delete_all_sessions(uid: int, user=User):
    if uid != user["id"]:
        require(user, "admin")
    with db() as conn:
        n = conn.execute("SELECT display_name FROM users WHERE id=?", (uid,)).fetchone()
        conn.execute("DELETE FROM sessions WHERE user_id=? AND id != ?", (uid, user.get("session_id") or -1))
        audit.log(user, "accessi", "Disconnesso da tutti i dispositivi", n["display_name"] if n else str(uid), conn=conn)
    return {"ok": True}


@app.post("/api/me/password")
def change_own_password(body: dict = Body(...), user=User):
    require(user, "admin")
    new = str(body.get("new") or "")
    if not auth.valid_password(new):
        raise HTTPException(400, "La nuova password deve avere almeno 8 caratteri")
    with db() as conn:
        u = conn.execute("SELECT secret_hash FROM users WHERE id=?", (user["id"],)).fetchone()
        if not auth.verify_secret(str(body.get("current") or ""), u["secret_hash"]):
            raise HTTPException(403, "Password attuale errata")
        conn.execute("UPDATE users SET secret_hash=? WHERE id=?", (auth.hash_secret(new), user["id"]))
        conn.execute("DELETE FROM sessions WHERE user_id=? AND id != ?", (user["id"], user.get("session_id") or -1))
        audit.log(user, "utenti", "Password amministratore cambiata", conn=conn)
    return {"ok": True}


# =================================================================== log (solo admin)

def _log_query(category, user_id, q, date_from, date_to):
    where, a = ["1=1"], []
    if category:
        where.append("category=?")
        a.append(category)
    if user_id:
        where.append("user_id=?")
        a.append(user_id)
    if q:
        where.append("(action LIKE ? OR detail LIKE ? OR user_name LIKE ? OR device LIKE ?)")
        a += [f"%{q}%"] * 4
    if date_from:
        where.append("ts >= ?")
        a.append(date_from)
    if date_to:
        where.append("ts < date(?, '+1 day')")
        a.append(date_to)
    return " AND ".join(where), a


@app.get("/api/log")
def get_log(category: str | None = None, user_id: int | None = None, q: str | None = None, date_from: str | None = None,
            date_to: str | None = None, limit: int = 200, offset: int = 0, user=User):
    require(user, "admin")
    w, a = _log_query(category, user_id, q, date_from, date_to)
    with db() as conn:
        items = rows(conn.execute(f"SELECT * FROM audit_log WHERE {w} ORDER BY id DESC LIMIT ? OFFSET ?",
                                  (*a, max(1, min(limit, 1000)), offset)))
        total = conn.execute(f"SELECT COUNT(*) FROM audit_log WHERE {w}", a).fetchone()[0]
    return {"total": total, "items": items, "categories": audit.CATEGORIES}


def _csv(rows_: list[dict], cols: list[tuple[str, str]], filename: str) -> Response:
    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf, delimiter=";")
    w.writerow([c[1] for c in cols])
    for r in rows_:
        w.writerow(["" if r.get(c[0]) is None else r.get(c[0]) for c in cols])
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/log.csv")
def log_csv(category: str | None = None, user_id: int | None = None, q: str | None = None, date_from: str | None = None,
            date_to: str | None = None, user=User):
    require(user, "admin")
    w, a = _log_query(category, user_id, q, date_from, date_to)
    with db() as conn:
        items = rows(conn.execute(f"SELECT * FROM audit_log WHERE {w} ORDER BY id DESC", a))
    return _csv(items, [("ts", "Quando"), ("user_name", "Chi"), ("device", "Dispositivo"), ("ip", "Indirizzo"),
                        ("category", "Tipo"), ("action", "Cosa"), ("detail", "Dettaglio"), ("before", "Prima"), ("after", "Dopo")],
                f"log_{today().isoformat()}.csv")


# =================================================================== impostazioni e notifiche

EDITABLE_SETTINGS = {
    "bar_name": "nome del bar", "window_days": "uscite medie (giorni)", "include_inventory_loss": "ammanchi come consumo",
    "expiry_warning_days": "avviso scadenze (giorni)", "ideal_period_days": "periodo scorta ideale", "photo_lookup": "foto online",
    "ntfy_server": "server ntfy", "ntfy_proposals": "notifica proposte", "ntfy_list_closed": "notifica lista chiusa",
    "ntfy_digest": "riepilogo settimanale", "digest_weekday": "giorno riepilogo", "digest_time": "ora riepilogo",
}


@app.get("/api/settings")
def read_settings(user=User):
    require(user, "coordinatore")
    with db() as conn:
        return get_settings(conn)


@app.put("/api/settings")
def put_settings(body: dict = Body(...), user=User):
    require(user, "coordinatore")
    if "digest_time" in body and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(body["digest_time"])):
        raise HTTPException(400, "Ora non valida (es. 15:00)")
    with db() as conn:
        st = get_settings(conn)
        before, after = {}, {}
        for k, v in body.items():
            if k in EDITABLE_SETTINGS and str(v) != st.get(k):
                set_setting(conn, k, str(v)[:120])
                before[EDITABLE_SETTINGS[k]], after[EDITABLE_SETTINGS[k]] = st.get(k), str(v)
        if after:
            audit.log(user, "impostazioni", "Impostazioni modificate", audit.diff(before, after), before, after, conn=conn)
        return get_settings(conn)


@app.post("/api/ntfy/test")
def ntfy_test(user=User):
    require(user, "coordinatore")
    notify.send(["coord", "admin"], "Prova notifiche Bar Oratorio", f"Notifica di prova inviata da {user['display_name']}.",
                ["white_check_mark"], wait=True)
    return {"ok": True}


@app.post("/api/ntfy/digest")
def ntfy_digest(user=User):
    require(user, "coordinatore")
    notify.send_digest(wait=True)
    return {"ok": True}


# =================================================================== export e import

@app.get("/api/export/giacenze.csv")
def export_stock(user=User):
    require(user, "coordinatore")
    with db() as conn:
        items = list_products(conn)
    for i in items:
        i["barcodes"] = " ".join(i["barcodes"])
    return _csv(items, [("name", "Prodotto"), ("category", "Categoria"), ("stock", "Giacenza"), ("min_stock", "Minimo"),
                        ("ideal_stock", "Ideale"), ("next_expiry", "Prossima scadenza"), ("barcodes", "Barcode"),
                        ("status", "Stato")], f"giacenze_{today().isoformat()}.csv")


@app.get("/api/export/movimenti.csv")
def export_movements(date_from: str | None = None, date_to: str | None = None, user=User):
    """Coordinatore: senza nomi. Admin: con i nomi."""
    require(user, "coordinatore")
    with db() as conn:
        items = rows(conn.execute(
            "SELECT m.ts, m.type, p.name AS product, m.qty, m.stock_after, m.reason, u.display_name AS user, m.batch "
            "FROM movements m JOIN products p ON p.id=m.product_id LEFT JOIN users u ON u.id=m.user_id "
            "WHERE m.ts >= ? AND m.ts < date(?, '+1 day') ORDER BY m.id",
            (date_from or "2000-01-01", date_to or "2999-12-31")))
    cols = [("ts", "Quando"), ("type", "Tipo"), ("product", "Prodotto"), ("qty", "Pezzi"), ("stock_after", "Giacenza dopo"),
            ("reason", "Motivo"), ("batch", "Documento")]
    if user["role"] == "admin":
        cols.insert(6, ("user", "Chi"))
    return _csv(items, cols, f"movimenti_{today().isoformat()}.csv")


@app.post("/api/import/products")
def import_products(body: dict = Body(...), user=User):
    """CSV (; o ,) con intestazione: nome;categoria;minimo;ideale;barcode (barcode multipli separati da spazio)."""
    require(user, "coordinatore")
    text = body.get("csv") or ""
    delim = ";" if text[:2000].count(";") >= text[:2000].count(",") else ","
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")), delimiter=delim)
    created, skipped, errors, new_ids = 0, 0, [], []
    with tx() as conn:
        cats = {r["name"].lower(): r["id"] for r in conn.execute("SELECT id, name FROM categories")}
        for n, r in enumerate(reader, start=2):
            r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
            name = r.get("nome") or r.get("name")
            if not name:
                continue
            if conn.execute("SELECT 1 FROM products WHERE name=? COLLATE NOCASE AND status!='archiviato'", (name,)).fetchone():
                skipped += 1
                continue
            cat = r.get("categoria") or ""
            cid = None
            if cat:
                cid = cats.get(cat.lower())
                if cid is None:
                    cid = conn.execute("INSERT INTO categories(name, sort) VALUES (?, 500)", (cat,)).lastrowid
                    cats[cat.lower()] = cid
            try:
                pid = conn.execute(
                    "INSERT INTO products(name, category_id, min_stock, ideal_stock, status, created_at, updated_at) "
                    "VALUES (?,?,?,?,'attivo',?,?)",
                    (name[:80], cid, int(float(r.get("minimo") or 0)), int(float(r["ideale"])) if r.get("ideale") else None,
                     now_s(), now_s())).lastrowid
            except ValueError:
                errors.append(f"riga {n}: numeri non validi")
                continue
            for c in (r.get("barcode") or "").split():
                try:
                    _add_barcodes(conn, pid, [c], user, pending=False)
                except HTTPException as e:
                    errors.append(f"riga {n}: {e.detail}")
            created += 1
            new_ids.append(pid)
        audit.log(user, "anagrafica", "Importazione prodotti da CSV", f"{created} creati, {skipped} già presenti", conn=conn)
    if new_ids:
        photos.fill_missing_async()
    return {"created": created, "skipped": skipped, "errors": errors[:30]}


# =================================================================== frontend

@app.get("/")
def index():
    with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as f:
        html = f.read().replace("__VERSION__", config.VERSION)
    return Response(html, media_type="text/html", headers={"Cache-Control": "no-cache"})


@app.get("/manifest.webmanifest")
def manifest():
    return JSONResponse({
        "name": "Bar Oratorio", "short_name": "Bar", "start_url": "./", "display": "standalone",
        "background_color": "#F2F4F3", "theme_color": "#1F6B52",
        "icons": [{"src": "static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "static/icon-512.png", "sizes": "512x512", "type": "image/png"}],
    }, media_type="application/manifest+json")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
