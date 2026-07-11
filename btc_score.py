"""
BTC SCORE - Sistema de scoring para invertir en Bitcoin (BTC-USD)
==================================================================
Calcula un score 0-100 basado en 5 senales core:

1. Drawdown vs ATH (20%)              - Caida desde maximo historico
2. Fear & Greed Index (25%)           - Sentimiento de alternative.me
3. Pi Cycle Top Indicator (15%)       - MA111 vs 2*MA350 (deteccion de topes)
4. Halving Cycle Position (25%)       - Ciclo de 4 anos (proximo: abril 2028)
5. Momentum 12-1m Jegadeesh-Titman (15%) - Trend del ultimo ano

Output: dict con score, zona, multiplicador, y desglose
Zonas: CARO (0.5x), NEUTRAL (1.0x), ATRACTIVO (1.5x), OPORTUNIDAD (2.5x)
"""
import json
import logging
import urllib.request
from datetime import datetime, timezone, date
from pathlib import Path

import yfinance as yf

SCRIPT_DIR = Path(__file__).parent
LOG_DIR = SCRIPT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "btc_score.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

OUTPUT_FILE = SCRIPT_DIR / "btc_data.json"

# Pesos de cada senal (suman 100)
PESOS = {
    "drawdown":       18,
    "fear_greed":     20,
    "pi_cycle":       12,
    "halving_cycle":  22,
    "momentum":       13,
    "mvrv":           15,
}

# Fecha del ultimo halving (importante para el ciclo)
LAST_HALVING = date(2024, 4, 19)  # 4to halving
NEXT_HALVING = date(2028, 4, 1)   # estimado (~4 anos)

# Zonas de scoring (igual que GLD/SP500)
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

def fetch_yahoo_history(ticker, period="5y"):
    """Descarga historial de Yahoo. BTC necesita 5y para Pi Cycle (350d MA).

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


def fetch_fear_greed():
    """
    Descarga Fear & Greed Index desde alternative.me (publico, sin key).
    Retorna: (value, classification) donde value es 0-100.
    """
    url = "https://api.alternative.me/fng/?limit=1"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Value Signal BTC Score"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if "data" in data and len(data["data"]) > 0:
            entry = data["data"][0]
            value = int(entry["value"])
            classification = entry.get("value_classification", "Unknown")
            log.info(f"  Fear & Greed: {value} ({classification})")
            return value, classification
        log.warning("  Fear & Greed: respuesta vacia")
        return None, None
    except Exception as e:
        log.error(f"  Fear & Greed error: {e}")
        return None, None


def fetch_mvrv_zscore():
    """
    Descarga MVRV Z-Score desde bgeometrics (API publica gratis, sin key).
    Retorna el ultimo valor numerico valido (NaN se descartan).

    El valor del dia actual suele ser NaN porque todavia no se cierra,
    asi que retrocedemos hasta encontrar el ultimo dato valido.
    """
    url = "https://api.bgeometrics.com/v1/mvrv-zscore"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Value Signal BTC Score"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        if not isinstance(data, list) or len(data) == 0:
            log.warning("  MVRV Z-Score: respuesta vacia o invalida")
            return None, None

        # Buscar el ultimo valor que NO sea NaN
        for entry in reversed(data):
            valor = entry.get("mvrvZscore")
            if valor and valor != "NaN":
                try:
                    z_score = float(valor)
                    fecha = entry.get("d", "—")
                    log.info(f"  MVRV Z-Score: {z_score:.3f} (fecha: {fecha})")
                    return z_score, fecha
                except (ValueError, TypeError):
                    continue

        log.warning("  MVRV Z-Score: no se encontro ningun valor valido")
        return None, None
    except Exception as e:
        log.error(f"  MVRV Z-Score error: {e}")
        return None, None


# ============================================================
# CALCULO DE SUB-SCORES (cada uno devuelve 0-100)
# ============================================================

def calc_drawdown_btc(btc_hist):
    """
    Drawdown desde ATH (all-time high) en los datos disponibles.
    En BTC los drawdowns son mas profundos: -50% = normal, -80% = panic buy.
    """
    if btc_hist is None or len(btc_hist) < 50:
        return None, None
    precio_actual = float(btc_hist["Close"].iloc[-1])
    ath = float(btc_hist["Close"].max())
    drawdown_pct = (precio_actual - ath) / ath * 100  # negativo si por debajo del ATH

    # Scoring ajustado para BTC (mas tolerante con drawdowns profundos):
    # 0% -> 0; -30% -> 50; -60% -> 100
    score = min(100, max(0, abs(drawdown_pct) * (100 / 60)))
    return score, {
        "drawdown_pct": round(drawdown_pct, 2),
        "ath": round(ath, 2),
        "precio_actual": round(precio_actual, 2),
    }


def calc_fear_greed_score(fg_value):
    """
    Fear & Greed Index 0-100 mapeado a nuestro score 0-100 (invertido).
    F&G alto = greed = mala oportunidad (score bajo).
    F&G bajo = fear = oportunidad (score alto).
    """
    if fg_value is None:
        return None, None
    # Inversion directa: score = 100 - fg_value
    score = 100 - fg_value
    return score, {"fg_value": fg_value}


def calc_mvrv(z_score):
    """
    Convierte el MVRV Z-Score a un score 0-100.

    Lectura clasica:
    - Z < 0:    zona verde (suelo) → score 100
    - Z 0-2:    zona neutral-acumulacion → score 90-60
    - Z 2-5:    caro → score 60-30
    - Z 5-7:    muy caro → score 30-10
    - Z > 7:    tope inminente → score 0
    """
    if z_score is None:
        return None, None

    if z_score < 0:
        score = 100
    elif z_score < 2:
        # 0 -> 90, 2 -> 60
        score = 90 - (z_score / 2) * 30
    elif z_score < 5:
        # 2 -> 60, 5 -> 30
        score = 60 - ((z_score - 2) / 3) * 30
    elif z_score < 7:
        # 5 -> 30, 7 -> 10
        score = 30 - ((z_score - 5) / 2) * 20
    else:
        score = 0

    score = max(0, min(100, score))
    return score, {"z_score": round(z_score, 3)}


def calc_pi_cycle(btc_hist):
    """
    Pi Cycle Top Indicator: cruce entre MA111 y 2*MA350.
    Cuando MA111 > 2*MA350 -> tope inminente -> score bajo (caro).
    Cuando MA111 << 2*MA350 -> safe zone -> score alto (oportunidad).
    Score basado en la distancia entre las dos lineas.
    """
    if btc_hist is None or len(btc_hist) < 350:
        return None, None
    close = btc_hist["Close"]
    ma_111 = close.rolling(window=111).mean()
    ma_350 = close.rolling(window=350).mean()

    ma_111_actual = float(ma_111.iloc[-1])
    ma_350_x2_actual = float(ma_350.iloc[-1]) * 2

    if ma_350_x2_actual == 0:
        return None, None

    # Ratio: cuanto mas chico mejor (MA111 muy por debajo de 2*MA350 = lejos del tope)
    ratio = ma_111_actual / ma_350_x2_actual

    # Scoring:
    # ratio < 0.5  -> score 100 (muy lejos del tope)
    # ratio = 0.7  -> score 75
    # ratio = 1.0  -> score 0 (tope inminente o ya cruzo)
    # ratio > 1.0  -> score 0 (peligroso)
    if ratio >= 1.0:
        score = 0
    elif ratio < 0.5:
        score = 100
    else:
        # Lineal entre 0.5 y 1.0
        score = (1.0 - ratio) * 200  # 0.5 -> 100, 1.0 -> 0
        score = max(0, min(100, score))

    return score, {
        "ma_111": round(ma_111_actual, 2),
        "ma_350_x2": round(ma_350_x2_actual, 2),
        "ratio": round(ratio, 3),
    }


def calc_halving_cycle():
    """
    Posicion en el ciclo de 4 anos del halving.
    Patron historico:
    - Mes 0-12 post-halving: bull market (zona optima)
    - Mes 12-18: tope
    - Mes 18-30: bear market (segunda mejor oportunidad)
    - Mes 30-48: acumulacion (zona optima de compra)
    """
    hoy = date.today()
    dias_desde_halving = (hoy - LAST_HALVING).days
    meses_desde_halving = dias_desde_halving / 30.4

    # Score por mes:
    # Mes 0-6: muy bueno (acumulacion temprana del bull) -> 80
    # Mes 6-12: bueno (bull en curso) -> 60
    # Mes 12-18: tope, peligroso -> 20
    # Mes 18-30: bear market -> 60-80 (depende)
    # Mes 30-48: pre-halving acumulacion -> 80-100

    m = meses_desde_halving

    if m < 6:
        score = 80  # acumulacion post-halving temprana
    elif m < 12:
        score = 60  # bull market activo
    elif m < 18:
        score = 20  # zona de tope
    elif m < 24:
        score = 40  # post-tope, bear market temprano
    elif m < 30:
        score = 65  # bear market medio (oportunidad)
    elif m < 42:
        score = 85  # acumulacion bear/pre-halving (mejor zona)
    elif m < 48:
        score = 95  # pre-halving inmediato (zona historica de compra)
    else:
        score = 50  # fuera del ciclo conocido

    fase = (
        "Acumulacion post-halving" if m < 6 else
        "Bull market activo"        if m < 12 else
        "Zona de tope"              if m < 18 else
        "Bear market temprano"      if m < 24 else
        "Bear market medio"         if m < 30 else
        "Acumulacion pre-halving"   if m < 42 else
        "Pre-halving inmediato"     if m < 48 else
        "Fuera del ciclo"
    )

    return score, {
        "meses_desde_halving": round(m, 1),
        "last_halving": LAST_HALVING.isoformat(),
        "next_halving_aprox": NEXT_HALVING.isoformat(),
        "fase": fase,
    }


def calc_momentum_btc(btc_hist):
    """Momentum 12-1m: retorno entre hace 12m y hace 1m (Jegadeesh-Titman)."""
    if btc_hist is None or len(btc_hist) < 252:
        return None, None
    precio_hace_12m = float(btc_hist["Close"].iloc[-252])
    precio_hace_1m = float(btc_hist["Close"].iloc[-21])
    ret_12_1 = (precio_hace_1m / precio_hace_12m - 1) * 100

    # En BTC los retornos anuales pueden ser mucho mas extremos
    # >+100% -> score 100 (muy alcista, pero tambien sospechoso de tope)
    # +30%  -> 70
    # 0%    -> 50
    # -30%  -> 30
    # <-50% -> 0

    # Score: inversion del retorno con saturacion
    # Reten que en BTC mucho momentum positivo NO es necesariamente bueno (puede ser tope)
    # Pero en general, momentum positivo = trend alcista validado
    if ret_12_1 > 100:
        score = 70  # extremo, peligroso de tope (no 100)
    elif ret_12_1 > 30:
        score = 60 + (ret_12_1 - 30) * (10 / 70)  # 30->60, 100->70
    elif ret_12_1 > -10:
        score = 50 + ret_12_1 * (10 / 40)  # -10->40, 30->60
    elif ret_12_1 > -50:
        score = 30 + (ret_12_1 + 50) * (10 / 40)  # -50->30, -10->40
    else:
        score = 30

    score = max(0, min(100, score))
    return score, {"ret_12_1_pct": round(ret_12_1, 2)}


# ============================================================
# CALCULO PRINCIPAL
# ============================================================

def calcular_score_btc():
    """Funcion principal. Retorna dict con todo el analisis."""
    log.info("=" * 60)
    log.info("=== BTC Score - Calculo ===")
    log.info(f"Hora: {datetime.now().isoformat()}")

    # 1. Descargar datos
    log.info("\n--- Descargando datos ---")
    log.info("BTC-USD (5y para Pi Cycle)...")
    btc_hist = fetch_yahoo_history("BTC-USD", "5y")
    log.info("IBIT (BlackRock Bitcoin ETF)...")
    ibit_hist = fetch_yahoo_history("IBIT", "1mo")
    log.info("Fear & Greed Index...")
    fg_value, fg_class = fetch_fear_greed()
    log.info("MVRV Z-Score (bgeometrics)...")
    mvrv_z, mvrv_fecha = fetch_mvrv_zscore()

    # 2. Calcular sub-scores
    log.info("\n--- Calculando sub-scores ---")
    sub_scores = {}
    sub_detalles = {}

    s, d = calc_drawdown_btc(btc_hist)
    sub_scores["drawdown"] = s
    sub_detalles["drawdown"] = d
    log.info(f"  Drawdown: score={s}, detalle={d}")

    s, d = calc_fear_greed_score(fg_value)
    if d is not None:
        d["classification"] = fg_class
    sub_scores["fear_greed"] = s
    sub_detalles["fear_greed"] = d
    log.info(f"  Fear & Greed: score={s}, detalle={d}")

    s, d = calc_pi_cycle(btc_hist)
    sub_scores["pi_cycle"] = s
    sub_detalles["pi_cycle"] = d
    log.info(f"  Pi Cycle: score={s}, detalle={d}")

    s, d = calc_halving_cycle()
    sub_scores["halving_cycle"] = s
    sub_detalles["halving_cycle"] = d
    log.info(f"  Halving Cycle: score={s}, detalle={d}")

    s, d = calc_momentum_btc(btc_hist)
    sub_scores["momentum"] = s
    sub_detalles["momentum"] = d
    log.info(f"  Momentum 12-1m: score={s}, detalle={d}")

    s, d = calc_mvrv(mvrv_z)
    if d is not None:
        d["fecha"] = mvrv_fecha
    sub_scores["mvrv"] = s
    sub_detalles["mvrv"] = d
    log.info(f"  MVRV Z-Score: score={s}, detalle={d}")

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

    score_final = score_total / peso_aplicado
    zona, multiplicador = zona_de_score(score_final)

    log.info(f"Score final: {round(score_final, 1)} / 100")
    log.info(f"Zona: {zona} (multiplicador {multiplicador}x)")

    # 4. Precio actual + historico
    btc_actual = None
    historico_btc = []
    if btc_hist is not None:
        btc_actual = round(float(btc_hist["Close"].iloc[-1]), 2)
        # Guardamos 5 anos para el selector de periodo del dashboard
        ventana = btc_hist["Close"]
        for fecha, precio in ventana.items():
            historico_btc.append({
                "fecha": fecha.strftime("%Y-%m-%d"),
                "precio": round(float(precio), 2),
            })

    # Precio actual de IBIT
    ibit_actual = None
    if ibit_hist is not None and not ibit_hist.empty:
        ibit_actual = round(float(ibit_hist["Close"].iloc[-1]), 2)

    resultado = {
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "precio_btc": btc_actual,
        "precio_ibit": ibit_actual,
        "score_final": round(score_final, 1),
        "zona": zona,
        "multiplicador": multiplicador,
        "peso_aplicado_pct": peso_aplicado,
        "sub_scores": {k: (round(v, 1) if v is not None else None) for k, v in sub_scores.items()},
        "sub_detalles": sub_detalles,
        "pesos": PESOS,
        "historico_btc": historico_btc,
    }

    return resultado


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

    code, out, err = ejecutar_git(["status", "--porcelain", "btc_data.json"])
    if not out.strip():
        log.info("Sin cambios en btc_data.json, nada que commitear")
        return

    code, out, err = ejecutar_git(["pull", "--rebase", "--autostash"])
    if code != 0:
        log.warning(f"git pull devolvio codigo {code}: {err}")
    else:
        log.info("git pull OK")

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    code, out, err = ejecutar_git(["add", "btc_data.json"])
    if code != 0:
        log.error(f"git add fallo: {err}")
        return

    code, out, err = ejecutar_git(["commit", "-m", f"chore: update BTC score {ts} [skip ci]"])
    if code != 0:
        log.warning(f"git commit dijo: {out or err}")
        return
    log.info("git commit OK")

    code, out, err = ejecutar_git(["push"])
    if code != 0:
        log.warning(f"git push fallo (intento 1): {err}")
        log.info("Reintentando: pull --rebase + push...")
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


def main():
    resultado = calcular_score_btc()
    if resultado is None:
        log.error("Calculo fallido. No se guarda btc_data.json")
        return 1

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)
    log.info(f"\nGuardado: {OUTPUT_FILE}")

    git_sync_and_push()

    log.info(f"=== FIN ({datetime.now().strftime('%H:%M:%S')}) ===")
    return 0


if __name__ == "__main__":
    main()
