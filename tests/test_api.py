"""Test end-to-end delle API: python -m pytest tests -q"""
import json
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ["BAR_DATA_DIR"] = tmp
os.environ["BAR_OPTIONS"] = os.path.join(tmp, "options.json")
os.environ["BAR_SCHEDULER"] = "0"
with open(os.environ["BAR_OPTIONS"], "w") as f:
    json.dump({"admin_username": "oratorio", "admin_password": "oratorio2026", "session_days": 365}, f)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bar_oratorio"))

from fastapi.testclient import TestClient  # noqa: E402
from app import notify, photos, stock  # noqa: E402
from app.main import app  # noqa: E402

photos.auto_photo_async = lambda pid: None   # niente rete nei test
photos.fill_missing_async = lambda: 0
SENT = []
notify.send = lambda to, title, message, tags=None, wait=False: SENT.append((tuple(to), title, message))
H = {"X-Bar": "1"}


def client(username, secret, device="Test"):
    c = TestClient(app)
    r = c.post("/api/login", json={"username": username, "secret": secret, "device_name": device}, headers=H)
    assert r.status_code == 200, r.text
    return c


def ok(r, code=200):
    assert r.status_code == code, f"{r.status_code} {r.text}"
    return r.json()


def test_full_flow():
    with TestClient(app) as boot:
        assert boot.get("/api/health").status_code == 200
        assert boot.get("/api/me").status_code == 401
        assert boot.post("/api/login", json={"username": "oratorio", "secret": "x"}).status_code == 403  # manca X-Bar
    admin = client("oratorio", "oratorio2026", "PC segreteria")
    ok(admin.post("/api/users", json={"username": "giulia", "display_name": "Giulia", "role": "coordinatore", "pin": "2222"}, headers=H))
    ok(admin.post("/api/users", json={"username": "marco", "display_name": "Marco", "role": "operatore", "pin": "1234"}, headers=H))
    coord = client("giulia", "2222", "PC ufficio")
    op = client("marco", "1234", "Telefono bar")
    assert coord.get("/api/users").status_code == 403
    assert coord.get("/api/log").status_code == 403

    cats = ok(coord.get("/api/bootstrap"))["categories"]
    bibite = next(c["id"] for c in cats if c["name"] == "Bibite")
    coca = ok(coord.post("/api/products", json={"name": "Coca-Cola 33cl", "category_id": bibite, "min_stock": 24,
                                                 "barcodes": ["5449000000996"]}, headers=H))["id"]
    fanta = ok(coord.post("/api/products", json={"name": "Fanta 33cl", "category_id": bibite, "min_stock": 6}, headers=H))["id"]

    # proposta bloccata finché non approvata + notifica ntfy ai coordinatori
    prop = ok(op.post("/api/products", json={"name": "Lemonsoda", "category_id": bibite, "barcodes": ["8002270014901"]}, headers=H))
    assert prop["status"] == "proposto" and SENT[-1][0] == ("coord",)
    r = op.put("/api/draft", json={"lines": [{"product_id": prop["id"], "qty": 5}]}, headers=H)
    assert r.status_code == 409 and "approvazione" in r.text
    assert ok(op.get("/api/lookup/8002270014901?online=false"))["pending"] is True
    ok(coord.post(f"/api/products/{prop['id']}/approve", json={"min_stock": 6}, headers=H))

    # carrello in bozza salvato sul server, poi confermato: diventa il lotto "gg/mm/aaaa"
    ok(op.put("/api/draft", json={"lines": [{"product_id": coca, "qty": 24}, {"product_id": fanta, "qty": 12, "expiry": "2026-12-20"}],
                                  "note": "Spesa Lidl"}, headers=H))
    op2 = client("marco", "1234", "Telefono di Marco")        # stesso utente, altro dispositivo
    assert len(ok(op2.get("/api/draft"))["lines"]) == 2
    r = op.post("/api/carico", json={}, headers=H)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "missing_expiry"
    c1 = ok(op.post("/api/carico", json={"confirm_missing_expiry": True}, headers=H))
    assert ok(op.get("/api/draft"))["lines"] == []
    ok(op.put("/api/draft", json={"lines": [{"product_id": coca, "qty": 6, "expiry": "2026-11-01"}]}, headers=H))
    c2 = ok(op.post("/api/carico", json={}, headers=H))
    assert c2["cart"]["name"] == c1["cart"]["name"] + "-2"

    # scarico FEFO: esce prima la riga che scade prima (lotto 2, 01/11)
    ok(op.post("/api/scarico", json={"type": "vendita", "items": [{"product_id": coca, "qty": 8}]}, headers=H))
    carts = ok(coord.get("/api/carts?status=tutti"))["items"]
    assert {c["name"] for c in carts} == {c1["cart"]["name"], c2["cart"]["name"]}
    d2 = ok(coord.get(f"/api/carts/{c2['cart']['id']}"))
    assert d2["lines"][0]["qty"] == 0          # i 6 pezzi del lotto 2 sono usciti per primi
    d1 = ok(coord.get(f"/api/carts/{c1['cart']['id']}"))
    coca_line = next(l for l in d1["lines"] if l["product_id"] == coca)
    assert coca_line["qty"] == 22 and coca_line["expiry_estimated"] == 1

    # I miei movimenti: modifica entro 30 minuti, mai eliminazione totale
    mine = ok(op.get("/api/my/batches"))["items"]
    carico1 = next(b for b in mine if b["type"] == "carico" and b["cart"] == c1["cart"]["name"])
    assert carico1["editable"]
    ed = ok(op.get(f"/api/batches/{carico1['id']}/edit"))
    rows = [{"lot_id": r["lot_id"], "product_id": r["product_id"], "qty": r["qty"], "expiry": r["expiry"]} for r in ed["rows"]]
    rows[0]["qty"] = 1                          # coca: ne sono già usciti 2 → non si può scendere a 1
    assert op.put(f"/api/batches/{carico1['id']}", json={"rows": rows}, headers=H).status_code == 409
    rows[0]["qty"] = 30
    rows[1]["delete"] = True                    # elimina la riga Fanta
    rows.append({"product_id": prop["id"], "qty": 4, "expiry": "2027-01-01"})
    ok(op.put(f"/api/batches/{carico1['id']}", json={"rows": rows, "note": "Spesa Lidl e Metro"}, headers=H))
    assert ok(coord.get(f"/api/products/{fanta}"))["stock"] == 0
    assert ok(coord.get(f"/api/products/{coca}"))["stock"] == 28
    all_del = [dict(r, delete=True) for r in ok(op.get(f"/api/batches/{carico1['id']}/edit"))["rows"]]
    assert op.put(f"/api/batches/{carico1['id']}", json={"rows": all_del}, headers=H).status_code == 409
    vend = next(b for b in ok(op.get("/api/my/batches"))["items"] if b["type"] == "vendita")
    ok(op.put(f"/api/batches/{vend['id']}", json={"rows": [{"product_id": coca, "qty": 5}]}, headers=H))
    assert ok(coord.get(f"/api/products/{coca}"))["stock"] == 31
    assert coord.put(f"/api/batches/{vend['id']}", json={"rows": [{"product_id": coca, "qty": 1}]}, headers=H).status_code == 403

    # storico senza nomi per il coordinatore; log completo per l'admin
    hist = ok(coord.get("/api/history"))["items"]
    assert hist and all("user" not in h for h in hist)
    lg = ok(admin.get("/api/log"))["items"]
    acts = [l["action"] for l in lg]
    assert any(a.startswith("Carico lotto") for a in acts) and any("Modifica" in a for a in acts)
    assert any(l["device"] == "Telefono bar" and l["user_name"] == "Marco" for l in lg)
    assert "Quando;Chi" in admin.get("/api/log.csv").text

    # gestore lotti: correzione riga, scadenza, scarico scaduto
    ok(coord.post(f"/api/lots/{coca_line['id']}/adjust", json={"qty": 27, "reason": "Ammanco"}, headers=H))
    ok(coord.put(f"/api/lots/{coca_line['id']}", json={"expiry": "2027-05-01"}, headers=H))
    assert ok(coord.get(f"/api/carts/{c1['cart']['id']}"))["lines"][0]["expiry_estimated"] == 0
    ok(coord.post(f"/api/lots/{coca_line['id']}/toss", headers=H))
    assert op.get("/api/carts").status_code == 403

    # Correggi quantità: pezzi in più in un lotto scelto o in un lotto di correzione
    ok(coord.post("/api/correggi", json={"product_id": coca, "stock": 6, "reason": "Trovati in più", "target": str(coca_line["id"])}, headers=H))
    ok(coord.post("/api/correggi", json={"product_id": fanta, "stock": 5, "reason": "Trovati in più", "target": "nuovo"}, headers=H))
    names = [c["name"] for c in ok(coord.get("/api/carts?status=tutti"))["items"]]
    assert any(n.endswith("-3") for n in names)

    # Fare inventario: contano tutti, chiude il coordinatore
    inv = ok(coord.post("/api/inventory/start", headers=H))["id"]
    ok(op.put(f"/api/inventory/{inv}/line", json={"product_id": coca, "counted": 2}, headers=H))
    ok(op.put(f"/api/inventory/{inv}/line", json={"product_id": fanta, "counted": 9}, headers=H))
    assert op.post(f"/api/inventory/{inv}/close", headers=H).status_code == 403
    assert ok(coord.post(f"/api/inventory/{inv}/close", headers=H))["differences"] == 2
    assert ok(coord.get(f"/api/products/{fanta}"))["stock"] == 9

    # lista della spesa: coordinatore aggiunge, operatore spunta e chiude, avviso ai coordinatori
    sh = ok(coord.get("/api/shop"))
    assert any(s["product_id"] == coca for s in sh["suggestions"])
    ok(coord.post("/api/shop/items", json={"product_id": coca, "qty": "48 pz"}, headers=H))
    item = ok(coord.post("/api/shop/items", json={"text": "Tovaglioli", "qty": "2 pacchi"}, headers=H))["id"]
    assert op.post("/api/shop/items", json={"text": "x"}, headers=H).status_code == 403
    ok(op.post(f"/api/shop/items/{item}/toggle", headers=H))
    res = ok(op.post("/api/shop/close", headers=H))
    assert res == {"ok": True, "taken": 1, "total": 2}
    assert ok(op.get("/api/shop"))["items"] == []
    assert ok(coord.get("/api/shop/archive"))[0]["items"][1]["checked"] is True

    # bozze altrui: il coordinatore le vede e le riprende
    ok(op.put("/api/draft", json={"lines": [{"product_id": fanta, "qty": 3}]}, headers=H))
    dr = ok(coord.get("/api/drafts"))
    assert dr[0]["user"] == "Marco"
    ok(coord.post(f"/api/drafts/{dr[0]['user_id']}/take", headers=H))
    assert len(ok(coord.get("/api/draft"))["lines"]) == 1 and ok(op.get("/api/draft"))["lines"] == []

    # prospetto e riepilogo
    assert ok(coord.get("/api/projection"))["items"]
    assert op.get("/api/projection").status_code == 403
    ok(coord.put("/api/settings", json={"digest_time": "15:00", "expiry_warning_days": "21"}, headers=H))
    assert coord.put("/api/settings", json={"digest_time": "25:00"}, headers=H).status_code == 400
    ok(coord.post("/api/ntfy/digest", headers=H))
    assert SENT[-1][0] == ("coord", "admin") and "Riepilogo" in SENT[-1][1]

    # dispositivi: l'admin vede e disconnette
    uid = ok(op.get("/api/me"))["id"]
    sess = ok(admin.get(f"/api/users/{uid}/sessions"))
    assert {s["device_name"] for s in sess} == {"Telefono bar", "Telefono di Marco"}
    first = next(s for s in sess if s["device_name"] == "Telefono bar")
    ok(admin.delete(f"/api/sessions/{first['id']}", headers=H))
    assert op.get("/api/me").status_code == 401 and op2.get("/api/me").status_code == 200

    # password admin e blocco tentativi
    ok(admin.post("/api/me/password", json={"current": "oratorio2026", "new": "nuovapass1"}, headers=H))
    client("oratorio", "nuovapass1")
    c = TestClient(app)
    for _ in range(4):
        assert c.post("/api/login", json={"username": "giulia", "secret": "0000"}, headers=H).status_code == 401
    assert c.post("/api/login", json={"username": "giulia", "secret": "0000"}, headers=H).status_code == 429
    lg = ok(client("oratorio", "nuovapass1").get("/api/log?category=accessi"))["items"]
    assert any(l["action"] == "Utente bloccato" for l in lg) and any(l["action"] == "PIN errato" for l in lg)
