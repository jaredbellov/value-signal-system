# -*- coding: utf-8 -*-
"""
UPDATE GROWTH ETFS (CFISPETF / CFINASDAQ)
==========================================
Genera sp_nq_data.json para la seccion Growth ETFs de la pagina web.

Reutiliza EXACTAMENTE el scoring de value_signal.py (el mismo del Streamlit):
CAPE oficial Shiller (40%) + Drawdown (25%) + EY vs Bond (15%) +
Yield Curve (10%) + Momentum 12-1 (10%).

Patron JSON-cache: corre LOCALMENTE (Task Scheduler cada 30 min) y pushea
el JSON al repo. La pagina solo lee desde raw.githubusercontent.com.

Uso:
  python update_growth_etfs.py            # genera JSON + git add/commit/push
  python update_growth_etfs.py --no-git   # solo genera el JSON
"""

import json
import subprocess
from git_lock import git_lock  # serializa acceso a git entre tareas
import sys
from datetime import datetime, timezone
from pathlib import Path

# Reusar el motor del sistema (mismo directorio del repo)
from value_signal import (
    fetch_monthly, fetch_daily, fetch_cape_official, calc_scores,
    analyze_etf_prices, WEIGHTS, MULT, APORTE_SP500, APORTE_NASDAQ,
)
import pandas as pd
import yfinance as yf

REPO = Path(__file__).parent
OUT = REPO / "sp_nq_data.json"

ETFS = {
    "CFISPETF": {
        "indice": "S&P 500",
        "indice_ticker": "^GSPC",
        "etf_ticker_yf": "CFISP500.SN",
        "aporte_base_usd": APORTE_SP500,
        "long_name": "FI ETF Singular S&P 500",
        "description": (
            "ETF de Singular AGF que replica el S&P 500: las ~500 mayores empresas de "
            "EEUU (cubre ~80% del mercado accionario norteamericano), con sectores "
            "diversificados. Accesible desde Chile via Racional, en CLP. Estrategia "
            "pasiva. El score se calcula sobre el indice subyacente con CAPE de Shiller."
        ),
        "usa_cape_oficial": True,
    },
    "CFINASDAQ": {
        "indice": "Nasdaq 100",
        "indice_ticker": "^NDX",
        "etf_ticker_yf": "CFINASDAQ.SN",
        "aporte_base_usd": APORTE_NASDAQ,
        "long_name": "FI ETF Singular Nasdaq 100",
        "description": (
            "ETF de Singular AGF que replica el Nasdaq 100: las 100 mayores companias "
            "no-financieras del Nasdaq, con fuerte sesgo tecnologico (Apple, Microsoft, "
            "Nvidia, Google, Amazon, Meta). Accesible desde Chile via Racional, en CLP. "
            "El score usa un proxy de valuacion (precio vs media movil 10 anos) porque "
            "el CAPE oficial de Shiller solo existe para el S&P 500."
        ),
        "usa_cape_oficial": False,
    },
}


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def historico_diario(ticker, period="5y"):
    s = fetch_daily(ticker, period=period)
    if s is None or len(s) < 100:
        return []
    return [{"fecha": idx.strftime("%Y-%m-%d"), "close": round(float(v), 2)}
            for idx, v in s.dropna().items()]


def main():
    no_git = "--no-git" in sys.argv

    log("Descargando indices mensuales (^GSPC, ^NDX, ^TNX, ^IRX)...")
    sp500 = fetch_monthly("^GSPC")
    nasdaq = fetch_monthly("^NDX")
    rate_10y = fetch_monthly("^TNX")
    rate_3m = fetch_monthly("^IRX")
    if sp500 is None or nasdaq is None or len(sp500) < 120:
        log("ERROR: datos de indices insuficientes — se conserva el JSON anterior")
        sys.exit(1)

    data = pd.DataFrame({"sp500": sp500, "nasdaq": nasdaq,
                         "rate_10y": rate_10y, "rate_3m": rate_3m})
    data["yield_curve"] = data["rate_10y"] - data["rate_3m"]

    log("Descargando CAPE oficial...")
    cape_official = fetch_cape_official()
    log(f"  CAPE: {'OK ' + str(round(float(cape_official.iloc[-1]),2)) if cape_official is not None else 'no disponible, usando proxy'}")

    series = {"CFISPETF": ("sp500", cape_official), "CFINASDAQ": ("nasdaq", None)}
    etfs_out, fallidos = {}, []

    for tk, meta in ETFS.items():
        try:
            col, cape_ext = series[tk]
            df = calc_scores(data[col], data["rate_10y"], data["yield_curve"], cape_ext)
            valid = df.dropna(subset=["score"])
            if valid.empty:
                raise ValueError("score sin datos validos")
            last = valid.iloc[-1]
            prev = valid.iloc[-2] if len(valid) >= 2 else last
            score = float(last["score"])
            zona = str(last["zona"])
            mult = MULT.get(zona, 1.0)

            log(f"Descargando precio ETF Racional {meta['etf_ticker_yf']}...")
            etf_px = analyze_etf_prices(fetch_daily(meta["etf_ticker_yf"], period="1y"),
                                        meta["indice"], meta["etf_ticker_yf"])

            log(f"Descargando historico diario {meta['indice_ticker']} (5y)...")
            hist = historico_diario(meta["indice_ticker"])
            if len(hist) < 100:
                raise ValueError("historico del indice insuficiente")

            etfs_out[tk] = {
                "ticker": tk,
                "indice": meta["indice"],
                "indice_ticker": meta["indice_ticker"],
                "long_name": meta["long_name"],
                "emisor": "Singular AGF",
                "moneda_etf": "CLP",
                "description": meta["description"],
                "cape_oficial": meta["usa_cape_oficial"],
                "precio_indice": round(float(last["price"]), 2),
                "cape": round(float(last["cape"]), 2),
                "drawdown_pct": round(float(last["drawdown"]) * 100, 2),
                "score": round(score, 1),
                "zona": zona,
                "multiplicador": mult,
                "delta_score": round(score - float(prev["score"]), 1),
                "aporte_base_usd": meta["aporte_base_usd"],
                "aporte_sugerido_usd": round(meta["aporte_base_usd"] * mult, 0),
                "precio_etf_clp": round(etf_px["current"], 2) if etf_px else None,
                "fecha_precio_etf": etf_px["date"] if etf_px else None,
                "rango_365_clp": ({"min": round(etf_px["r365"]["min"], 2),
                                   "max": round(etf_px["r365"]["max"], 2)}
                                  if etf_px and etf_px.get("r365") else None),
                "componentes": {k: round(float(last[f"s_{k}"]), 1) if pd.notna(last[f"s_{k}"]) else 0
                                for k in WEIGHTS},
                "fecha_dato_indice": last.name.strftime("%Y-%m-%d"),
                "historico": hist,
            }
            log(f"  OK {tk}: score {score:.1f} ({zona})")
        except Exception as e:
            log(f"  FALLO {tk}: {e}")
            fallidos.append({"ticker": tk, "error": str(e)})

    if not etfs_out:
        log("Ningun ETF procesado — no se escribe el JSON")
        sys.exit(1)

    OUT.write_text(json.dumps({
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "etfs": etfs_out,
        "fallidos": fallidos,
    }, ensure_ascii=False), encoding="utf-8")
    log(f"JSON escrito: {OUT} ({OUT.stat().st_size//1024} KB)")

    if no_git:
        return
    # push con lock compartido para evitar choques entre tareas
    if False:  # [PLAN-B] git neutralizado: publicar.py se encarga del push
        try:
            subprocess.run(["git", "pull", "--no-rebase", "--no-edit"], cwd=REPO,
                           check=False, capture_output=True)
            subprocess.run(["git", "add", OUT.name], cwd=REPO, check=True, capture_output=True)
            r = subprocess.run(["git", "commit", "-m", "data: growth ETFs (SP500/Nasdaq)"],
                               cwd=REPO, capture_output=True, text=True)
            if r.returncode == 0:
                subprocess.run(["git", "push"], cwd=REPO, check=True, capture_output=True)
                log("Push OK")
            else:
                log("Sin cambios para commitear")
        except subprocess.CalledProcessError as e:
            log(f"Error git: {e}")
            sys.exit(1)


if __name__ == "__main__":
    main()
