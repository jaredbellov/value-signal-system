"""
GLD SCORE - Sistema de scoring para invertir en oro (GLD)
=========================================================
Calcula un score 0-100 basado en 7 señales fundamentales del oro:

1. Drawdown vs 52w max (20%)       - Mayor caída = oportunidad
2. Momentum 12-1m Jegadeesh-Titman (15%) - Trend del año
3. Ratio Oro/S&P 500 (15%)         - Oro barato vs equities
4. Ratio Oro/Plata (10%)           - Oro caro/barato vs plata
5. DXY índice del dólar (15%)      - Dólar fuerte = oro reprimido
6. Real yields TIPS 10y (15%)      - Real yields bajos = oro brilla
7. VIX miedo de mercado (10%)      - VIX alto = demanda safe-haven

Output: dict con score, zona, multiplicador, y desglose
"""
import json
import logging
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf

SCRIPT_DIR = Path(__file__).parent
LOG_DIR = SCRIPT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "gld_score.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

OUTPUT_FILE = SCRIPT_DIR / "gld_data.json"

# Pesos de cada senal (suman 100)
PESOS = {
    "drawdown":     20,
    "momentum":     15,
    "ratio_sp500":  15,
    "ratio_silver": 10,
    "dxy":          15,
    "real_yield":   15,
    "vix":          10,
}

# Zonas de scoring (igual que SP500)
def zona_de_score(score):
    if score < 25:
        return "CARO", 0.5
    elif score < 50:
        return "NEUTRAL", 1.0
    elif score < 75:
        return "ATRACTIVO", 1.5
    else:
        return "OPORTUNIDAD", 2.5


# ============================================================
# FUENTES DE DATOS
# ============================================================

def fetch_yahoo_history(ticker, period="2y"):
    """Descarga historial de Yahoo. Retorna DataFrame o None.

    Filtra automaticamente filas con Close=NaN para evitar el bug donde
    Yahoo sirve dias con volumen pero sin OHLC (pasa con tickers grandes
    como SCHD/JEPQ/SPY/GLD/IBIT en ciertos momentos).
    """
    try:
        data = yf.Ticker(ticker).history(period=period)
        if data.empty:
            log.warning(f"  {ticker}: sin datos en Yahoo")
            return None

        # Filtrar filas con Close NaN (bug intermitente de Yahoo)
        filas_antes = len(data)
        data = data.dropna(subset=["Close"]).copy()
        filas_despues = len(data)
        if filas_antes != filas_despues:
            log.warning(f"  {ticker}: filtradas {filas_antes - filas_despues} filas con Close=NaN")

        if data.empty:
            log.error(f"  {ticker}: dataframe vacio despues de filtrar NaN")
            return None

        return data
    except Exception as e:
        log.error(f"  {ticker}: error Yahoo: {e}")
        return None


def load_env_file():
    """Lee el archivo .env y carga las variables al entorno."""
    import os
    env_path = SCRIPT_DIR / ".env"
    if not env_path.exists():
        return {}
    config = {}
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    config[key.strip()] = value.strip()
        return config
    except Exception as e:
        log.warning(f"Error leyendo .env: {e}")
        return {}


def fetch_fred_real_yield():
    """
    Descarga 10-year TIPS yield desde FRED (DFII10) via API JSON oficial.
    Requiere FRED_API_KEY en .env (obtener gratis en fred.stlouisfed.org).
    """
    import json as _json
    env = load_env_file()
    api_key = env.get("FRED_API_KEY", "").strip()
    if not api_key or api_key == "PEGAR_KEY_AQUI":
        log.warning("  FRED: API key no configurada en .env (real_yield sera None)")
        return None

    # Endpoint JSON oficial - mucho mas rapido que el CSV
    url = (
        f"https://api.stlouisfed.org/fred/series/observations"
        f"?series_id=DFII10&api_key={api_key}&file_type=json"
        f"&sort_order=desc&limit=10"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Value Signal GLD Score"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        # Buscar la observacion mas reciente con valor numerico
        for obs in data.get("observations", []):
            val = obs.get("value", ".").strip()
            if val and val != ".":
                fecha = obs.get("date", "?")
                valor = float(val)
                log.info(f"  FRED DFII10 (TIPS 10y): {valor}% al {fecha}")
                return valor
        log.warning("  FRED: no se encontro valor reciente de DFII10")
        return None
    except Exception as e:
        log.error(f"  FRED error: {e}")
        return None


# ============================================================
# CALCULO DE SUB-SCORES (cada uno devuelve 0-100)
# ============================================================

def calc_drawdown(gld_hist):
    """Drawdown vs maximo 52 semanas. Mayor caida = score mas alto."""
    if gld_hist is None or len(gld_hist) < 50:
        return None, None
    precio_actual = float(gld_hist["Close"].iloc[-1])
    # Ultimas 252 sesiones = ~52 semanas
    ventana = gld_hist["Close"].iloc[-252:] if len(gld_hist) >= 252 else gld_hist["Close"]
    max_52w = float(ventana.max())
    drawdown_pct = (precio_actual - max_52w) / max_52w * 100  # negativo si por debajo del max
    # Score: 0% drawdown -> 0 score; -20% drawdown -> 100 score
    score = min(100, max(0, abs(drawdown_pct) * 5))
    return score, {"drawdown_pct": round(drawdown_pct, 2), "max_52w": round(max_52w, 2), "precio_actual": round(precio_actual, 2)}


def calc_momentum_12_1(gld_hist):
    """Momentum 12-1m: retorno entre hace 12m y hace 1m (Jegadeesh-Titman)."""
    if gld_hist is None or len(gld_hist) < 252:
        return None, None
    # Aproximacion: usar dias de trading (252/ano)
    precio_hace_12m = float(gld_hist["Close"].iloc[-252])
    precio_hace_1m = float(gld_hist["Close"].iloc[-21])
    ret_12_1 = (precio_hace_1m / precio_hace_12m - 1) * 100
    # Scoring: >+20% -> 100, +5% -> 60, 0% -> 50, -10% -> 0
    if ret_12_1 > 20:
        score = 100
    elif ret_12_1 < -20:
        score = 0
    else:
        score = 50 + ret_12_1 * 2.5  # cada 1% suma/resta 2.5 puntos
        score = max(0, min(100, score))
    return score, {"ret_12_1_pct": round(ret_12_1, 2)}


def calc_ratio_sp500(gld_hist, sp_hist):
    """Ratio GLD/S&P. Bajo = oro barato vs equities = score alto."""
    if gld_hist is None or sp_hist is None:
        return None, None
    gld_actual = float(gld_hist["Close"].iloc[-1])
    sp_actual = float(sp_hist["Close"].iloc[-1])
    ratio = gld_actual / sp_actual
    # Tomar percentil del ratio en los ultimos 2 anos
    # Alinear fechas
    ratios = (gld_hist["Close"] / sp_hist["Close"].reindex(gld_hist.index, method="ffill")).dropna()
    if len(ratios) < 50:
        return None, None
    percentil = (ratios < ratio).sum() / len(ratios) * 100
    # Bajo percentil = ratio bajo = oro barato = score alto
    score = 100 - percentil
    return score, {"ratio_actual": round(ratio, 4), "percentil_2y": round(percentil, 1)}


def calc_ratio_silver(gld_hist, sl_hist):
    """Ratio Oro/Plata. >80 oro caro, <50 oro barato."""
    if gld_hist is None or sl_hist is None:
        return None, None
    gld_actual = float(gld_hist["Close"].iloc[-1])
    sl_actual = float(sl_hist["Close"].iloc[-1])
    # OJO: GLD es 1/10 oz aprox, no precio spot. SI=F es precio plata futuro.
    # Para ratio puro de spot, hay que multiplicar GLD por 10 aprox.
    # Simplificacion: usamos los precios tal cual y normalizamos por historia.
    ratio = gld_actual / sl_actual
    # En vez de umbrales absolutos (que dependen del normalizado), usar percentil
    ratios_hist = (gld_hist["Close"] / sl_hist["Close"].reindex(gld_hist.index, method="ffill")).dropna()
    if len(ratios_hist) < 50:
        return None, None
    percentil = (ratios_hist < ratio).sum() / len(ratios_hist) * 100
    # Bajo percentil = ratio bajo = oro barato = score alto
    score = 100 - percentil
    return score, {"ratio_actual": round(ratio, 2), "percentil_2y": round(percentil, 1)}


def calc_dxy(dxy_hist):
    """DXY alto = dolar fuerte = oro reprimido = oportunidad."""
    if dxy_hist is None or len(dxy_hist) < 50:
        return None, None
    dxy_actual = float(dxy_hist["Close"].iloc[-1])
    valores = dxy_hist["Close"].dropna()
    percentil = (valores < dxy_actual).sum() / len(valores) * 100
    # Alto percentil = DXY alto = oro reprimido = score alto
    score = percentil
    return score, {"dxy_actual": round(dxy_actual, 2), "percentil_2y": round(percentil, 1)}


def calc_real_yield(real_yield_pct):
    """Real yield negativo = oro brilla. Real yield alto = bonos compiten."""
    if real_yield_pct is None:
        return None, None
    # Real yield <0% -> 100
    # Real yield 0-2% -> 100 a 50
    # Real yield 2-4% -> 50 a 0
    if real_yield_pct < 0:
        score = 100
    elif real_yield_pct < 2:
        score = 100 - (real_yield_pct * 25)
    elif real_yield_pct < 4:
        score = 50 - ((real_yield_pct - 2) * 25)
    else:
        score = 0
    score = max(0, min(100, score))
    return score, {"real_yield_pct": round(real_yield_pct, 2)}


def calc_vix(vix_hist):
    """VIX alto = miedo = demanda de oro."""
    if vix_hist is None or len(vix_hist) < 5:
        return None, None
    vix_actual = float(vix_hist["Close"].iloc[-1])
    # VIX <15 -> 0; 15-25 -> 50; >35 -> 100
    if vix_actual < 15:
        score = 0
    elif vix_actual < 25:
        score = (vix_actual - 15) * 5  # 15->0, 25->50
    elif vix_actual < 35:
        score = 50 + (vix_actual - 25) * 5  # 25->50, 35->100
    else:
        score = 100
    return score, {"vix_actual": round(vix_actual, 2)}


# ============================================================
# CALCULO PRINCIPAL
# ============================================================

def calcular_score_gld():
    """Funcion principal. Retorna dict con todo el analisis."""
    log.info("=" * 60)
    log.info("=== GLD Score - Calculo ===")
    log.info(f"Hora: {datetime.now().isoformat()}")

    # 1. Descargar datos
    log.info("\n--- Descargando datos ---")
    log.info("GLD...")
    gld_hist = fetch_yahoo_history("GLD", "5y")
    log.info("S&P 500 (^GSPC)...")
    sp_hist = fetch_yahoo_history("^GSPC", "2y")
    log.info("Silver futures (SI=F)...")
    sl_hist = fetch_yahoo_history("SI=F", "2y")
    log.info("DXY (DX-Y.NYB)...")
    dxy_hist = fetch_yahoo_history("DX-Y.NYB", "2y")
    log.info("VIX (^VIX)...")
    vix_hist = fetch_yahoo_history("^VIX", "2y")
    log.info("FRED Real Yield (DFII10)...")
    real_yield = fetch_fred_real_yield()

    # 2. Calcular sub-scores
    log.info("\n--- Calculando sub-scores ---")
    sub_scores = {}
    sub_detalles = {}

    s, d = calc_drawdown(gld_hist)
    sub_scores["drawdown"] = s
    sub_detalles["drawdown"] = d
    log.info(f"  Drawdown: score={s}, detalle={d}")

    s, d = calc_momentum_12_1(gld_hist)
    sub_scores["momentum"] = s
    sub_detalles["momentum"] = d
    log.info(f"  Momentum 12-1m: score={s}, detalle={d}")

    s, d = calc_ratio_sp500(gld_hist, sp_hist)
    sub_scores["ratio_sp500"] = s
    sub_detalles["ratio_sp500"] = d
    log.info(f"  Ratio Oro/SP500: score={s}, detalle={d}")

    s, d = calc_ratio_silver(gld_hist, sl_hist)
    sub_scores["ratio_silver"] = s
    sub_detalles["ratio_silver"] = d
    log.info(f"  Ratio Oro/Plata: score={s}, detalle={d}")

    s, d = calc_dxy(dxy_hist)
    sub_scores["dxy"] = s
    sub_detalles["dxy"] = d
    log.info(f"  DXY: score={s}, detalle={d}")

    s, d = calc_real_yield(real_yield)
    sub_scores["real_yield"] = s
    sub_detalles["real_yield"] = d
    log.info(f"  Real yield: score={s}, detalle={d}")

    s, d = calc_vix(vix_hist)
    sub_scores["vix"] = s
    sub_detalles["vix"] = d
    log.info(f"  VIX: score={s}, detalle={d}")

    # 3. Score ponderado total
    log.info("\n--- Score final ---")
    score_total = 0
    peso_aplicado = 0
    for nombre, peso in PESOS.items():
        if sub_scores.get(nombre) is not None:
            score_total += sub_scores[nombre] * peso
            peso_aplicado += peso

    if peso_aplicado == 0:
        log.error("No se pudo calcular ninguna senal!")
        return None

    # Normalizar al peso efectivamente aplicado (si alguna senal falta)
    score_final = score_total / peso_aplicado
    zona, multiplicador = zona_de_score(score_final)

    log.info(f"Score final: {round(score_final, 1)} / 100")
    log.info(f"Zona: {zona} (multiplicador {multiplicador}x)")

    # 4. Precio actual de GLD
    gld_actual = None
    if gld_hist is not None:
        gld_actual = round(float(gld_hist["Close"].iloc[-1]), 2)

    # Historico de precios de GLD para el grafico del dashboard
    # Guardamos historico completo para el selector de periodo (1Y/3Y/5Y)
    historico_gld = []
    if gld_hist is not None:
        # Guardamos 5 anos para el selector de periodo del dashboard (1Y/3Y/5Y)
        ventana = gld_hist["Close"]
        for fecha, precio in ventana.items():
            historico_gld.append({
                "fecha": fecha.strftime("%Y-%m-%d"),
                "precio": round(float(precio), 2),
            })

    resultado = {
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "precio_gld": gld_actual,
        "score_final": round(score_final, 1),
        "zona": zona,
        "multiplicador": multiplicador,
        "peso_aplicado_pct": peso_aplicado,
        "sub_scores": {k: (round(v, 1) if v is not None else None) for k, v in sub_scores.items()},
        "sub_detalles": sub_detalles,
        "pesos": PESOS,
        "historico_gld": historico_gld,
    }

    return resultado


def main():
    resultado = calcular_score_gld()
    if resultado is None:
        log.error("Calculo fallido. No se guarda gld_data.json")
        return 1

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)
    log.info(f"\nGuardado: {OUTPUT_FILE}")

    # Git pull/commit/push automatico
    git_sync_and_push()

    log.info(f"=== FIN ({datetime.now().strftime('%H:%M:%S')}) ===")
    return 0


def ejecutar_git(args, cwd=None):
    """Ejecuta un comando git y devuelve (exit_code, stdout, stderr)."""
    import subprocess as _sp
    cwd = cwd or SCRIPT_DIR
    try:
        result = _sp.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return result.returncode, result.stdout, result.stderr
    except Exception as e:
        return -1, "", str(e)


def git_sync_and_push():
    """Pull con rebase + autostash, commit, push con retry."""
    # [PLAN-B] git neutralizado: publicar.py se encarga del push
    return
    log.info("--- Git sync ---")

    # 1. Verificar si hay cambios en gld_data.json
    code, out, err = ejecutar_git(["status", "--porcelain", "gld_data.json"])
    if not out.strip():
        log.info("Sin cambios en gld_data.json, nada que commitear")
        return

    # 2. Pull con rebase + autostash (maneja races con otras tareas)
    code, out, err = ejecutar_git(["pull", "--rebase", "--autostash"])
    if code != 0:
        log.warning(f"git pull devolvio codigo {code}: {err}")
    else:
        log.info("git pull OK")

    # 3. Add + commit
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    code, out, err = ejecutar_git(["add", "gld_data.json"])
    if code != 0:
        log.error(f"git add fallo: {err}")
        return

    code, out, err = ejecutar_git(["commit", "-m", f"chore: update GLD score {ts} [skip ci]"])
    if code != 0:
        log.warning(f"git commit dijo: {out or err}")
        return
    log.info("git commit OK")

    # 4. Push con retry (1 intento si falla)
    code, out, err = ejecutar_git(["push"])
    if code != 0:
        log.warning(f"git push fallo (intento 1): {err}")
        log.info("Reintentando: pull --rebase --autostash + push...")
        code_pull, _, err_pull = ejecutar_git(["pull", "--rebase", "--autostash"])
        if code_pull != 0:
            log.error(f"git pull en retry fallo: {err_pull}")
            return
        code, out, err = ejecutar_git(["push"])
        if code != 0:
            log.error(f"git push fallo (intento 2): {err}")
            return
        log.info("git push OK (en retry)")
    else:
        log.info("git push OK")


if __name__ == "__main__":
    main()
