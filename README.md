# Add-on Bar Oratorio per Home Assistant

Repository di add-on per Home Assistant OS con l'app **Bar Oratorio**: inventario del bar dell'oratorio
(carichi a carrello, lotti con scadenze, scarichi, inventario, prospetto, lista della spesa, log),
raggiungibile con un link dedicato tramite Tailscale Funnel.

Installazione: Home Assistant → Impostazioni → Componenti aggiuntivi → Negozio → ⋮ → Repository →
aggiungi l'indirizzo di questo repository → installa **Bar Oratorio**.
Configurazione e primo avvio: vedi [bar_oratorio/DOCS.md](bar_oratorio/DOCS.md).

Test delle API: `pip install fastapi==0.115.6 uvicorn==0.32.1 httpx pytest && python -m pytest tests -q`
