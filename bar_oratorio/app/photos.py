"""Foto prodotti: ricerca per barcode o per nome su più fonti, download e upload dal telefono.

Catena delle fonti
- per barcode: Open Food Facts API v2 -> UPCitemdb (trial, gratuito, 100 richieste/giorno)
- per nome:    OFF Search-a-licious -> OFF ricerca legacy (/cgi/search.pl) -> UPCitemdb

Una fonte che risponde 503/429/timeout viene messa "in pausa" per qualche minuto,
così le ricerche successive passano subito alla fonte dopo invece di aspettare.
"""
from __future__ import annotations

import base64
import ipaddress
import json
import logging
import os
import re
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .db import PHOTO_DIR, db, now_s

log = logging.getLogger("bar.photos")
UA = "BarOratorio/1.0 (Home Assistant add-on; inventario bar oratorio)"
OFF = "https://world.openfoodfacts.org"
OFF_SAL = "https://search.openfoodfacts.org/search"
UPC = "https://api.upcitemdb.com/prod/trial"
OFF_IMG_HOSTS = ("images.openfoodfacts.org", "static.openfoodfacts.org", "openfoodfacts-images.s3.eu-west-3.amazonaws.com")
MAX_BYTES = 3 * 1024 * 1024
OFF_FIELDS = ["code", "product_name", "product_name_it", "brands", "quantity", "image_front_url", "image_front_small_url",
              "packaging", "packaging_tags", "packaging_shapes_tags", "categories_tags"]

# ---------------------------------------------------------------- stato condiviso

_lock = threading.Lock()
_cooldown: dict[str, float] = {}           # fonte -> istante fino a cui è in pausa
_last_call: dict[str, float] = {}          # fonte -> ultima chiamata (per distanziare)
_MIN_GAP = {"off_legacy": 6.5, "off_sal": 1.0, "upc": 2.0}
_cache: dict[str, tuple[float, list[dict]]] = {}
_CACHE_TTL = 6 * 3600
_issued: dict[str, float] = {}             # URL immagine proposti da noi: scaricabili anche se non OFF
_ISSUED_TTL = 24 * 3600


class SourceDown(Exception):
    """La fonte non risponde o ci limita: va saltata per un po'."""


def _available(src: str) -> bool:
    return time.time() >= _cooldown.get(src, 0)


def _pause(src: str, seconds: int, why) -> None:
    _cooldown[src] = time.time() + seconds
    log.info("fonte %s in pausa per %d min: %s", src, seconds // 60, why)


def _gap(src: str) -> None:
    with _lock:
        wait = _MIN_GAP.get(src, 0) - (time.time() - _last_call.get(src, 0))
        if wait > 0:
            time.sleep(wait)
        _last_call[src] = time.time()


def _http_json(src: str, url: str, data: dict | None = None, timeout: int = 8) -> dict:
    """GET (o POST JSON) con gestione uniforme degli errori: 429/5xx/timeout -> SourceDown."""
    _gap(src)
    headers = {"User-Agent": UA, "Accept": "application/json"}
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read(2_000_000).decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            _pause(src, 60 * 60, e)                     # quota (UPCitemdb: giornaliera/oraria)
            raise SourceDown(e) from e
        if e.code >= 500:
            _pause(src, 10 * 60, e)
            raise SourceDown(e) from e
        raise                                             # 404 & co: risposta "vera", non un guasto
    except (urllib.error.URLError, TimeoutError, socket.timeout, json.JSONDecodeError) as e:
        _pause(src, 5 * 60, e)
        raise SourceDown(e) from e


def _issue(url: str | None) -> str | None:
    if url:
        now = time.time()
        _issued[url] = now
        if len(_issued) > 3000:
            for k, t in list(_issued.items()):
                if now - t > _ISSUED_TTL:
                    _issued.pop(k, None)
    return url


def _txt(v) -> str:
    """OFF a volte restituisce stringhe, a volte liste o dizionari per lingua."""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, list):
        return _txt(v[0]) if v else ""
    if isinstance(v, dict):
        for k in ("it", "main", "en"):
            if v.get(k):
                return _txt(v[k])
        return next((_txt(x) for x in v.values() if x), "")
    return ""


def _txt_all(v) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return " ".join(_txt_all(x) for x in v)
    if isinstance(v, dict):
        return " ".join(_txt_all(x) for x in v.values())
    return ""


def _name(prod: dict) -> str:
    return _txt(prod.get("product_name_it")) or _txt(prod.get("product_name"))


def _off_item(p: dict, source: str) -> dict | None:
    img = p.get("image_front_url") or p.get("image_url")
    if not img:
        return None
    return {
        "code": p.get("code"), "name": _name(p), "brand": _txt(p.get("brands")).split(",")[0].strip(),
        "quantity": _txt(p.get("quantity")), "source": source,
        "image_url": _issue(img), "thumb_url": _issue(p.get("image_front_small_url") or img),
        "_extra": " ".join(_txt_all(p.get(k)) for k in ("packaging", "packaging_tags", "packaging_shapes_tags", "categories_tags")).strip(),
    }


# ---------------------------------------------------------------- barcode

def _off_barcode(code: str) -> dict | None:
    d = _http_json("off_api", f"{OFF}/api/v2/product/{code}.json?fields=" + ",".join(OFF_FIELDS + ["image_url"]))
    if d.get("status") != 1:
        return None
    p = d.get("product") or {}
    p["code"] = code
    item = _off_item(p, "off")
    if item is None:  # prodotto noto ma senza foto: teniamo comunque nome e marca
        item = {"code": code, "name": _name(p), "brand": _txt(p.get("brands")).split(",")[0].strip(),
                "quantity": _txt(p.get("quantity")), "source": "off", "image_url": None, "thumb_url": None}
    return item


def _upc_item(it: dict) -> dict | None:
    imgs = [u for u in (it.get("images") or []) if isinstance(u, str) and u.startswith("https://")]
    if not imgs:
        return None
    return {"code": it.get("ean") or it.get("upc"), "name": (it.get("title") or "").strip(),
            "brand": (it.get("brand") or "").strip(), "quantity": (it.get("size") or "").strip(),
            "source": "upcitemdb", "image_url": _issue(imgs[0]), "thumb_url": _issue(imgs[0]),
            "_extra": " ".join(str(it.get(k) or "") for k in ("category", "description"))[:600].strip()}


def _upc_barcode(code: str) -> dict | None:
    try:
        d = _http_json("upc", f"{UPC}/lookup?upc={code}")
    except urllib.error.HTTPError:
        return None
    for it in d.get("items") or []:
        item = _upc_item(it)
        if item:
            item["code"] = code
            return item
    return None


def lookup_barcode(code: str) -> dict | None:
    """Dati del prodotto per barcode: nome, marca, formato, immagine (prima OFF, poi UPCitemdb)."""
    if not re.fullmatch(r"\d{6,14}", code or ""):
        return None
    found = None
    for src, fn in (("off_api", _off_barcode), ("upc", _upc_barcode)):
        if not _available(src):
            continue
        try:
            item = fn(code)
        except SourceDown:
            continue
        except Exception as e:
            log.info("barcode %s su %s: %s", code, src, e)
            continue
        if item and item.get("image_url"):
            return item
        found = found or item          # nome senza foto: lo teniamo se nessuno ha di meglio
    return found


# ---------------------------------------------------------------- ricerca per nome

def _off_sal(query: str, limit: int) -> list[dict]:
    d = _http_json("off_sal", OFF_SAL, {"q": query, "langs": ["it", "en"], "page_size": 30,
                                        "boost_phrase": True, "fields": OFF_FIELDS})
    if d.get("errors"):
        raise SourceDown(d["errors"])
    out, no_img = [], []
    for p in d.get("hits") or []:
        item = _off_item(p, "off")
        if item:
            out.append(item)
        elif p.get("code"):
            no_img.append(str(p["code"]))
        if len(out) >= limit:
            return out
    # L'indice di ricerca a volte non riporta l'immagine: la chiediamo all'API prodotto (max 6).
    for code in no_img[:3]:
        if len(out) >= limit or not _available("off_api"):
            break
        try:
            item = _off_barcode(code)
        except Exception:
            break
        if item and item.get("image_url"):
            out.append(item)
    return out


def _off_legacy(query: str, limit: int) -> list[dict]:
    q = urllib.parse.urlencode({
        "search_terms": query, "search_simple": 1, "action": "process", "json": 1,
        "page_size": 30, "sort_by": "unique_scans_n", "fields": ",".join(OFF_FIELDS),
    })
    d = _http_json("off_legacy", f"{OFF}/cgi/search.pl?{q}", timeout=10)
    out = []
    for p in d.get("products") or []:
        item = _off_item(p, "off")
        if item:
            out.append(item)
            if len(out) >= limit:
                break
    return out


def _upc_search(query: str, limit: int) -> list[dict]:
    q = urllib.parse.urlencode({"s": query, "match_mode": 0, "type": "product"})
    try:
        d = _http_json("upc", f"{UPC}/search?{q}")
    except urllib.error.HTTPError:
        return []
    out = []
    for it in d.get("items") or []:
        item = _upc_item(it)
        if item:
            out.append(item)
            if len(out) >= limit:
                break
    return out


SEARCH_CHAIN = (("off_sal", _off_sal), ("off_legacy", _off_legacy), ("upc", _upc_search))

# ---------------------------------------------------------------- formato e categoria

# Formati di confezione: parole che li indicano (nome del prodotto, categoria, dati OFF/UPC)
# e volumi tipici in ml, usati solo come indizio debole quando la confezione non è dichiarata.
FORMATS = {
    "lattina": {"words": ("lattina", "lattine", "latta", "can", "cans", "canette", "lata", "dosen",
                          "metal", "aluminium", "aluminum", "alluminio"), "ml": (300, 355)},
    "brick": {"words": ("brick", "brik", "bric", "tetra", "tetrapak", "tetra-pak", "cartone", "cartoncino", "carton",
                        "cardboard", "combibloc", "brique"), "ml": (100, 250)},
    "bottiglia": {"words": ("bottiglia", "bottigliette", "bottiglietta", "bottiglie", "bottle", "bottles", "pet",
                            "bouteille", "flasche"), "ml": (330, 2000)},
    "vetro": {"words": ("vetro", "glass", "verre"), "ml": None},
    "busta": {"words": ("busta", "bustina", "sacchetto", "pacchetto", "bag", "sachet", "packet"), "ml": None},
}
# vetro è un materiale: non è in conflitto con bottiglia
_COMPATIBLE = {("vetro", "bottiglia"), ("bottiglia", "vetro")}

# Categorie dell'app -> parole che compaiono nelle categorie OFF (en:...) o nei testi UPC
CATEGORY_HINTS = {
    "bibite": ("soda", "sodas", "soft-drink", "carbonated", "beverages", "drink", "cola", "boissons"),
    "acqua": ("water", "waters", "eaux", "mineral"),
    "succhi": ("juice", "juices", "nectar", "tea", "teas", "iced", "beverages", "drink"),
    "te": ("tea", "teas", "iced", "beverages"),
    "snack dolci": ("snacks", "sweet", "biscuits", "chocolate", "candies", "confectioner", "bars", "wafer", "cakes"),
    "snack salati": ("snacks", "salty", "chips", "crisps", "crackers", "appetizers", "popcorn", "pretzel"),
    "patatine": ("chips", "crisps", "potato"),
    "gelati": ("ice-cream", "ice-creams", "frozen", "sorbet", "glaces"),
    "caffetteria": ("coffee", "coffees", "cocoa", "capsules", "sugar", "milk"),
    "birra": ("beer", "beers", "bieres"),
}
_STOP = {"di", "da", "del", "della", "al", "alla", "e", "con", "gusto", "the", "-", "x", "pz", "pezzi"}


def _norm(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9.,]+", " ", s).strip()


def _tokens(s: str) -> set[str]:
    return set(_norm(s).replace(",", " ").split())


def _formats_in(text: str) -> set[str]:
    toks = _tokens(text)
    return {f for f, d in FORMATS.items() if toks & set(d["words"])}


def _ml(text: str) -> float | None:
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(ml|cl|dl|l|lt|litri?)\b", _norm(text))
    if not m:
        return None
    v = float(m.group(1).replace(",", "."))
    return v * {"ml": 1, "cl": 10, "dl": 100}.get(m.group(2), 1000)


def _query_terms(name: str) -> str:
    """Nome senza le parole di formato e i volumi: 'Estathe pesca - Brick' -> 'Estathe pesca'."""
    words = [w for w in re.split(r"[\s\-/()]+", name or "") if w]
    fmt_words = {w for d in FORMATS.values() for w in d["words"]}
    keep = [w for w in words if _norm(w) not in fmt_words and not re.fullmatch(r"\d+([.,]\d+)?(ml|cl|l|lt|g|kg)?", _norm(w))]
    return " ".join(keep) or (name or "")


def _category_hints(category: str | None) -> set[str]:
    c = _norm(category or "")
    hints: set[str] = set()
    toks = set(c.split())
    for key, words in CATEGORY_HINTS.items():
        if (" " in key and key in c) or (" " not in key and key in toks):
            hints.update(words)
    return hints


def _score(item: dict, q_tokens: set[str], target: set[str], cat_hints: set[str], want_ml: float | None) -> tuple[float, bool, bool]:
    """Restituisce (punteggio, formato confermato, formato in conflitto)."""
    text = " ".join(str(item.get(k) or "") for k in ("name", "brand", "quantity", "_extra"))
    toks = _tokens(text)
    name_toks = _tokens(item.get("name") or "") | _tokens(item.get("brand") or "")
    score = 2.0 * len(q_tokens & name_toks) - 2.5 * len(q_tokens - toks)   # ogni parola cercata che manca pesa
    confirmed = conflict = False
    ml = _ml(item.get("quantity") or "") or _ml(item.get("name") or "")
    if target:
        found = _formats_in(text)
        if found & target:
            score += 8
            confirmed = True
        elif found and not any((f, t) in _COMPATIBLE for f in found for t in target):
            score -= 10
            conflict = True
        elif ml is not None:
            for t in target:
                rng = FORMATS[t]["ml"]
                if rng and rng[0] <= ml <= rng[1]:
                    score += 3
                    confirmed = True
                elif rng:
                    score -= 3
    if want_ml and ml:
        score += 3 if abs(ml - want_ml) <= want_ml * 0.08 else -2
    if cat_hints and item.get("_extra"):
        score += 2 if toks & cat_hints else -3   # categoria dichiarata ma di altro tipo (es. yogurt per un tè)
    return score, confirmed, conflict


def _rank(items: list[dict], query: str, target: set[str], cat_hints: set[str], want_ml: float | None) -> list[dict]:
    q_tokens = _tokens(query) - _STOP
    seen, ranked = set(), []
    for it in items:
        if it["image_url"] in seen:
            continue
        seen.add(it["image_url"])
        sc, ok, bad = _score(it, q_tokens, target, cat_hints, want_ml)
        ranked.append((sc, ok, bad, it))
    ranked.sort(key=lambda r: -r[0])
    best = ranked[0][0] if ranked else 0
    out = []
    for sc, ok, bad, it in ranked:
        if sc <= 0 < best:   # c'è qualcosa di pertinente: nascondo i risultati fuori tema
            continue
        if bad:            # formato diverso dichiarato (es. lattina quando cerchi il brick): scartato
            continue
        it = {k: v for k, v in it.items() if not k.startswith("_")}
        it["format_ok"] = ok if target else None
        it["score"] = round(sc, 1)
        out.append(it)
    return out


def search(query: str, limit: int = 12, category: str | None = None) -> list[dict]:
    """Ricerca per nome che tiene conto del formato (lattina, brick, bottiglia…) e della categoria.

    Il formato si legge dal nome del prodotto e dalla categoria; le parole di formato vengono tolte
    dalla ricerca testuale (le banche dati raramente le hanno nel nome) e usate per ordinare e
    filtrare i risultati. Le fonti si interrogano in ordine finché non c'è almeno una foto con il
    formato confermato; se nessuna lo conferma restano le foto senza formato dichiarato.
    """
    query = re.sub(r"\s+", " ", (query or "").strip())
    if len(query) < 2:
        return []
    target = _formats_in(query) | _formats_in(category or "")
    want_ml = _ml(query)
    terms = _query_terms(query)
    hints = _category_hints(category)
    key = f"{terms.lower()}|{','.join(sorted(target))}|{_norm(category or '')}|{want_ml}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1][:limit]
    pool: list[dict] = []
    for src, fn in SEARCH_CHAIN:
        if not _available(src):
            continue
        try:
            pool += fn(terms, 30)
        except SourceDown:
            continue
        except Exception as e:
            log.info("ricerca %r su %s: %s", terms, src, e)
            continue
        ranked = _rank(pool, terms, target, hints, want_ml)
        if ranked and (not target or any(r["format_ok"] for r in ranked)):
            break
    ranked = _rank(pool, terms, target, hints, want_ml)
    if ranked:
        _cache[key] = (time.time(), ranked)
    else:
        log.info("ricerca %r (%s): nessuna foto adatta", terms, "/".join(sorted(target)) or "formato libero")
    return ranked[:limit]


def sources_status() -> dict:
    now = time.time()
    return {s: (max(0, int(_cooldown.get(s, 0) - now)) or None) for s in ("off_api", "off_sal", "off_legacy", "upc")}


# ---------------------------------------------------------------- download e salvataggio

def _public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    for *_, sa in infos:
        ip = ipaddress.ip_address(sa[0])
        if not ip.is_global:
            return False
    return True


def _image_allowed(url: str) -> bool:
    u = urllib.parse.urlparse(url)
    if u.scheme != "https" or not u.hostname:
        return False
    if u.hostname in OFF_IMG_HOSTS:
        return True
    t = _issued.get(url)
    return bool(t and time.time() - t < _ISSUED_TTL and _public_host(u.hostname))


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        u = urllib.parse.urlparse(newurl)
        if u.scheme != "https" or not u.hostname or not _public_host(u.hostname):
            raise ValueError("Reindirizzamento dell'immagine non ammesso")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_SafeRedirect)


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
    if not _image_allowed(url or ""):
        raise ValueError("Immagine non ammessa: usa una foto proposta dalla ricerca")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "image/*"})
    with _opener.open(req, timeout=15) as r:
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
        p = conn.execute("SELECT p.name, p.photo, c.name AS category FROM products p "
                         "LEFT JOIN categories c ON c.id = p.category_id WHERE p.id=?", (pid,)).fetchone()
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
                download_to_product(pid, info["image_url"], f"{info.get('source', 'off')}-barcode")
                return True
            except Exception as e:
                log.info("foto barcode %s: %s", c, e)
    for r in search(p["name"], limit=3, category=p["category"]):
        if r.get("format_ok") is False or r.get("score", 0) <= 0:
            continue   # in automatico solo foto che corrispondono davvero; le altre si scelgono a mano
        try:
            download_to_product(pid, r["image_url"], f"{r.get('source', 'off')}-nome")
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


_fill_lock = threading.Lock()


def fill_missing_async() -> int:
    with db() as conn:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM products WHERE photo IS NULL AND status != 'archiviato'").fetchall()]

    def run():
        if not _fill_lock.acquire(blocking=False):   # un solo giro alla volta
            return
        try:
            for i in ids:
                _safe(auto_photo, i)
        finally:
            _fill_lock.release()
    threading.Thread(target=run, daemon=True).start()
    return len(ids)
