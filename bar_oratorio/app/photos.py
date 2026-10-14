"""Foto prodotti: ricerca su Open Food Facts (per barcode o per nome), download e upload dal telefono."""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import secrets
import threading
import time
import urllib.parse
import urllib.request

from .db import PHOTO_DIR, db, now_s

log = logging.getLogger("bar.photos")
UA = "BarOratorio/1.0 (Home Assistant add-on; inventario bar oratorio)"
OFF = "https://world.openfoodfacts.org"
ALLOWED_IMG_HOSTS = ("images.openfoodfacts.org", "static.openfoodfacts.org", "openfoodfacts-images.s3.eu-west-3.amazonaws.com")
MAX_BYTES = 3 * 1024 * 1024
_search_lock = threading.Lock()
_last_search = 0.0


def _get_json(url: str, timeout: int = 10) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(2_000_000).decode("utf-8", "replace"))


def _name(prod: dict) -> str:
    return (prod.get("product_name_it") or prod.get("product_name") or "").strip()


def lookup_barcode(code: str) -> dict | None:
    """Dati del prodotto su Open Food Facts: nome, marca, formato, immagine."""
    if not re.fullmatch(r"\d{6,14}", code or ""):
        return None
    try:
        d = _get_json(f"{OFF}/api/v2/product/{code}.json?fields=product_name,product_name_it,brands,quantity,"
                      f"image_front_url,image_front_small_url,image_url")
    except Exception as e:  # rete assente, 404, timeout...
        log.info("OFF barcode %s: %s", code, e)
        return None
    if d.get("status") != 1:
        return None
    p = d.get("product") or {}
    return {
        "code": code,
        "name": _name(p),
        "brand": (p.get("brands") or "").split(",")[0].strip(),
        "quantity": p.get("quantity") or "",
        "image_url": p.get("image_front_url") or p.get("image_url"),
        "thumb_url": p.get("image_front_small_url") or p.get("image_front_url"),
    }


def search(query: str, limit: int = 12) -> list[dict]:
    """Ricerca per nome (limite OFF ~10 ricerche/minuto: le serializziamo con una pausa)."""
    global _last_search
    query = (query or "").strip()
    if len(query) < 2:
        return []
    with _search_lock:
        wait = 6.5 - (time.time() - _last_search)
        if wait > 0:
            time.sleep(wait)
        _last_search = time.time()
        q = urllib.parse.urlencode({
            "search_terms": query, "search_simple": 1, "action": "process", "json": 1,
            "page_size": 30, "sort_by": "unique_scans_n",
            "fields": "code,product_name,product_name_it,brands,quantity,image_front_url,image_front_small_url",
        })
        try:
            d = _get_json(f"{OFF}/cgi/search.pl?{q}", timeout=15)
        except Exception as e:
            log.info("OFF search %r: %s", query, e)
            return []
    out = []
    for p in d.get("products") or []:
        if not p.get("image_front_url"):
            continue
        out.append({
            "code": p.get("code"), "name": _name(p), "brand": (p.get("brands") or "").split(",")[0].strip(),
            "quantity": p.get("quantity") or "",
            "image_url": p["image_front_url"], "thumb_url": p.get("image_front_small_url") or p["image_front_url"],
        })
        if len(out) >= limit:
            break
    return out


def _save(pid: int, data: bytes, ext: str, source: str) -> str:
    os.makedirs(PHOTO_DIR, exist_ok=True)
    fname = f"p{pid}_{secrets.token_hex(4)}.{ext}"
    with open(os.path.join(PHOTO_DIR, fname), "wb") as f:
        f.write(data)
    with db() as conn:
        old = conn.execute("SELECT photo FROM products WHERE id=?", (pid,)).fetchone()
        conn.execute("UPDATE products SET photo=?, photo_source=?, updated_at=? WHERE id=?", (fname, source, now_s(), pid))
    if old and old["photo"]:
        try:
            os.remove(os.path.join(PHOTO_DIR, old["photo"]))
        except OSError:
            pass
    return fname


def download_to_product(pid: int, url: str, source: str) -> str:
    u = urllib.parse.urlparse(url)
    if u.scheme != "https" or u.hostname not in ALLOWED_IMG_HOSTS:
        raise ValueError("Immagine non ammessa: solo da Open Food Facts")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15) as r:
        ctype = r.headers.get("Content-Type", "")
        if not ctype.startswith("image/"):
            raise ValueError("Il file non è un'immagine")
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("Immagine troppo grande")
    ext = "png" if "png" in ctype else "webp" if "webp" in ctype else "jpg"
    return _save(pid, data, ext, source)


def save_upload(pid: int, data_url: str) -> str:
    m = re.fullmatch(r"data:image/(jpeg|png|webp);base64,([A-Za-z0-9+/=\s]+)", data_url or "")
    if not m:
        raise ValueError("Formato immagine non valido")
    data = base64.b64decode(m.group(2))
    if len(data) > MAX_BYTES:
        raise ValueError("Immagine troppo grande")
    return _save(pid, data, "jpg" if m.group(1) == "jpeg" else m.group(1), "foto")


def auto_photo(pid: int) -> bool:
    """Prima foto automatica: per barcode (affidabile) o, se manca, primo risultato per nome."""
    with db() as conn:
        p = conn.execute("SELECT name, photo FROM products WHERE id=?", (pid,)).fetchone()
        if p is None or p["photo"]:
            return False
        codes = [r["code"] for r in conn.execute("SELECT code FROM barcodes WHERE product_id=?", (pid,))]
        enabled = (conn.execute("SELECT value FROM settings WHERE key='photo_lookup'").fetchone() or ["1"])[0] == "1"
    if not enabled:
        return False
    for c in codes:
        info = lookup_barcode(c)
        if info and info.get("image_url"):
            try:
                download_to_product(pid, info["image_url"], "off-barcode")
                return True
            except Exception as e:
                log.info("foto barcode %s: %s", c, e)
    res = search(p["name"], limit=1)
    if res:
        try:
            download_to_product(pid, res[0]["image_url"], "off-nome")
            return True
        except Exception as e:
            log.info("foto nome %s: %s", p["name"], e)
    return False


def auto_photo_async(pid: int) -> None:
    threading.Thread(target=lambda: _safe(auto_photo, pid), daemon=True).start()


def _safe(fn, *a):
    try:
        fn(*a)
    except Exception:
        log.exception("errore foto")


def fill_missing_async() -> int:
    with db() as conn:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM products WHERE photo IS NULL AND status != 'archiviato'").fetchall()]

    def run():
        for i in ids:
            _safe(auto_photo, i)
    threading.Thread(target=run, daemon=True).start()
    return len(ids)
