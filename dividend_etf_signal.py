"""
DIVIDEND ETF SIGNAL
====================
Sistema de scoring para ETFs de dividendos (SCHD, JEPQ).

Metodología:
- DY actual vs promedio histórico 3y (35%)
- Drawdown vs 52-week high (25%)
- Balance CAGR precio vs DY (15%)
- Momentum 12-1 mes (10%)
- Dividend Growth Rate sostenido (15%)

Output: score 0-100 + zona (CARO/NEUTRAL/ATRACTIVO/OPORTUNIDAD)
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Optional

import yfinance as yf
import pandas as pd
import numpy as np

log = logging.getLogger(__name__)

# ============================================================================
# CONFIGURACIÓN POR ETF
# ============================================================================

DIVIDEND_ETFS = {
    "SCHD": {
        "name": "SCHD",
        "long_name": "Schwab US Dividend Equity ETF",
        "description": (
            "ETF pasivo que replica el Dow Jones US Dividend 100 Index. "
            "Selecciona ~100 acciones USA con: (1) historial de pago de dividendos "
            "≥10 años, (2) ratios financieros sólidos vs sus pares "
            "(ROE, deuda/cash flow), (3) yield atractivo. "
            "Excluye REITs. Top holdings históricos: AbbVie, Cisco, Verizon, "
            "Pfizer, Texas Instruments, Coca-Cola. Dividendos cualificados "
            "(ventaja fiscal). Expense ratio 0.06%. Trimestral."
        ),
        "type": "Pasivo - Dividend Growth",
        "inception": "2011-10-20",
        "expense_ratio": 0.0006,
        "frequency": "Trimestral",
        "yahoo_ticker": "SCHD",
        "min_history_years": 3,
    },
    "JEPQ": {
        "name": "JEPQ",
        "long_name": "JPMorgan Nasdaq Equity Premium Income ETF",
        "description": (
            "ETF activo de JPMorgan. Estrategia: (1) ~80% portfolio en acciones "
            "del Nasdaq-100 seleccionadas con criterios ESG y data science, "
            "(2) ~20% en ELNs (notas equity-linked) que ejecutan covered calls "
            "sobre el Nasdaq-100. Las primas de las calls generan ingreso mensual. "
            "Trade-off: yield alto (~10%) a cambio de capear upside en mercados "
            "alcistas fuertes. Dividendos NO cualificados. Expense ratio 0.35%. "
            "Pago mensual. Gestor: Hamilton Reiner."
        ),
        "type": "Activo - Covered Call Income",
        "inception": "2022-05-03",
        "expense_ratio": 0.0035,
        "frequency": "Mensual",
        "yahoo_ticker": "JEPQ",
        "min_history_years": 1,  # JEPQ solo tiene ~4 años
    },
    "DGRO": {
        "name": "DGRO",
        "long_name": "iShares Core Dividend Growth ETF",
        "description": (
            "ETF pasivo que replica el Morningstar US Dividend Growth Index. "
            "~440 empresas USA con 5+ anos consecutivos subiendo su dividendo, "
            "payout <75% y exclusion del decil de mayor yield (filtro anti-trampa). "
            "Top holdings: JPMorgan, Microsoft, AbbVie, ExxonMobil, J&J. "
            "Yield ~2% que crece ~6-7% anual: el interes compuesto del dividendo. "
            "Dividendos cualificados. Expense ratio 0.08%. Trimestral."
        ),
        "type": "Pasivo - Dividend Growth",
        "inception": "2014-06-10",
        "expense_ratio": 0.0008,
        "frequency": "Trimestral",
        "yahoo_ticker": "DGRO",
        "min_history_years": 3,
    },
    "VNQ": {
        "name": "VNQ",
        "long_name": "Vanguard Real Estate ETF",
        "description": (
            "REIT-ETF que replica el MSCI US Investable Market Real Estate 25/50 "
            "Index. ~160 REITs de EE.UU.: centros comerciales, bodegas/logistica, "
            "oficinas, data centers, torres de telecom, residencial. El mas grande "
            "y liquido del sector. Por ley los REITs reparten ~90% de utilidades: "
            "el retorno va por el DIVIDENDO, no por apreciacion (CAGR de precio "
            "bajo es normal). Sensible a tasas de interes. OJO: parte del yield es "
            "retorno de capital por depreciacion contable. Expense ratio 0.13%. "
            "Pago trimestral."
        ),
        "type": "REIT - Real Estate USA",
        "inception": "2004-09-23",
        "expense_ratio": 0.0013,
        "frequency": "Trimestral",
        "yahoo_ticker": "VNQ",
        "min_history_years": 3,
    },
    "VNQI": {
        "name": "VNQI",
        "long_name": "Vanguard Global ex-US Real Estate ETF",
        "description": (
            "REIT-ETF internacional: replica el S&P Global ex-US Property Index. "
            ">700 REITs y empresas inmobiliarias fuera de EE.UU. (Asia-Pacifico, "
            "Europa, mercados emergentes). Mayor yield y valoracion mas barata "
            "(P/B ~0.9x) que los REITs USA. NO correlacionado con VNQ: aporta "
            "diversificacion real, no duplica posicion. Mismo criterio de REIT: se "
            "juzga por yield sostenible y crecimiento del dividendo, no por precio. "
            "Sensible a tasas y a tipo de cambio. Expense ratio 0.12%. "
            "Frecuencia variable (trimestral/semestral segun distribuciones)."
        ),
        "type": "REIT - Real Estate Internacional",
        "inception": "2010-11-01",
        "expense_ratio": 0.0012,
        "frequency": "Variable",
        "yahoo_ticker": "VNQI",
        "min_history_years": 3,
    },
}

# ============================================================================
# DESCARGA DE DATOS
# ============================================================================

def fetch_etf_data(ticker: str, years: int = 5) -> Optional[pd.DataFrame]:
    """
    Descarga precios diarios y dividendos del ETF.
    Devuelve DataFrame con columnas: Close, Dividends.
    """
    end = datetime.now()
    start = end - timedelta(days=365 * years + 30)

    for attempt in range(3):
        try:
            t = yf.Ticker(ticker)
            df = t.history(start=start.strftime('%Y-%m-%d'),
                           end=end.strftime('%Y-%m-%d'),
                           auto_adjust=False)
            if df.empty:
                if attempt < 2:
                    time.sleep(2)
                    continue
                log.warning(f"Sin datos para {ticker}")
                return None
            return df
        except Exception as e:
            log.warning(f"Intento {attempt+1} falló para {ticker}: {e}")
            if attempt < 2:
                time.sleep(2)
    return None


def fetch_usd_clp() -> Optional[float]:
    """Obtiene el tipo de cambio USD/CLP actual."""
    for attempt in range(3):
        try:
            t = yf.Ticker("USDCLP=X")
            hist = t.history(period="5d")
            if not hist.empty:
                return float(hist['Close'].iloc[-1])
        except Exception:
            if attempt < 2:
                time.sleep(2)
    return None


# ============================================================================
# CÁLCULOS DE INDICADORES
# ============================================================================

def calcular_dy_actual(df: pd.DataFrame, lookback_days: int = 365) -> Optional[float]:
    """
    DY actual = dividendos pagados últimos 365 días / precio actual.
    """
    if df is None or df.empty:
        return None

    # Filtrar dividendos del último año
    end_date = df.index[-1]
    start_date = end_date - timedelta(days=lookback_days)
    divs_ttm = df.loc[start_date:end_date, 'Dividends'].sum()

    precio_actual = df['Close'].iloc[-1]
    if precio_actual <= 0:
        return None

    return float(divs_ttm / precio_actual)


def calcular_dy_historico(df: pd.DataFrame, years: int = 3) -> Optional[float]:
    """
    DY promedio de los últimos N años.
    Para cada día, calcula DY trailing 12 meses y promedia.
    """
    if df is None or df.empty:
        return None

    end_date = df.index[-1]
    start_date = end_date - timedelta(days=365 * years)

    # Para cada año, calcular DY anual
    yields = []
    current = end_date
    for _ in range(years):
        year_start = current - timedelta(days=365)
        divs_year = df.loc[year_start:current, 'Dividends'].sum()
        # Precio promedio del año
        precios_year = df.loc[year_start:current, 'Close']
        if len(precios_year) > 0 and precios_year.mean() > 0:
            yield_y = float(divs_year / precios_year.mean())
            yields.append(yield_y)
        current = year_start

    if not yields:
        return None
    return float(np.mean(yields))


def calcular_drawdown(df: pd.DataFrame, days: int = 252) -> Optional[float]:
    """
    Drawdown vs máximo de los últimos N días.
    Devuelve número negativo o cero (ej. -0.05 = caída 5%).
    """
    if df is None or df.empty:
        return None

    recent = df.tail(days)
    if len(recent) < 5:
        return None

    max_price = recent['Close'].max()
    current_price = recent['Close'].iloc[-1]

    if max_price <= 0:
        return None

    return float((current_price - max_price) / max_price)


def calcular_cagr_precio(df: pd.DataFrame, years: int = 3) -> Optional[float]:
    """CAGR del precio (sin dividendos) últimos N años."""
    if df is None or df.empty:
        return None

    end_date = df.index[-1]
    start_date = end_date - timedelta(days=365 * years)

    # Buscar primer precio disponible cerca de start_date
    df_filtered = df.loc[start_date:end_date]
    if len(df_filtered) < 30:
        return None

    inicio = float(df_filtered['Close'].iloc[0])
    fin = float(df_filtered['Close'].iloc[-1])

    if inicio <= 0:
        return None

    years_actual = (df_filtered.index[-1] - df_filtered.index[0]).days / 365.0
    if years_actual < 0.5:
        return None

    cagr = (fin / inicio) ** (1 / years_actual) - 1
    return float(cagr)


def calcular_dividend_growth_rate(df: pd.DataFrame, years: int = 3) -> tuple:
    """
    Dividend Growth Rate (DGR) anualizado. Devuelve (dgr, confiable).
    Compara dividendos del anio mas reciente vs hace N anios.

    Guard: marca confiable=False cuando el dato no es comparable (ETFs con
    distribuciones irregulares, ej. VNQI): si el numero de pagos difiere entre
    la ventana reciente y la antigua, o si el DGR sale de un rango sano.
    """
    if df is None or df.empty:
        return None, False

    end_date = df.index[-1]
    start_recent = end_date - timedelta(days=365)
    start_old = end_date - timedelta(days=365 * (years + 1))
    end_old = end_date - timedelta(days=365 * years)

    serie_recent = df.loc[start_recent:end_date, 'Dividends']
    serie_old = df.loc[start_old:end_old, 'Dividends']
    divs_recent = serie_recent.sum()
    divs_old = serie_old.sum()

    if divs_old <= 0:
        return None, False

    growth_total = divs_recent / divs_old
    if growth_total <= 0:
        return None, False

    dgr_annual = float(growth_total ** (1 / years) - 1)

    # --- Guard de confiabilidad ---
    n_pagos_recent = int((serie_recent > 0).sum())
    n_pagos_old = int((serie_old > 0).sum())
    confiable = True
    if n_pagos_recent != n_pagos_old:
        confiable = False          # distinto numero de pagos -> no comparable
    if dgr_annual < -0.30 or dgr_annual > 0.40:
        confiable = False          # fuera de rango sano para un ETF de dividendos
    return dgr_annual, confiable


def calcular_momentum(df: pd.DataFrame) -> Optional[float]:
    """
    Momentum 12-1: retorno últimos 12 meses excluyendo el mes más reciente.
    """
    if df is None or df.empty:
        return None

    end_date = df.index[-1]
    one_month_ago = end_date - timedelta(days=30)
    twelve_months_ago = end_date - timedelta(days=365)

    # Precio hace 12 meses y hace 1 mes
    df_12m = df.loc[df.index <= twelve_months_ago]
    df_1m = df.loc[df.index <= one_month_ago]

    if df_12m.empty or df_1m.empty:
        return None

    precio_12m = float(df_12m['Close'].iloc[-1])
    precio_1m = float(df_1m['Close'].iloc[-1])

    if precio_12m <= 0:
        return None

    return float((precio_1m - precio_12m) / precio_12m)


# ============================================================================
# CÁLCULO DEL SCORE
# ============================================================================

def score_dy_vs_historico(dy_actual: float, dy_historico: float) -> float:
    """
    Componente: DY actual vs su promedio histórico (35% del score).
    - DY actual / DY histórico = 1.0 → score 50 (neutral)
    - Ratio = 1.3 → score 75 (DY 30% arriba de su promedio, oportunidad)
    - Ratio = 0.7 → score 25 (DY 30% abajo, caro)
    """
    if dy_actual is None or dy_historico is None or dy_historico <= 0:
        return 50.0  # neutral si no hay datos

    ratio = dy_actual / dy_historico

    # Mapeo: ratio 0.5 → 0, ratio 1.0 → 50, ratio 1.5 → 100
    score = (ratio - 0.5) * 100
    return float(max(0, min(100, score)))


def score_drawdown(drawdown: float) -> float:
    """
    Componente: Drawdown vs máximo (25% del score).
    - 0% drawdown → score 0 (en máximos)
    - -10% → score 20
    - -20% → score 50
    - -30% → score 80
    - -40%+ → score 100
    """
    if drawdown is None:
        return 50.0

    dd_pct = abs(drawdown * 100)
    # Mapeo lineal hasta -40%
    score = (dd_pct / 40.0) * 100
    return float(max(0, min(100, score)))


def score_balance_cagr(cagr_precio: float, dy_actual: float) -> float:
    """
    Componente: Balance entre apreciación de capital y income (15%).
    - Si CAGR precio es positivo y DY también: bueno
    - Si CAGR muy negativo: malo (la acción está cayendo demasiado)
    - Si DY es muy alto pero CAGR negativo: trampa de yield potencial
    """
    if cagr_precio is None or dy_actual is None:
        return 50.0

    # Total return aproximado = CAGR precio + DY
    total_return = cagr_precio + dy_actual

    # Mapeo: 0% → 0, 5% → 30, 10% → 60, 15%+ → 100
    score = (total_return / 0.15) * 100
    return float(max(0, min(100, score)))


def score_momentum(momentum: float) -> float:
    """
    Componente: Momentum 12-1m (10%).
    - Momentum positivo → buena tendencia
    - Momentum negativo → tendencia bajista
    """
    if momentum is None:
        return 50.0

    # Mapeo: -20% → 0, 0% → 50, 20%+ → 100
    score = 50 + (momentum * 100 * 2.5)
    return float(max(0, min(100, score)))


def score_dgr(dgr: float) -> float:
    """
    Componente: Dividend Growth Rate (15%).
    - DGR ≥ 8% → score 100 (crecimiento robusto)
    - DGR ≥ 5% → score 75 (saludable)
    - DGR ≥ 2% → score 50 (normal)
    - DGR ≥ 0% → score 25
    - DGR < 0% → score 0 (dividendos cayendo, problema)
    """
    if dgr is None:
        return 50.0

    if dgr >= 0.08:
        return 100.0
    elif dgr >= 0.05:
        return 75.0
    elif dgr >= 0.02:
        return 50.0
    elif dgr >= 0:
        return 25.0
    else:
        return 0.0


def calcular_score_total(componentes: dict, excluir: list = None) -> float:
    """Combina los 5 componentes con sus pesos.

    Si 'excluir' trae componentes no confiables (ej. dgr irregular), se quitan
    del calculo y su peso se REDISTRIBUYE proporcionalmente entre los demas
    (renormalizacion), en vez de inyectar un valor neutro que diluye el score.
    """
    pesos = {
        "dy_vs_historico": 0.35,
        "drawdown": 0.25,
        "balance_cagr": 0.15,
        "momentum": 0.10,
        "dgr": 0.15,
    }
    excluir = set(excluir or [])
    pesos_activos = {k: p for k, p in pesos.items() if k not in excluir}
    total_peso = sum(pesos_activos.values())
    if total_peso <= 0:
        return 50.0
    score_total = 0.0
    for k, peso in pesos_activos.items():
        valor = componentes.get(k, 50.0)
        score_total += valor * (peso / total_peso)
    return round(score_total, 1)


def determinar_zona(score: float) -> tuple:
    """Devuelve (zona, multiplicador, emoji)."""
    if score < 25:
        return ("CARO", 0.5, "🔴")
    elif score < 50:
        return ("NEUTRAL", 1.0, "🟡")
    elif score < 75:
        return ("ATRACTIVO", 1.5, "🟢")
    else:
        return ("OPORTUNIDAD", 2.5, "🟢🟢")


# ============================================================================
# FUNCIÓN PRINCIPAL
# ============================================================================

def analyze_dividend_etf(ticker: str, aporte_base_usd: float = 100, usd_clp: float = None) -> Optional[dict]:
    """
    Análisis completo de un Dividend ETF.

    Devuelve dict con todos los indicadores y el score final.
    """
    config = DIVIDEND_ETFS.get(ticker.upper())
    if not config:
        log.error(f"Ticker no configurado: {ticker}")
        return None

    log.info(f"Analizando {ticker}...")
    df = fetch_etf_data(config['yahoo_ticker'], years=5)
    if df is None or df.empty:
        log.error(f"Sin datos para {ticker}")
        return None

    # Guard defensivo: limpiar filas con Close NaN antes de cualquier calculo.
    filas_antes = len(df)
    df = df.dropna(subset=["Close"]).copy()
    filas_despues = len(df)
    if filas_antes != filas_despues:
        log.info(f"{ticker}: filtradas {filas_antes - filas_despues} filas con NaN en Close")

    if df.empty:
        log.error(f"{ticker}: dataframe vacio despues de filtrar NaN - se rechaza")
        return None

    import math as _math_guard
    try:
        ultimo_precio = float(df["Close"].iloc[-1])
        if _math_guard.isnan(ultimo_precio) or ultimo_precio <= 0:
            log.error(f"{ticker}: ultimo precio invalido despues de filtrar ({ultimo_precio}) - se rechaza")
            return None
    except Exception as e:
        log.error(f"{ticker}: error validando ultimo precio: {e}")
        return None

    if filas_despues < 100:
        log.error(f"{ticker}: solo {filas_despues} filas validas (<100) - se rechaza")
        return None

    # Calcular indicadores
    dy_actual = calcular_dy_actual(df)
    dy_historico = calcular_dy_historico(df, years=3)
    drawdown = calcular_drawdown(df, days=252)
    cagr_precio_3y = calcular_cagr_precio(df, years=3)
    cagr_precio_5y = calcular_cagr_precio(df, years=5)
    dgr, dgr_confiable = calcular_dividend_growth_rate(df, years=3)
    momentum = calcular_momentum(df)

    # Calcular scores por componente
    componentes = {
        "dy_vs_historico": score_dy_vs_historico(dy_actual, dy_historico),
        "drawdown": score_drawdown(drawdown),
        "balance_cagr": score_balance_cagr(cagr_precio_3y, dy_actual),
        "momentum": score_momentum(momentum),
        "dgr": score_dgr(dgr if dgr_confiable else None),
    }

    # Score total y zona
    _excluir = [] if dgr_confiable else ['dgr']
    score_total = calcular_score_total(componentes, excluir=_excluir)
    zona, multiplicador, emoji = determinar_zona(score_total)

    # Precio actual
    precio_usd = float(df['Close'].iloc[-1])
    precio_clp = precio_usd * usd_clp if usd_clp else None

    # Aporte sugerido este mes
    aporte_sugerido_usd = aporte_base_usd * multiplicador

    return {
        "ticker": ticker,
        "name": config['name'],
        "long_name": config['long_name'],
        "description": config['description'],
        "type": config['type'],
        "inception": config['inception'],
        "expense_ratio": config['expense_ratio'],
        "frequency": config['frequency'],

        # Precios
        "precio_usd": round(precio_usd, 2),
        "precio_clp": round(precio_clp) if precio_clp else None,
        "usd_clp": round(usd_clp) if usd_clp else None,

        # Indicadores raw
        "dy_actual_pct": round(dy_actual * 100, 2) if dy_actual else None,
        "dy_historico_3y_pct": round(dy_historico * 100, 2) if dy_historico else None,
        "drawdown_pct": round(drawdown * 100, 2) if drawdown is not None else None,
        "cagr_precio_3y_pct": round(cagr_precio_3y * 100, 2) if cagr_precio_3y else None,
        "cagr_precio_5y_pct": round(cagr_precio_5y * 100, 2) if cagr_precio_5y else None,
        "dgr_3y_pct": round(dgr * 100, 2) if dgr is not None else None,
        "dgr_confiable": bool(dgr_confiable),
        "momentum_pct": round(momentum * 100, 2) if momentum else None,

        # Score
        "componentes": componentes,
        "score": score_total,
        "zona": zona,
        "multiplicador": multiplicador,
        "emoji": emoji,

        # Aporte
        "aporte_base_usd": aporte_base_usd,
        "aporte_sugerido_usd": round(aporte_sugerido_usd, 2),
        "aporte_sugerido_clp": round(aporte_sugerido_usd * usd_clp) if usd_clp else None,

        # Metadatos
        "fecha_analisis": datetime.now().isoformat(),
        "ultima_fecha_dato": df.index[-1].strftime('%Y-%m-%d'),

        # Historico de precios (5y) para grafico con selector en dashboard
        "historico": [
            {"fecha": idx.strftime('%Y-%m-%d'), "close": round(float(c), 2)}
            for idx, c in df["Close"].items()
        ],
    }


# ============================================================================
# CLI para testing
# ============================================================================

if __name__ == "__main__":
    import json
    import sys

    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

    print("=" * 60)
    print("DIVIDEND ETF SIGNAL - Test")
    print("=" * 60)

    usd_clp = fetch_usd_clp()
    print(f"\nUSD/CLP: ${usd_clp:.2f}\n" if usd_clp else "USD/CLP no disponible\n")

    aportes = {"SCHD": 140, "JEPQ": 60}

    for ticker in ["SCHD", "JEPQ"]:
        print(f"\n{'=' * 60}")
        print(f"  {ticker}")
        print(f"{'=' * 60}")

        result = analyze_dividend_etf(ticker, aporte_base_usd=aportes[ticker], usd_clp=usd_clp)
        if result:
            print(f"  Nombre: {result['long_name']}")
            print(f"  Tipo: {result['type']}")
            print(f"  Precio: ${result['precio_usd']} USD = ${result['precio_clp']:,} CLP" if result['precio_clp'] else f"  Precio: ${result['precio_usd']} USD")
            print()
            print(f"  DY actual: {result['dy_actual_pct']}%")
            print(f"  DY promedio 3y: {result['dy_historico_3y_pct']}%")
            print(f"  CAGR precio 3y: {result['cagr_precio_3y_pct']}%")
            print(f"  CAGR precio 5y: {result['cagr_precio_5y_pct']}%")
            print(f"  Dividend Growth Rate 3y: {result['dgr_3y_pct']}%")
            print(f"  Drawdown vs 52w high: {result['drawdown_pct']}%")
            print(f"  Momentum 12-1m: {result['momentum_pct']}%")
            print()
            print(f"  Componentes del score:")
            for k, v in result['componentes'].items():
                print(f"    {k}: {v:.1f}/100")
            print()
            print(f"  SCORE: {result['score']}/100")
            print(f"  ZONA: {result['emoji']} {result['zona']}")
            print(f"  Multiplicador: {result['multiplicador']}x")
            print(f"  Aporte sugerido: ${result['aporte_sugerido_usd']} USD = ${result['aporte_sugerido_clp']:,} CLP" if result['aporte_sugerido_clp'] else f"  Aporte: ${result['aporte_sugerido_usd']} USD")
        else:
            print("  ERROR: no se pudo analizar")

    print(f"\n{'=' * 60}\n")
