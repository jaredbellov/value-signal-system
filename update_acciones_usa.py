# -*- coding: utf-8 -*-
"""
UPDATE ACCIONES USA
====================
Genera acciones_usa_data.json para la seccion "Acciones USA" de Value Signal.

Patron JSON-cache: este script corre LOCALMENTE (Task Scheduler cada 30 min),
calcula todo con yfinance y pushea el JSON al repo. La pagina (GitHub Pages)
solo lee el JSON desde raw.githubusercontent.com. Nunca llamar Yahoo desde
la nube (rate-limiting confirmado con SCHD/JEPQ).

Metodologia (curso dividendos):
- Clasificacion por G = ROE x (1 - payout). Sin dividendos => CRECIMIENTO.
- Valoracion por multiplos cruzados: PER por rango de clasificacion,
  P/B razonable segun ROE (>15% => 2-2.5 | 10-15% => 1-2 | <10% => <=1),
  Graham = PER x P/B con 22.5 como desempate.
- DY informativo: promedio 3 anos completos (sin eventuales; en USA los
  dividendos ordinarios trimestrales equivalen a definitivo+provisorio).
- Sin flujo de caja descontado.

Uso:
  python update_acciones_usa.py            # genera JSON + git add/commit/push
  python update_acciones_usa.py --no-git   # solo genera el JSON
"""

import json
import subprocess
from git_lock import git_lock  # serializa acceso a git entre tareas
import sys
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf

REPO = Path(r"C:\value-signal-local\repo")
OUT = REPO / "acciones_usa_data.json"

# ============================================================
# WATCHLIST USA
# Para agregar una accion: nueva entrada aqui. Nada mas.
# ============================================================
WATCHLIST = {
    "TSLA": {
        "nombre": "Tesla Inc",
        "sector": "Automotriz / Energia",
        "descripcion": (
            "Fabricante de vehiculos electricos, almacenamiento de energia (Megapack) "
            "y software de conduccion autonoma (FSD). No paga dividendos: reinvierte el "
            "100% de las utilidades en crecimiento (gigafabricas, robotica, IA). "
            "Empresa de Crecimiento clasica: la tesis es ganancia por capital, no por renta."
        ),
    },
    "NVDA": {
        "nombre": "NVIDIA Corp",
        "sector": "Semiconductores",
        "descripcion": (
            "Lider mundial en GPUs y aceleradores de IA para datacenters (arquitecturas "
            "Blackwell/Rubin), con ecosistema de software CUDA como foso competitivo. "
            "Paga un dividendo simbolico (payout ~1%): reinvierte casi todo. Empresa de "
            "Crecimiento: ROE altisimo, valoracion exigente, tesis de ganancia por capital."
        ),
    },
    "AMZN": {
        "nombre": "Amazon.com Inc",
        "sector": "E-commerce / Cloud",
        "descripcion": (
            "Gigante del comercio electronico y lider en nube con AWS (su motor de margen "
            "y caja). No paga dividendos: reinvierte en logistica, IA y datacenters. "
            "Empresa de Crecimiento con foso amplio; la tesis es ganancia por capital "
            "apalancada en el margen creciente de AWS y publicidad."
        ),
    },
    "MSFT": {
        "nombre": "Microsoft Corp",
        "sector": "Software / Cloud",
        "descripcion": (
            "Software empresarial (Office, Windows), nube Azure e IA (participacion en "
            "OpenAI, Copilot). ROE alto y margenes muy estables. Paga dividendo creciente "
            "hace dos decadas con payout moderado: perfil DGI de calidad, aunque suele "
            "cotizar con multiplos exigentes por su consistencia."
        ),
    },
    "GOOGL": {
        "nombre": "Alphabet Inc (Class A)",
        "sector": "Publicidad / Cloud / IA",
        "descripcion": (
            "Matriz de Google: buscador, YouTube, Android, nube (GCP) e IA (Gemini, "
            "DeepMind). Foso en busqueda y publicidad con ROE alto y caja enorme. "
            "Inicio dividendo en 2024 (payout bajo): aun perfil de Crecimiento, con "
            "valoracion historicamente mas razonable que sus pares de megacap."
        ),
    },
    "META": {
        "nombre": "Meta Platforms Inc",
        "sector": "Publicidad / Redes sociales",
        "descripcion": (
            "Dueno de Facebook, Instagram y WhatsApp; lider en publicidad digital con "
            "ROE alto. Fuerte inversion en IA y Reality Labs (metaverso). Inicio dividendo "
            "en 2024 con payout bajo: perfil de Crecimiento que empieza a repartir caja, "
            "con multiplos mas contenidos que otras megacaps tech."
        ),
    },
    "AAPL": {
        "nombre": "Apple Inc",
        "sector": "Hardware / Servicios",
        "descripcion": (
            "Fabricante del iPhone con ecosistema de servicios de alto margen y recurrentes. "
            "ROE muy alto (apalancado por recompras agresivas) y caja masiva. Dividendo "
            "creciente con payout bajo y recompras constantes: perfil DGI premium, "
            "habitualmente con valoracion exigente por su calidad y previsibilidad."
        ),
    },
}

# PER razonable por clasificacion (curso)
PER_RANGOS = {
    "CRECIMIENTO": (25.0, 30.0),
    "DGI": (15.0, 24.9),
    "VACA LECHERA": (8.0, 14.0),
}


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def safe(v):
    """None si el valor es NaN/None/no numerico."""
    try:
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None


def pb_rango_por_roe(roe_pct):
    """P/B razonable segun ROE (tabla del curso)."""
    if roe_pct is None:
        return (None, None)
    if roe_pct > 15:
        return (2.0, 2.5)
    if roe_pct >= 10:
        return (1.0, 2.0)
    return (0.0, 1.0)


def clasificar(roe_pct, payout_pct):
    """G = ROE x (1 - payout). Sin dividendo relevante => CRECIMIENTO."""
    if payout_pct is None or payout_pct < 5:
        g = roe_pct if roe_pct is not None else None
        return "CRECIMIENTO", g
    if roe_pct is None:
        return "CRECIMIENTO", None
    g = roe_pct * (1 - payout_pct / 100.0)
    if g >= 15:
        return "CRECIMIENTO", g
    if g >= 10:
        return "DGI", g
    return "VACA LECHERA" if payout_pct > 80 else "DGI", g


def valorar(per, pb, roe_pct, clasif):
    """Multiplos cruzados: PER por rango + P/B vs ROE + Graham de desempate."""
    per_min, per_max = PER_RANGOS.get(clasif, (None, None))
    pb_min, pb_max = pb_rango_por_roe(roe_pct)
    graham = per * pb if (per is not None and pb is not None) else None

    per_ok = per is not None and per_max is not None and per <= per_max
    per_barata = per is not None and per_min is not None and per < per_min
    pb_ok = pb is not None and pb_max is not None and pb <= pb_max

    if per is None:
        status = "Sin datos"
    elif per_barata and pb_ok:
        status = "Barata"
    elif per_ok and pb_ok:
        status = "Razonable"
    elif per_ok != pb_ok and graham is not None:
        # contradiccion entre PER y P/B: Graham desempata
        status = "Razonable" if graham < 22.5 else "Cara"
    else:
        status = "Cara"

    return {
        "status": status,
        "per_rango_min": per_min,
        "per_rango_max": per_max,
        "pb_rango_min": pb_min,
        "pb_rango_max": pb_max,
        "graham": round(graham, 1) if graham is not None else None,
    }


def cagr(first, last, years):
    if not first or not last or first <= 0 or years <= 0:
        return None
    try:
        return (last / first) ** (1.0 / years) - 1.0
    except (ValueError, ZeroDivisionError):
        return None


def roe_robusto(t, info):
    """ROE en %. Primero info['returnOnEquity']; si viene None (info parcial de
    Yahoo), lo calcula como Net Income / Stockholders Equity desde los estados
    financieros. Devuelve (roe_pct, distorsionado).
    Si el patrimonio es <=0 (recompras agresivas, ej. AAPL), el ROE contable no
    es economico -> devuelve (None, True)."""
    # 1) via info (rapido)
    r = safe(info.get("returnOnEquity"))
    if r is not None:
        return round(r * 100, 2), False
    # 2) calcular desde estados financieros
    try:
        fin = t.financials
        bs = t.balance_sheet
        if fin is None or fin.empty or bs is None or bs.empty:
            return None, False
        fcol = fin.columns[0]
        bcol = bs.columns[0]
        ni_key = next((k for k in ("Net Income", "Net Income Common Stockholders",
                                   "Net Income From Continuing Operation Net Minority Interest")
                       if k in fin.index), None)
        eq_key = next((k for k in ("Stockholders Equity", "Total Stockholder Equity",
                                   "Common Stock Equity") if k in bs.index), None)
        if not ni_key or not eq_key:
            return None, False
        ni = safe(fin.loc[ni_key, fcol])
        eq = safe(bs.loc[eq_key, bcol])
        if ni is None or eq is None:
            return None, False
        if eq <= 0:
            # patrimonio contable distorsionado (recompras) -> ROE no economico
            return None, True
        return round(ni / eq * 100, 2), False
    except Exception as e:
        log(f"  aviso roe {info.get('symbol','?')}: {e}")
        return None, False


def payout_robusto(t, info):
    """Payout en %. Primero info['payoutRatio']; si None, lo calcula como
    dividendos pagados / Net Income (ultimo ano). Devuelve float o 0.0."""
    p = safe(info.get("payoutRatio"))
    if p is not None:
        return round(p * 100, 2)
    try:
        divs = t.dividends
        fin = t.financials
        if divs is None or len(divs) == 0 or fin is None or fin.empty:
            return 0.0
        ano = datetime.now().year - 1
        dps = float(divs[divs.index.year == ano].sum())
        if dps <= 0:
            return 0.0
        # dividendo por accion / EPS  ->  payout
        eps = safe(info.get("trailingEps"))
        if eps and eps > 0:
            return round(dps / eps * 100, 2)
    except Exception:
        pass
    return 0.0


def procesar(ticker, meta):
    log(f"Procesando {ticker}...")
    t = yf.Ticker(ticker)
    info = t.info or {}

    # ---- precio e historico (5y, diario) ----
    hist = t.history(period="5y", auto_adjust=True)
    if hist is None or hist.empty or len(hist) < 100:
        raise ValueError(f"{ticker}: historico insuficiente ({0 if hist is None else len(hist)} filas)")
    closes = hist["Close"].dropna()
    precio = safe(closes.iloc[-1])
    if precio is None or precio <= 0:
        raise ValueError(f"{ticker}: ultimo precio invalido")
    variacion = safe((closes.iloc[-1] / closes.iloc[-2] - 1) * 100) if len(closes) >= 2 else 0.0
    historico = [
        {"fecha": idx.strftime("%Y-%m-%d"), "close": round(float(v), 2)}
        for idx, v in closes.items()
    ]

    # ---- fundamentales ----
    roe_pct, roe_distorsionado = roe_robusto(t, info)
    margen = safe(info.get("profitMargins"))
    margen_pct = round(margen * 100, 2) if margen is not None else None
    payout_pct = payout_robusto(t, info)
    per = safe(info.get("trailingPE"))
    pb = safe(info.get("priceToBook"))
    razon_corriente = safe(info.get("currentRatio"))

    # endeudamiento = pasivos totales / activos totales (balance mas reciente)
    razon_endeud_pct, patrimonio, periodo = None, None, None
    try:
        bs = t.balance_sheet
        if bs is not None and not bs.empty:
            col = bs.columns[0]
            periodo = col.strftime("%m/%Y") if hasattr(col, "strftime") else str(col)
            ta = safe(bs.loc["Total Assets", col]) if "Total Assets" in bs.index else None
            tl_key = next((k for k in ("Total Liabilities Net Minority Interest", "Total Liab")
                           if k in bs.index), None)
            tl = safe(bs.loc[tl_key, col]) if tl_key else None
            eq_key = next((k for k in ("Stockholders Equity", "Total Stockholder Equity",
                                       "Common Stock Equity") if k in bs.index), None)
            patrimonio = safe(bs.loc[eq_key, col]) if eq_key else None
            if ta and tl and ta > 0:
                razon_endeud_pct = round(tl / ta * 100, 2)
    except Exception as e:
        log(f"  aviso balance {ticker}: {e}")

    # ---- dividendos ----
    divs = t.dividends
    anos_detalle, promedio, dy_pct, anos_usados = {}, None, 0.0, 0
    cagr3 = cagr5 = cagr10 = None
    recientes = []
    if divs is not None and len(divs):
        por_ano = divs.groupby(divs.index.year).sum()
        ano_actual = datetime.now().year
        completos = por_ano[por_ano.index < ano_actual]
        ult3 = completos.tail(3)
        if len(ult3):
            anos_detalle = {str(y): round(float(v), 4) for y, v in ult3.items()}
            promedio = round(float(ult3.mean()), 4)
            anos_usados = len(ult3)
            dy_pct = round(promedio / precio * 100, 2)
        if len(completos) >= 4:
            cagr3 = cagr(float(completos.iloc[-4]), float(completos.iloc[-1]), 3)
        if len(completos) >= 6:
            cagr5 = cagr(float(completos.iloc[-6]), float(completos.iloc[-1]), 5)
        if len(completos) >= 11:
            cagr10 = cagr(float(completos.iloc[-11]), float(completos.iloc[-1]), 10)
        for fecha, monto in divs.tail(6)[::-1].items():
            recientes.append({
                "fecha_pago": fecha.strftime("%Y-%m-%d"),
                "monto_usd": round(float(monto), 4),
                "tipo": "ORDINARIO",
            })

    clasif, g = clasificar(roe_pct, payout_pct)
    val = valorar(per, pb, roe_pct, clasif)

    return {
        "ticker": ticker,
        "nombre": meta["nombre"],
        "sector": meta["sector"],
        "descripcion": meta["descripcion"],
        "razon_social": info.get("longName") or meta["nombre"],
        "clasificacion": clasif,
        "g_pct": round(g, 2) if g is not None else None,
        "precio_actual_usd": round(precio, 2),
        "variacion_pct": round(variacion or 0, 2),
        "per": round(per, 1) if per is not None else None,
        "pb": round(pb, 2) if pb is not None else None,
        "payout_pct": payout_pct,
        "valoracion": val,
        "dy": {
            "dy_pct": dy_pct,
            "anos_usados": anos_usados,
            "anos_detalle": anos_detalle,
            "promedio_anual": promedio,
        },
        "cagr_3y": cagr3, "cagr_5y": cagr5, "cagr_10y": cagr10,
        "fund": {
            "fuente": "10-K/10-Q via Yahoo Finance",
            "periodo": periodo,
            "moneda": "USD",
            "roe_pct": roe_pct,
            "roe_distorsionado": roe_distorsionado,
            "margen_neto_pct": margen_pct,
            "razon_endeudamiento_pct": razon_endeud_pct,
            "razon_corriente": round(razon_corriente, 2) if razon_corriente is not None else None,
            "patrimonio_total": patrimonio,
        },
        "dividendos_recientes": recientes,
        "historico": historico,
    }


# Pausa entre acciones para no gatillar el rate-limit de Yahoo Finance
# (sin pausa, info llega parcial y ROE/payout salen vacios).
PAUSA_YF = 4  # segundos

def main():
    no_git = "--no-git" in sys.argv
    acciones, fallidos = [], []
    for i, (tk, meta) in enumerate(WATCHLIST.items()):
        if i > 0:
            time.sleep(PAUSA_YF)
        try:
            acciones.append(procesar(tk, meta))
            log(f"  OK {tk}")
        except Exception as e:
            log(f"  FALLO {tk}: {e}")
            fallidos.append({"ticker": tk, "error": str(e)})

    if not acciones:
        log("Ninguna accion procesada — no se escribe el JSON (se conserva el anterior).")
        sys.exit(1)

    data = {
        "fecha_actualizacion": datetime.now(timezone.utc).isoformat(),
        "n_acciones": len(acciones),
        "acciones": acciones,
        "fallidos": fallidos,
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    log(f"JSON escrito: {OUT} ({OUT.stat().st_size//1024} KB)")

    if no_git:
        return
    # push con lock compartido para evitar choques entre tareas
    if False:  # [PLAN-B] git neutralizado: publicar.py se encarga del push
        try:
            subprocess.run(["git", "pull", "--no-rebase", "--no-edit"], cwd=REPO, check=False,
                           capture_output=True)
            subprocess.run(["git", "add", OUT.name], cwd=REPO, check=True, capture_output=True)
            r = subprocess.run(["git", "commit", "-m", "data: acciones USA"], cwd=REPO,
                               capture_output=True, text=True)
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
