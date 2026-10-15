"""Avvio dell'add-on: Tailscale (userspace) con Funnel per il link dedicato, poi il server web."""
from __future__ import annotations

import atexit
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time

from . import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout)
log = logging.getLogger("bar.avvio")

PORT = config.PUBLIC_PORT
SOCK = "/tmp/tailscaled.sock"
STATE_DIR = os.path.join(os.environ.get("BAR_DATA_DIR", "/data"), "tailscale")
procs: list[subprocess.Popen] = []


def ts(*args, timeout: int = 30, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["tailscale", "--socket", SOCK, *args], capture_output=True, text=True, timeout=timeout, check=check)


def stream(proc: subprocess.Popen, prefix: str) -> None:
    for line in iter(proc.stdout.readline, ""):
        line = line.rstrip()
        if line:
            log.info("%s %s", prefix, line)


def backend_state() -> tuple[str, dict]:
    try:
        r = ts("status", "--json", timeout=10)
        st = json.loads(r.stdout or "{}")
        return st.get("BackendState", ""), st
    except Exception:
        return "", {}


def run_up(host: str, key: str) -> bool:
    """Esegue `tailscale up`; True quando il nodo è collegato, False se il comando fallisce o scade il tempo."""
    cmd = ["tailscale", "--socket", SOCK, "up", f"--hostname={host}", "--accept-dns=false", "--reset"]
    if key:
        cmd.append(f"--authkey={key}")
    up = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    procs.append(up)
    threading.Thread(target=stream, args=(up, "[tailscale up]"), daemon=True).start()
    for _ in range(600):               # fino a 10 minuti per l'eventuale accesso manuale
        if backend_state()[0] == "Running":
            return True
        if up.poll() not in (None, 0):  # es. chiave scaduta/non valida
            return False
        time.sleep(1)
    return False


def start_tailscale(opts: dict) -> None:
    if not shutil.which("tailscaled"):
        log.warning("tailscale non installato: link Funnel non disponibile")
        return
    os.makedirs(STATE_DIR, exist_ok=True)
    d = subprocess.Popen(["tailscaled", "--tun=userspace-networking", f"--statedir={STATE_DIR}", f"--socket={SOCK}", "--port=0"],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    procs.append(d)
    threading.Thread(target=stream, args=(d, "[tailscaled]"), daemon=True).start()
    for _ in range(60):
        if os.path.exists(SOCK):
            break
        time.sleep(0.5)
    host = opts.get("tailscale_hostname") or "bar-oratorio"
    key = (opts.get("tailscale_authkey") or "").strip()
    state = ""
    for _ in range(40):                 # attende che tailscaled carichi lo stato salvato
        state, _ = backend_state()
        if state in ("Running", "NeedsLogin", "Stopped", "NeedsMachineAuth"):
            break
        time.sleep(0.5)
    if state != "Running":
        # La chiave serve solo al primo collegamento: dopo, l'identità resta in /data/tailscale
        # e la scadenza della chiave non conta più. Se manca o è scaduta si usa il link di accesso.
        ok = False
        if key:
            ok = run_up(host, key)
            if not ok:
                log.warning("Chiave Tailscale non valida o scaduta. Apri il link di accesso che compare qui sotto "
                            "(oppure metti una chiave nuova nelle opzioni e riavvia).")
        else:
            log.warning("Nessuna chiave Tailscale nelle opzioni: apri il link di accesso che compare qui sotto.")
        if not ok:
            run_up(host, "")
    else:
        ts("set", f"--hostname={host}")
    state, st = backend_state()
    if state != "Running":
        log.error("Tailscale non collegato (stato: %s). L'app resta raggiungibile solo in rete locale se hai abilitato la porta.", state)
        return
    ts("serve", "reset")
    mode = "funnel" if opts.get("funnel", True) else "serve"
    r = subprocess.Popen(["tailscale", "--socket", SOCK, mode, "--bg", str(PORT)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    threading.Thread(target=stream, args=(r, f"[tailscale {mode}]"), daemon=True).start()
    try:
        r.wait(timeout=90)
    except subprocess.TimeoutExpired:
        log.warning("Funnel non ancora attivo: se nel log c'è un link per abilitarlo, aprilo e riavvia l'add-on.")
    dns = (st.get("Self") or {}).get("DNSName", "").rstrip(".")
    if dns:
        # Il certificato HTTPS viene emesso alla prima richiesta e ci vuole fino a un minuto:
        # lo prepariamo subito, così chi apre il link non trova l'errore SSL.
        log.info("Preparo il certificato HTTPS per %s (al primo avvio può richiedere un minuto)...", dns)
        ready = False
        for attempt in range(3):
            try:
                c = ts("cert", "--cert-file=/tmp/ts-cert.pem", "--key-file=/tmp/ts-key.pem", dns, timeout=180)
                if c.returncode == 0:
                    ready = True
                    break
                log.warning("Certificato non ancora pronto (tentativo %d): %s", attempt + 1, (c.stdout + c.stderr).strip()[:300])
            except subprocess.TimeoutExpired:
                log.warning("Certificato non ancora pronto (tentativo %d): tempo scaduto", attempt + 1)
            time.sleep(10)
        for f in ("/tmp/ts-cert.pem", "/tmp/ts-key.pem"):
            try:
                os.remove(f)
            except OSError:
                pass
        if ready:
            log.info("Certificato HTTPS pronto.")
        else:
            log.warning("Certificato HTTPS non ottenuto: verifica che nella console Tailscale siano attivi "
                        "MagicDNS e HTTPS Certificates, poi riavvia l'add-on.")
        log.info("=" * 60)
        log.info("Bar Oratorio raggiungibile su: https://%s", dns)
        log.info("(%s)", "pubblico via Funnel" if mode == "funnel" else "solo dai dispositivi della tailnet")
        log.info("=" * 60)


def stop_all() -> None:
    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass


def main() -> None:
    opts = config.load()
    pw = opts.get("admin_password") or ""
    if not os.path.exists(os.path.join(os.environ.get("BAR_DATA_DIR", "/data"), "bar.db")) and len(pw) < 8:
        log.error("Prima di avviare imposta 'admin_password' (almeno 8 caratteri) nella scheda Configurazione dell'add-on.")
        sys.exit(1)
    atexit.register(stop_all)
    if opts.get("tailscale_enabled", True):
        threading.Thread(target=start_tailscale, args=(opts,), daemon=True).start()
    import uvicorn
    log.info("Avvio del server web sulla porta %s (versione %s)", PORT, config.VERSION)
    uvicorn.run("app.main:app", host="0.0.0.0", port=PORT, proxy_headers=True, forwarded_allow_ips="127.0.0.1",
                log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
