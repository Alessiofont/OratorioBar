"""Prospetto dinamico: uscite medie per giorno della settimana, proiezione delle giacenze,
scorta ideale suggerita dall'anno precedente e pezzi a rischio scadenza."""
from __future__ import annotations

import calendar
import math
from collections import defaultdict
from datetime import date, timedelta

from .db import get_settings, today as _today

WEEKDAYS = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]
TREND_WEEKS = 8


def consumption_sql(include_loss: bool) -> str:
    """Condizione SQL per i movimenti che contano come 'uscita' (consumo reale)."""
    cond = "(type IN ('vendita','scarico'))"
    if include_loss:
        cond = f"({cond} OR (type IN ('inventario','correzione') AND qty < 0))"
    return cond


def horizon_end(code: str, ref: date) -> tuple[date, str]:
    if code == "fine_mese_prossimo":
        y, m = (ref.year + 1, 1) if ref.month == 12 else (ref.year, ref.month + 1)
        return date(y, m, calendar.monthrange(y, m)[1]), "fine del mese prossimo"
    if code and code.isdigit():
        n = max(1, min(int(code), 365))
        return ref + timedelta(days=n - 1), f"prossimi {n} giorni"
    return date(ref.year, ref.month, calendar.monthrange(ref.year, ref.month)[1]), "fine mese"


def round_up(qty: float) -> int:
    return 0 if qty <= 0 else int(math.ceil(qty - 1e-9))


def _sum_range(dmap: dict, a: date, b: date) -> float:
    """Somma delle uscite tra a e b inclusi."""
    return sum(q for d, q in dmap.items() if a <= d <= b)


def compute(conn, horizon: str = "fine_mese", ref: date | None = None,
            product_ids: list[int] | None = None) -> dict:
    ref = ref or _today()
    st = get_settings(conn)
    window = max(7, int(st.get("window_days") or 56))
    ideal_days = max(7, int(st.get("ideal_period_days") or 30))
    include_loss = st.get("include_inventory_loss", "1") == "1"
    end, label = horizon_end(horizon, ref)
    start = ref - timedelta(days=window)
    cond = consumption_sql(include_loss)

    prods = [dict(r) for r in conn.execute(
        "SELECT p.*, c.name AS category, c.color AS cat_color FROM products p "
        "LEFT JOIN categories c ON c.id = p.category_id WHERE p.status = 'attivo' "
        "ORDER BY COALESCE(c.sort, 999), c.name, p.sort, p.name").fetchall()]
    if product_ids is not None:
        prods = [p for p in prods if p["id"] in product_ids]

    # uscite giornaliere: finestra recente + anno precedente (stesso periodo e trend)
    ly_from = min(start, ref - timedelta(days=365 + TREND_WEEKS * 7))
    daily: dict[int, dict[date, float]] = defaultdict(dict)
    for r in conn.execute(
        f"SELECT product_id, date(ts) AS d, SUM(-qty) AS q FROM movements WHERE {cond} "
        f"AND date(ts) >= ? AND date(ts) <= ? GROUP BY product_id, date(ts)",
        (ly_from.isoformat(), max(ref, ref - timedelta(days=365) + timedelta(days=ideal_days)).isoformat())).fetchall():
        daily[r["product_id"]][date.fromisoformat(r["d"])] = r["q"] or 0.0

    first_mov = {r["product_id"]: date.fromisoformat(r["d"]) for r in conn.execute(
        "SELECT product_id, MIN(date(ts)) AS d FROM movements GROUP BY product_id").fetchall()}

    lots_by_prod: dict[int, list] = defaultdict(list)
    for r in conn.execute("SELECT l.product_id, l.qty, l.expiry, l.expiry_estimated FROM lots l "
                          "JOIN carts c ON c.id = l.cart_id WHERE l.qty > 0 "
                          "ORDER BY (l.expiry IS NULL), l.expiry, c.date, l.id").fetchall():
        lots_by_prod[r["product_id"]].append(dict(r))

    days_to_end = [ref + timedelta(days=i) for i in range((end - ref).days + 1)]
    out = []
    for p in prods:
        created = date.fromisoformat(p["created_at"][:10])
        first = min(created, first_mov.get(p["id"], created))
        eff_start = max(start, first)
        obs = [eff_start + timedelta(days=i) for i in range((ref - eff_start).days)]  # giorni pieni, oggi escluso
        dmap = daily.get(p["id"], {})
        total = sum(dmap.get(d, 0) for d in obs)
        n = len(obs)
        today_out = dmap.get(ref, 0.0)

        if n >= 14:
            cnt, tot = [0] * 7, [0.0] * 7
            for d in obs:
                cnt[d.weekday()] += 1
                tot[d.weekday()] += dmap.get(d, 0)
            flat = total / n
            profile = [tot[w] / cnt[w] if cnt[w] else flat for w in range(7)]
            method = "settimanale"
        elif n > 0:
            profile = [total / n] * 7
            method = "media semplice"
        else:
            profile = [0.0] * 7
            method = "nessun dato"

        def expected(d: date) -> float:
            e = profile[d.weekday()]
            return max(0.0, e - today_out) if d == ref else e

        week = sum(profile)
        stock = p["stock"]
        cum = 0.0
        stockout = None
        for d in days_to_end:
            e = expected(d)
            cum += e
            if stockout is None and e > 0 and stock - cum < 0:
                stockout = d
        if stock <= 0:
            stockout = ref
        end_stock = stock - cum

        # --- scorta ideale suggerita dall'anno precedente (solo se ci sono i dati)
        ideal_dyn = None
        ly_start = ref - timedelta(days=365)
        if first <= ly_start:
            ly_period = _sum_range(dmap, ly_start, ly_start + timedelta(days=ideal_days - 1))
            trend = None
            ty_a, ty_b = ref - timedelta(days=TREND_WEEKS * 7), ref - timedelta(days=1)
            ly_a, ly_b = ty_a - timedelta(days=365), ty_b - timedelta(days=365)
            if first <= ly_a:
                ly_recent = _sum_range(dmap, ly_a, ly_b)
                ty_recent = _sum_range(dmap, ty_a, ty_b)
                if ly_recent > 0:
                    trend = max(0.5, min(2.0, ty_recent / ly_recent))
            if ly_period > 0:
                ideal_dyn = {
                    "qty": round_up(ly_period * (trend or 1.0)),
                    "last_year": round(ly_period, 1),
                    "trend": round(trend, 2) if trend else None,
                    "period_days": ideal_days,
                    "from": ly_start.isoformat(),
                }
        ideal_eff = ideal_dyn["qty"] if ideal_dyn else p["ideal_stock"]

        # --- quanto comprare: coprire l'orizzonte restando sopra il minimo, e arrivare alla scorta ideale
        target = max(cum + p["min_stock"], ideal_eff or 0)
        suggested = round_up(target - stock)

        # --- pezzi a rischio scadenza (consumo FEFO previsto dai profili)
        at_risk = 0
        risk_date = None
        before = 0
        for lot in lots_by_prod.get(p["id"], []):
            if lot["expiry"]:
                exp = date.fromisoformat(lot["expiry"])
                if exp < ref:
                    cons = 0.0
                else:
                    cons = sum(expected(ref + timedelta(days=i)) for i in range((exp - ref).days + 1))
                left = before + lot["qty"] - cons
                r = int(max(0, min(lot["qty"], left)))
                if r > 0:
                    at_risk += r
                    risk_date = risk_date or lot["expiry"]
            before += lot["qty"]

        if stock <= 0 and (week > 0 or p["min_stock"] > 0):
            status = "esaurito"
        elif stockout is not None and stockout <= end and week > 0:
            status = "esaurisce"
        elif p["min_stock"] > 0 and (stock <= p["min_stock"] or end_stock < p["min_stock"]):
            status = "sotto_minimo"
        elif method == "nessun dato":
            status = "nessun_dato"
        elif week == 0:
            status = "fermo"
        else:
            status = "ok"
        if method == "nessun dato":
            at_risk, risk_date = 0, None   # senza uscite registrate il rischio non è stimabile

        daily_avg = week / 7
        out.append({
            "product_id": p["id"],
            "name": p["name"],
            "color": p["cat_color"],
            "photo": p["photo"],
            "category": p["category"],
            "status_anagrafica": p["status"],
            "stock": stock,
            "min_stock": p["min_stock"],
            "ideal_stock": p["ideal_stock"],
            "ideal_dyn": ideal_dyn,
            "method": method,
            "observed_days": n,
            "avg_week": round(week, 1),
            "profile": [round(x, 2) for x in profile],
            "expected_out": round(cum, 1),
            "end_stock": round(end_stock, 1),
            "coverage_days": round(stock / daily_avg, 1) if daily_avg > 0 and stock > 0 else None,
            "stockout_date": stockout.isoformat() if stockout else None,
            "suggested": suggested,
            "at_risk": at_risk,
            "risk_date": risk_date,
            "status": status,
        })
    return {
        "ref_date": ref.isoformat(),
        "end_date": end.isoformat(),
        "horizon": horizon,
        "horizon_label": label,
        "days": len(days_to_end),
        "window_days": window,
        "weekdays": WEEKDAYS,
        "items": out,
    }


def monthly(conn, months: int = 6, ref: date | None = None) -> dict:
    ref = ref or _today()
    st = get_settings(conn)
    cond = consumption_sql(st.get("include_inventory_loss", "1") == "1")
    keys = []
    y, m = ref.year, ref.month
    for _ in range(max(1, min(months, 24))):
        keys.append(f"{y:04d}-{m:02d}")
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    keys.reverse()
    ly_keys = [f"{int(k[:4]) - 1:04d}{k[4:]}" for k in keys]
    data: dict[int, dict[str, float]] = defaultdict(dict)
    for r in conn.execute(
        f"SELECT product_id, strftime('%Y-%m', ts) AS ym, SUM(-qty) AS q FROM movements WHERE {cond} "
        f"AND strftime('%Y-%m', ts) >= ? GROUP BY product_id, ym", (ly_keys[0],)).fetchall():
        data[r["product_id"]][r["ym"]] = round(r["q"] or 0)
    loads: dict[int, dict[str, float]] = defaultdict(dict)
    for r in conn.execute(
        "SELECT product_id, strftime('%Y-%m', ts) AS ym, SUM(qty) AS q FROM movements WHERE type='carico' "
        "AND strftime('%Y-%m', ts) >= ? GROUP BY product_id, ym", (keys[0],)).fetchall():
        loads[r["product_id"]][r["ym"]] = round(r["q"] or 0)
    prods = conn.execute(
        "SELECT p.id, p.name, c.name AS category FROM products p "
        "LEFT JOIN categories c ON c.id = p.category_id WHERE p.status = 'attivo' "
        "ORDER BY COALESCE(c.sort, 999), c.name, p.sort, p.name").fetchall()
    dim = calendar.monthrange(ref.year, ref.month)[1]
    items = []
    for p in prods:
        d = data[p["id"]]
        items.append({
            "product_id": p["id"], "name": p["name"],
            "category": p["category"],
            "out": [d.get(k, 0) for k in keys],
            "out_last_year": [d.get(k) for k in ly_keys],
            "in": [loads[p["id"]].get(k, 0) for k in keys],
        })
    return {"months": keys, "current_month_progress": round(ref.day / dim, 3), "items": items}
