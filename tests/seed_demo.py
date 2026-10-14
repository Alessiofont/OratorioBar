"""Popola un'istanza di prova con utenti, prodotti e movimenti (per i test dell'interfaccia).
Uso: python tests/seed_demo.py http://127.0.0.1:8765
"""
import json
import sys
import urllib.request
from http.cookiejar import CookieJar

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765").rstrip("/")


class Client:
    def __init__(self):
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(BASE + "/api/" + path, data=data, method=method,
                                     headers={"X-Bar": "1", "Content-Type": "application/json"})
        try:
            with self.op.open(req) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            raise SystemExit(f"{method} {path}: {e.code} {e.read().decode()}")

    def login(self, u, s, dev):
        return self.call("POST", "login", {"username": u, "secret": s, "device_name": dev})


admin = Client(); admin.login("oratorio", "oratorio2026", "PC segreteria")
admin.call("PUT", "settings", {"photo_lookup": "0"})
admin.call("POST", "users", {"username": "giulia", "display_name": "Giulia Ferri", "role": "coordinatore", "pin": "2222"})
admin.call("POST", "users", {"username": "marco", "display_name": "Marco Rossi", "role": "operatore", "pin": "1234"})
admin.call("POST", "users", {"username": "sara", "display_name": "Sara Bianchi", "role": "operatore", "pin": "1111"})
coord = Client(); coord.login("giulia", "2222", "PC ufficio")
cats = {c["name"]: c["id"] for c in coord.call("GET", "bootstrap")["categories"]}
P = {}
for name, cat, mn, codes in [
    ("Coca-Cola lattina 33cl", "Bibite", 24, ["5449000000996"]), ("Fanta lattina 33cl", "Bibite", 12, ["5449000011527"]),
    ("Acqua naturale 50cl", "Acqua", 24, ["8001620007003"]), ("Estathé limone", "Succhi e tè", 12, ["8000090003314"]),
    ("Kinder Bueno", "Snack dolci", 10, ["8000500167113"]), ("Patatine classiche", "Snack salati", 10, ["8001200001119"]),
    ("Ghiacciolo limone", "Gelati", 10, []), ("Panino prosciutto", "Freschi", 0, []),
    ("Bicchieri di plastica", "Materiale di consumo", 50, []),
]:
    P[name] = coord.call("POST", "products", {"name": name, "category_id": cats[cat], "min_stock": mn, "barcodes": codes})["id"]
marco = Client(); marco.login("marco", "1234", "Telefono bar")
marco.call("PUT", "draft", {"lines": [
    {"product_id": P["Coca-Cola lattina 33cl"], "qty": 48, "expiry": "2027-03-10"},
    {"product_id": P["Fanta lattina 33cl"], "qty": 24, "expiry": "2027-02-01"},
    {"product_id": P["Acqua naturale 50cl"], "qty": 48, "expiry": "2027-09-01"},
    {"product_id": P["Estathé limone"], "qty": 12},
    {"product_id": P["Kinder Bueno"], "qty": 20, "expiry": "2026-11-30"},
    {"product_id": P["Ghiacciolo limone"], "qty": 20},
    {"product_id": P["Panino prosciutto"], "qty": 10},
    {"product_id": P["Bicchieri di plastica"], "qty": 200},
], "note": "Spesa Metro"})
marco.call("POST", "carico", {"confirm_missing_expiry": True})
marco.call("POST", "scarico", {"type": "vendita", "reason": "Venduto al bar", "items": [
    {"product_id": P["Coca-Cola lattina 33cl"], "qty": 30}, {"product_id": P["Kinder Bueno"], "qty": 12},
    {"product_id": P["Panino prosciutto"], "qty": 4}, {"product_id": P["Bicchieri di plastica"], "qty": 160}]})
marco.call("POST", "products", {"name": "Lemonsoda 33cl", "category_id": cats["Bibite"], "barcodes": ["8002270014901"],
                                "proposal_note": "arrivato col carico di oggi"})
sara = Client(); sara.login("sara", "1111", "Telefono bar")
sara.call("PUT", "draft", {"lines": [{"product_id": P["Fanta lattina 33cl"], "qty": 12}], "note": "Consegna distributore"})
coord.call("POST", "shop/items", {"product_id": P["Coca-Cola lattina 33cl"], "qty": "48 pz"})
coord.call("POST", "shop/items", {"text": "Tovaglioli di carta", "qty": "2 pacchi"})
print("ok", P)
