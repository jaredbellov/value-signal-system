"""PRECIO HISTORICO BCS - CAGR oficial desde Bolsa de Santiago"""
import logging
from datetime import datetime, timedelta
import requests

log = logging.getLogger("precio-historico-bcs")

BCS_HIST_URL = "https://www.bolsadesantiago.com/api/RV_Instrumentos/getPointHistGAT"

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Origin": "https://www.bolsadesantiago.com",
    "Referer": "https://www.bolsadesantiago.com/",
    "Accept": "application/json, text/plain, */*",
}

TIMEOUT = 15
_CACHE = {}


def _fetch_historico_bcs(ticker):
    if ticker in _CACHE:
        return _CACHE[ticker]
    params = {"nemo": ticker.upper()}
    try:
        resp = requests.get(BCS_HIST_URL, params=params, headers=DEFAULT_HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.warning(f"BCS hist fallo para {ticker}: {e}")
        return None
    if isinstance(data, dict):
        for key in ("listaResult", "data", "result", "items"):
            if key in data and isinstance(data[key], list):
                data = data[key]
                break
        else:
            log.warning(f"BCS hist {ticker}: respuesta dict sin lista reconocida")
            return None
    if not isinstance(data, list) or not data:
        log.warning(f"BCS hist {ticker}: respuesta vacia")
        return None
    _CACHE[ticker] = data
    log.info(f"BCS hist {ticker}: {len(data)} puntos descargados")
    return data


def _parse_fecha(s):
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            continue
    return None


def _precio_en_fecha(historico, fecha_objetivo):
    candidatos = []
    for punto in historico:
        f = _parse_fecha(punto.get("DATE", ""))
        if f is None:
            continue
        if f <= fecha_objetivo:
            close = punto.get("CLOSE") or punto.get("ADJ_CLOSE")
            if close and close > 0:
                candidatos.append((f, close))
    if not candidatos:
        return None
    candidatos.sort(key=lambda x: x[0], reverse=True)
    return candidatos[0][1]


def obtener_cagr(ticker, anios=3):
    historico = _fetch_historico_bcs(ticker)
    if not historico:
        return None
    hoy = datetime.now()
    fecha_pasada = hoy - timedelta(days=anios * 365)
    precio_inicial = _precio_en_fecha(historico, fecha_pasada)
    precio_final = _precio_en_fecha(historico, hoy)
    if not precio_inicial or not precio_final or precio_inicial <= 0:
        log.warning(f"CAGR {ticker} {anios}y: precios insuficientes")
        return None
    cagr = (precio_final / precio_inicial) ** (1 / anios) - 1
    log.info(f"CAGR {ticker} {anios}y: {cagr*100:.2f}% ({precio_inicial:,.0f} -> {precio_final:,.0f})")
    return cagr


def obtener_serie_mensual(ticker, anios=5):
    """Devuelve la serie de precios submuestreada a 1 punto por mes (ultimos
    'anios' anos): lista de {\"f\": \"YYYY-MM\", \"p\": precio}. Reutiliza el
    historico ya cacheado (mismo que usa el CAGR). Sin descargas extra."""
    historico = _fetch_historico_bcs(ticker)
    if not historico:
        return []
    hoy = datetime.now()
    desde = hoy - timedelta(days=anios * 365 + 15)
    por_mes = {}
    for punto in historico:
        f = _parse_fecha(punto.get("DATE", ""))
        if f is None or f < desde:
            continue
        close = punto.get("CLOSE") or punto.get("ADJ_CLOSE")
        if not close or close <= 0:
            continue
        clave = f.strftime("%Y-%m")
        # nos quedamos con el ultimo dato de cada mes (cierre de mes)
        if clave not in por_mes or f >= por_mes[clave][0]:
            por_mes[clave] = (f, close)
    serie = [{"f": k, "p": round(v[1], 2)} for k, v in sorted(por_mes.items())]
    log.info(f"Serie mensual {ticker}: {len(serie)} puntos ({anios}y)")
    return serie


def obtener_cagr_multi(ticker):
    return {
        "cagr_3y": obtener_cagr(ticker, 3),
        "cagr_5y": obtener_cagr(ticker, 5),
        "cagr_10y": obtener_cagr(ticker, 10),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "HABITAT"
    print(f"\n=== CAGR para {ticker} ===")
    resultados = obtener_cagr_multi(ticker)
    for k, v in resultados.items():
        if v is not None:
            print(f"  {k}: {v*100:.2f}%")
        else:
            print(f"  {k}: N/A")
