"""Autenticazione: hash di PIN/password, sessioni per dispositivo, blocco tentativi."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
from datetime import timedelta

from fastapi import HTTPException, Request

from . import audit, config
from .db import db, now, now_s

COOKIE = "bar_session"
MAX_FAILED = 5
LOCK_MINUTES = 15
GLOBAL_FAIL_LIMIT = 30          # tentativi falliti totali al minuto, oltre i quali si rallenta tutto

_glock = threading.Lock()
_global_fails: list[float] = []


# ---------- hash ----------

def hash_secret(secret: str) -> str:
    salt = secrets.token_bytes(16)
    it = 120_000
    dk = hashlib.pbkdf2_hmac("sha256", secret.encode(), salt, it)
    return f"pbkdf2${it}${salt.hex()}${dk.hex()}"


def verify_secret(secret: str, stored: str) -> bool:
    try:
        algo, it, salt, dk = stored.split("$")
        if algo != "pbkdf2":
            return False
        calc = hashlib.pbkdf2_hmac("sha256", secret.encode(), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(calc.hex(), dk)
    except Exception:
        return False


def valid_pin(pin: str) -> bool:
    return pin.isdigit() and 4 <= len(pin) <= 8


def valid_password(pw: str) -> bool:
    return isinstance(pw, str) and len(pw) >= 8


# ---------- bootstrap admin ----------

def ensure_builtin_admin() -> None:
    """L'amministratore (l'oratorio) entra con nome utente e password, mai in automatico da HA.
    Le opzioni dell'add-on danno nome e password iniziali; la password si cambia poi dall'app.
    Con l'opzione admin_reset attiva, al riavvio la password torna quella delle opzioni (recupero)."""
    name = (config.OPTIONS.get("admin_username") or "oratorio").strip().lower()
    pw = config.OPTIONS.get("admin_password") or ""
    reset = bool(config.OPTIONS.get("admin_reset"))
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE builtin = 1").fetchone()
        if row is None:
            if not valid_password(pw):
                raise SystemExit("Imposta admin_password (almeno 8 caratteri) nelle opzioni dell'add-on")
            conn.execute(
                "INSERT INTO users(username, display_name, role, secret_hash, builtin, created_at) "
                "VALUES (?, 'Oratorio', 'admin', ?, 1, ?)", (name, hash_secret(pw), now_s()))
            return
        if row["username"] != name:
            conn.execute("UPDATE users SET username=? WHERE id=?", (name, row["id"]))
        if reset and valid_password(pw):
            conn.execute("UPDATE users SET secret_hash=?, failed=0, locked_until=NULL, active=1 WHERE id=?",
                         (hash_secret(pw), row["id"]))
            conn.execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))


# ---------- login ----------

def _global_throttled() -> bool:
    with _glock:
        t = time.time()
        while _global_fails and _global_fails[0] < t - 60:
            _global_fails.pop(0)
        return len(_global_fails) >= GLOBAL_FAIL_LIMIT


def _note_global_fail() -> None:
    with _glock:
        _global_fails.append(time.time())


def describe_ua(ua: str) -> str:
    """Descrizione leggibile del dispositivo, es. 'Android · Chrome'."""
    ua = ua or ""
    os_ = ("iPhone" if "iPhone" in ua else "iPad" if "iPad" in ua else "Android" if "Android" in ua
           else "Windows" if "Windows" in ua else "Mac" if "Mac OS" in ua else "Linux" if "Linux" in ua else "?")
    br = ("Edge" if "Edg/" in ua else "Firefox" if "Firefox" in ua else "Chrome" if "Chrome" in ua
          else "Safari" if "Safari" in ua else "?")
    return f"{os_} · {br}"


def login(username: str, secret: str, user_agent: str | None, device_name: str | None = None,
          ip: str | None = None) -> tuple[str, dict]:
    if _global_throttled():
        raise HTTPException(429, "Troppi tentativi, riprova tra un minuto")
    username = (username or "").strip()
    secret = secret or ""
    with db() as conn:
        u = conn.execute("SELECT * FROM users WHERE username = ? AND active = 1", (username,)).fetchone()
        if u is None:
            _note_global_fail()
            time.sleep(0.4)
            raise HTTPException(401, "Utente o codice errati")
        if u["locked_until"] and u["locked_until"] > now_s():
            raise HTTPException(429, f"Utente bloccato per troppi tentativi fino alle {u['locked_until'][11:16]}")
        if not verify_secret(secret, u["secret_hash"]):
            failed = u["failed"] + 1
            locked = None
            if failed >= MAX_FAILED:
                locked = (now() + timedelta(minutes=LOCK_MINUTES)).isoformat(sep=" ")
                failed = 0
            conn.execute("UPDATE users SET failed=?, locked_until=? WHERE id=?", (failed, locked, u["id"]))
            what = "Password errata" if u["role"] == "admin" else "PIN errato"
            audit.log({"id": u["id"], "display_name": u["display_name"]}, "accessi",
                      "Utente bloccato" if locked else what,
                      f"{MAX_FAILED} tentativi sbagliati, bloccato per {LOCK_MINUTES} minuti" if locked
                      else f"tentativo {failed} di {MAX_FAILED}", conn=conn,
                      device=(device_name or describe_ua(user_agent or "")), ip=ip)
            _note_global_fail()
            time.sleep(0.4)
            if locked:
                raise HTTPException(429, f"Troppi tentativi: utente bloccato per {LOCK_MINUTES} minuti")
            raise HTTPException(401, "Utente o codice errati")
        token = secrets.token_urlsafe(32)
        days = int(config.OPTIONS.get("session_days") or 365)
        conn.execute("UPDATE users SET failed=0, locked_until=NULL, last_login=? WHERE id=?", (now_s(), u["id"]))
        dev = (device_name or "").strip()[:60] or describe_ua(user_agent or "")
        known = conn.execute("SELECT 1 FROM audit_log WHERE user_id=? AND category='accessi' AND device=? "
                             "AND action LIKE 'Accesso%' LIMIT 1", (u["id"], dev)).fetchone()
        conn.execute(
            "INSERT INTO sessions(token_hash, user_id, device_name, user_agent, device, ip, created_at, expires_at, last_seen) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (_th(token), u["id"], dev, (user_agent or "")[:300], describe_ua(user_agent or ""), (ip or "")[:64],
             now_s(), (now() + timedelta(days=days)).isoformat(sep=" "), now_s()),
        )
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now_s(),))
        audit.log({"id": u["id"], "display_name": u["display_name"]}, "accessi",
                  "Accesso" if known else "Accesso da nuovo dispositivo", describe_ua(user_agent or ""),
                  conn=conn, device=dev, ip=ip)
        return token, public_user(dict(u))


def logout(token: str | None) -> None:
    if token:
        with db() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (_th(token),))


def _th(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def public_user(u: dict) -> dict:
    return {"id": u["id"], "username": u["username"], "display_name": u["display_name"], "role": u["role"]}


# ---------- dipendenze FastAPI ----------

def current_user(request: Request) -> dict:
    """Sessione legata al dispositivo: resta valida finché l'utente non esce o l'admin la chiude
    (scadenza scorrevole, rinnovata a ogni uso)."""
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "Accesso richiesto")
    with db() as conn:
        row = conn.execute(
            "SELECT u.*, s.id AS session_id, s.device_name, s.expires_at, s.last_seen FROM sessions s "
            "JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ?", (_th(token),)).fetchone()
        if row is None or row["expires_at"] < now_s() or not row["active"]:
            raise HTTPException(401, "Sessione scaduta")
        # rinnovo "scorrevole": aggiorna last_seen al massimo ogni 5 minuti
        if row["last_seen"] < (now() - timedelta(minutes=5)).isoformat(sep=" "):
            days = int(config.OPTIONS.get("session_days") or 365)
            conn.execute("UPDATE sessions SET last_seen=?, expires_at=?, ip=? WHERE token_hash=?",
                         (now_s(), (now() + timedelta(days=days)).isoformat(sep=" "), client_ip(request)[:64],
                          _th(token)))
    d = public_user(dict(row))
    d["session_id"] = row["session_id"]
    d["device_name"] = row["device_name"]
    d["ip"] = client_ip(request)
    return d


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


LEVEL = {"operatore": 1, "coordinatore": 2, "admin": 3}
ROLE_LABEL = {"operatore": "operatore", "coordinatore": "coordinatore", "admin": "amministratore"}


def require(user: dict, role: str) -> None:
    """Admin > Coordinatore > Operatore."""
    if LEVEL.get(user["role"], 0) < LEVEL[role]:
        raise HTTPException(403, f"Operazione riservata a: {ROLE_LABEL[role]}")


def check_csrf(request: Request) -> None:
    """Le richieste che modificano dati devono avere l'header X-Bar (un form di un altro sito non può impostarlo)."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.headers.get("X-Bar") != "1":
        raise HTTPException(403, "Richiesta non valida")
