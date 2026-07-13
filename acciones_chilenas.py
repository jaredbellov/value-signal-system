"""
ACCIONES CHILENAS - WATCHLIST DIVIDENDERO
==========================================
Sistema de analisis para watchlist de acciones chilenas dividenderas.

Formula Jared:
- DY 3y = promedio de dividendos (definitivos + provisorios) de los ÃšLTIMOS
  3 ANOS COMPLETOS (SIN incluir el ano actual) / precio actual BCS

Datos:
- Precio: Bolsa de Santiago (oficial, via Playwright)
- Dividendos: Bolsa de Santiago (clasificados: DEF / PROV / ADIC / EVENT)
- Datos financieros: CMF Chile (estados financieros oficiales)

Uso:
    python acciones_chilenas.py
    -> genera acciones_chilenas.json con todos los datos del watchlist
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Agregar la carpeta del MCP al sys.path
MCP_PATH = r"C:\mcp-bolsa"
if MCP_PATH not in sys.path:
    sys.path.insert(0, MCP_PATH)

# Importar funciones del MCP
from server import BCSClient, parsear_dividendos_de_variaciones
from cmf import (
    obtener_estados_financieros,
    extraer_cuentas_clave,
    calcular_indicadores_balance,
)
from precio_historico_bcs import obtener_cagr_multi

# ============================================================================
# CONFIGURACIÃ“N
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
)
log = logging.getLogger("acciones-chilenas")

# Carpeta del repo (donde se guarda el JSON y se hace git push)
REPO_PATH = Path(r"C:\value-signal-local\repo")
JSON_PATH = REPO_PATH / "acciones_chilenas.json"
FLUJOS_PATH = REPO_PATH / "flujos_historicos.json"
try:
    with open(FLUJOS_PATH, encoding="utf-8") as _f:
        _FLUJOS_CACHE = json.load(_f)
except Exception:
    _FLUJOS_CACHE = {}

# ============================================================================
# WATCHLIST - 11 acciones chilenas dividenderas
# Benchmarks calculados desde G = ROE Ã— (1 - Payout)
# ============================================================================

WATCHLIST = [
    # AFPs
    {
        "ticker": "HABITAT",
        "nombre": "AFP Habitat",
        "sector": "AFP",
        "clasificacion": "DGI",
        "benchmark_min": 0.06,
        "benchmark_max": 0.07,
        "descripcion": "Administradora de Fondos de Pensiones. Top 1 por AUM en Chile. ROE consistentemente alto (~45%), G=20% (tecnicamente Crecimiento pero paga dividendos altos).",
    },
    # Concesion
    {
        "ticker": "ZOFRI",
        "nombre": "Zona Franca de Iquique",
        "sector": "Concesion",
        "clasificacion": "Vaca Lechera",
        "benchmark_min": 0.08,
        "benchmark_max": 0.09,
        "descripcion": "Operadora de la zona franca de Iquique. Negocio estable y predecible. ROE 35%, payout 74%, G=9%.",
    },
    # Electricas
    {
        "ticker": "PEHUENCHE",
        "nombre": "Empresa Electrica Pehuenche",
        "sector": "Electrica",
        "clasificacion": "Vaca Lechera",
        "benchmark_min": 0.08,
        "benchmark_max": 0.09,
        "descripcion": "Generadora hidroelectrica del Maule. Filial de Enel Generacion. ROE 72%, payout >100% (paga mas que lo que gana, usa caja acumulada).",
    },
    {
        "ticker": "TRICAHUE",
        "nombre": "Tricahue",
        "sector": "Electrica",
        "clasificacion": "Vaca Lechera",
        "benchmark_min": 0.08,
        "benchmark_max": 0.09,
        "descripcion": "Electrica regulada chilena. ROE 8%, payout >100%, G negativo.",
    },
    {
        "ticker": "COLBUN",
        "nombre": "Colbun",
        "sector": "Electrica",
        "clasificacion": "Vaca Lechera",
        "benchmark_min": 0.08,
        "benchmark_max": 0.09,
        "descripcion": "Segunda mayor generadora electrica de Chile. Diversificada en hidro, gas, eolica, solar. ROE 4%, payout 79%, G=1%.",
    },
    {
        "ticker": "ENELGXCH",
        "nombre": "Enel Generacion Chile",
        "sector": "Electrica",
        "clasificacion": "Vaca Lechera",
        "benchmark_min": 0.08,
        "benchmark_max": 0.09,
        "descripcion": "Filial chilena de Enel. Principal generadora hidroelectrica del pais. ROE 19%, payout 62%, G=7%.",
    },
    # Gas
    {
        "ticker": "LIPIGAS",
        "nombre": "Lipigas",
        "sector": "Gas",
        "clasificacion": "DGI",
        "benchmark_min": 0.06,
        "benchmark_max": 0.07,
        "descripcion": "Distribuidora de GLP en Chile, Colombia y Peru. Crecimiento moderado por expansion regional. ROE 33%, payout 63%, G=12%.",
    },
    {
        "ticker": "NTGCLGAS",
        "nombre": "GASCO",
        "sector": "Gas",
        "clasificacion": "Vaca Lechera",
        "benchmark_min": 0.08,
        "benchmark_max": 0.09,
        "descripcion": "Distribucion de gas natural por redes en Chile. Negocio regulado, payout alto. ROE 13%, payout 68%, G=4%.",
    },
    # Fertilizantes
    {
        "ticker": "SOQUICOM",
        "nombre": "Soquimich Comercial",
        "sector": "Fertilizantes",
        "clasificacion": "DGI",
        "benchmark_min": 0.06,
        "benchmark_max": 0.07,
        "descripcion": "Distribuidora de fertilizantes especiales en Chile. Filial de SQM. ROE 11%, ciclo de payout variable, G=11%.",
    },
    # Holding
    {
        "ticker": "QUINENCO",
        "nombre": "Quinenco",
        "sector": "Holding",
        "clasificacion": "Vaca Lechera",
        "benchmark_min": 0.08,
        "benchmark_max": 0.09,
        "descripcion": "Holding del grupo Luksic. Participaciones en CCU, Banco de Chile, Madeco, Enex, Vapores. ROE 10%, payout 73%, G=3%.",
    },
    # Inmobiliario
    {
        "ticker": "CENCOMALLS",
        "nombre": "Cencosud Shopping",
        "sector": "Inmobiliario",
        "clasificacion": "Vaca Lechera",
        "benchmark_min": 0.08,
        "benchmark_max": 0.09,
        "descripcion": "Filial inmobiliaria de Cencosud. Operadora de centros comerciales en Chile, Peru y Colombia. ROE 11%, payout 46%, G=6%.",
    },
    {
        "ticker": "ENELAM",
        "nombre": "Enel Americas",
        "sector": "Electrica",
        "clasificacion": "Vaca Lechera",
        "benchmark_min": 0.08,
        "benchmark_max": 0.09,
        "descripcion": "Mayor electrica privada de Latam. Par regional de Enel Generacion Chile.",
    },
    {
        "ticker": "AGUAS-A",
        "nombre": "Aguas Andinas",
        "sector": "Sanitaria",
        "clasificacion": "Vaca Lechera",
        "benchmark_min": 0.08,
        "benchmark_max": 0.09,
        "descripcion": "Mayor sanitaria de Chile. Negocio regulado y muy estable, dividendos altos y predecibles. Precio via Yahoo (.SN) por bloqueo captcha BCS.",
    },
    {
        "ticker": "PARAUCO",
        "nombre": "Parque Arauco",
        "sector": "Inmobiliario",
        "clasificacion": "Vaca Lechera",
        "benchmark_min": 0.08,
        "benchmark_max": 0.09,
        "descripcion": "Operador de centros comerciales en Chile, Peru y Colombia. Dividendos estables. Precio via Yahoo (.SN) por bloqueo captcha BCS.",
    },
    {
        "ticker": "MALLPLAZA",
        "nombre": "Mall Plaza",
        "sector": "Inmobiliario",
        "clasificacion": "Vaca Lechera",
        "benchmark_min": 0.08,
        "benchmark_max": 0.09,
        "descripcion": "Operador de malls (grupo Falabella). Negocio inmobiliario de renta estable. Precio via Yahoo (.SN) por bloqueo captcha BCS.",
    },
    {
        "ticker": "CCU",
        "nombre": "Compania Cervecerias Unidas",
        "sector": "Embotelladora",
        "clasificacion": "DGI",
        "benchmark_min": 0.06,
        "benchmark_max": 0.07,
        "descripcion": "Mayor cervecera y embotelladora de Chile. Marcas lider, expansion regional. Precio via Yahoo (.SN) por bloqueo captcha BCS.",
    },
    # Quimica / Materiales
    {
        "ticker": "OXIQUIM",
        "nombre": "Oxiquim",
        "sector": "Quimica",
        "clasificacion": "DGI",
        "benchmark_min": 0.06,
        "benchmark_max": 0.07,
        "descripcion": "Quimica industrial: resinas para tableros de madera y terminales maritimos de graneles liquidos. ROE 21%, margen neto 11%, endeudamiento 46% (desapalancandose). Dividendos irregulares. Baja liquidez bursatil.",
    },
]

# ============================================================================
# CÃLCULOS
# ============================================================================


def calcular_dy_jared(divs, precio_actual):
    """
    Aplica la formula de Jared para DY:
    - Solo DEFINITIVO + PROVISORIO (excluye ADICIONAL y EVENTUAL)
    - Promedio de ultimos 3 ANOS COMPLETOS (sin ano actual)
    - Dividido por precio actual BCS

    Si no hay 3 anos completos, usa los anos disponibles e indica cuantos.
    """
    ano_actual = datetime.now().year

    # Filtrar solo definitivos y provisorios, EXCLUYENDO ano actual
    divs_validos = [
        d for d in divs
        if d["tipo"] in ("DEFINITIVO", "PROVISORIO")
        and d["fecha_pago"]
        and int(d["fecha_pago"][:4]) < ano_actual  # Sin ano actual
    ]

    # Agrupar por ano
    por_ano = {}
    for d in divs_validos:
        ano = d["fecha_pago"][:4]
        por_ano[ano] = por_ano.get(ano, 0) + d["monto_clp"]

    if not por_ano:
        return {
            "dy_pct": None,
            "anos_usados": 0,
            "anos_detalle": {},
            "promedio_anual": 0,
            "advertencia": "Sin dividendos definitivos/provisorios en anos pasados",
        }

    # Tomar los 3 anos mas recientes disponibles (sin ano actual)
    anos_disponibles = sorted(por_ano.keys(), reverse=True)[:3]
    anos_para_promedio = {a: por_ano[a] for a in anos_disponibles}

    promedio_anual = sum(anos_para_promedio.values()) / len(anos_para_promedio)
    dy_pct = (promedio_anual / precio_actual * 100) if precio_actual > 0 else 0

    advertencia = None
    if len(anos_disponibles) < 3:
        advertencia = f"Solo {len(anos_disponibles)} anos disponibles (ideal: 3)"

    return {
        "dy_pct": round(dy_pct, 2),
        "anos_usados": len(anos_disponibles),
        "anos_detalle": {a: round(anos_para_promedio[a], 2) for a in anos_disponibles},
        "promedio_anual": round(promedio_anual, 2),
        "advertencia": advertencia,
    }


def evaluar_vs_benchmark(dy_pct, benchmark_min, benchmark_max):
    """
    Compara DY actual vs benchmark del ticker.
    Retorna: status + emoji + tasa_descuento_vs_benchmark
    """
    if dy_pct is None:
        return {
            "status": "Sin datos",
            "emoji": "⚪",
            "vs_benchmark_pp": None,
        }

    dy_decimal = dy_pct / 100
    benchmark_medio = (benchmark_min + benchmark_max) / 2

    diff_pp = (dy_decimal - benchmark_medio) * 100  # diferencia en puntos porcentuales

    if dy_decimal >= benchmark_max:
        return {"status": "Sobre benchmark", "emoji": "🟢", "vs_benchmark_pp": round(diff_pp, 2)}
    elif dy_decimal >= benchmark_min:
        return {"status": "En rango", "emoji": "🟡", "vs_benchmark_pp": round(diff_pp, 2)}
    elif dy_decimal >= benchmark_min * 0.8:
        return {"status": "Cerca", "emoji": "🟠", "vs_benchmark_pp": round(diff_pp, 2)}
    else:
        return {"status": "Bajo benchmark", "emoji": "🔴", "vs_benchmark_pp": round(diff_pp, 2)}


def _valor_mas_reciente(item):
    """Helper para extraer el valor mas reciente del dict de cuentas CMF."""
    if not item or "valores" not in item:
        return None
    valores = item["valores"]
    if not valores:
        return None
    fechas_ord = sorted(valores.keys(), reverse=True)
    return valores[fechas_ord[0]] if fechas_ord else None


# ============================================================================
# ANÃLISIS DE UN TICKER
# ============================================================================


async def analizar_ticker(ticker_config, bcs_client):
    """
    Obtiene todos los datos de un ticker:
    - Precio BCS oficial
    - Dividendos clasificados ultimos 5 anos
    - Datos CMF (ROE, indicadores)
    - Aplica formula DY Jared
    - Evalua vs benchmark
    """
    ticker = ticker_config["ticker"]
    log.info(f"Analizando {ticker}...")

    resultado = {
        "ticker": ticker,
        "nombre": ticker_config["nombre"],
        "sector": ticker_config["sector"],
        "clasificacion": ticker_config["clasificacion"],
        "benchmark_min_pct": ticker_config["benchmark_min"] * 100,
        "benchmark_max_pct": ticker_config["benchmark_max"] * 100,
        "descripcion": ticker_config["descripcion"],
        "precio_actual_clp": None,
        "razon_social": None,
        "variacion_pct": None,
        "dy": None,
        "cagr_3y": None,
        "cagr_5y": None,
        "cagr_10y": None,
        "evaluacion": None,
        "cmf": None,
        "dividendos_recientes": [],
        "error": None,
    }

    # 1. Obtener datos BCS (precio + dividendos)
    try:
        data = await bcs_client.fetch_data_for_nemo(ticker)
        resumen = data.get("resumen") or {}
        variaciones = data.get("variaciones") or []

        if resumen:
            resultado["precio_actual_clp"] = resumen.get("PRECIO_CIERRE", 0)
            resultado["razon_social"] = resumen.get("RAZON_SOCIAL", "")
            resultado["variacion_pct"] = resumen.get("VAR", 0)

        # Parsear dividendos
        if variaciones:
            divs = parsear_dividendos_de_variaciones(variaciones)
            # Guardar los 10 mas recientes
            resultado["dividendos_recientes"] = sorted(
                divs, key=lambda x: x["fecha_pago"], reverse=True
            )[:10]

            # Calcular DY con formula Jared
            precio = resultado["precio_actual_clp"] or 0
            if precio > 0:
                resultado["dy"] = calcular_dy_jared(divs, precio)
                # Evaluar vs benchmark
                resultado["evaluacion"] = evaluar_vs_benchmark(
                    resultado["dy"]["dy_pct"],
                    ticker_config["benchmark_min"],
                    ticker_config["benchmark_max"],
                )
    except Exception as e:
        resultado["error"] = f"BCS: {e}"

# 1.5 CAGR historico desde BCS (precio oficial)
    try:
        cagrs = obtener_cagr_multi(ticker)
        resultado["cagr_3y"] = cagrs["cagr_3y"]
        resultado["cagr_5y"] = cagrs["cagr_5y"]
        resultado["cagr_10y"] = cagrs["cagr_10y"]
    except Exception as e:
        log.warning(f"CAGR BCS fallo para {ticker}: {e}")
        log.warning(f"  Error BCS para {ticker}: {e}")

    # Flujos historicos (detector La Polar): cache CMF de cierres anuales
    resultado["flujos_5y"] = _FLUJOS_CACHE.get(ticker, [])
    # 2. Obtener datos CMF (indicadores)
    try:
        cmf_ef = obtener_estados_financieros(ticker)
        if cmf_ef:
            cuentas = extraer_cuentas_clave(cmf_ef)
            indicadores = calcular_indicadores_balance(cuentas)

            balance = cuentas.get("balance", {})
            eerr = cuentas.get("eerr", {})

            utilidad = (
                _valor_mas_reciente(eerr.get("ganancia_perdida_atribuible_controladora"))
                or _valor_mas_reciente(eerr.get("ganancia_perdida"))
            )
            patrimonio = _valor_mas_reciente(balance.get("patrimonio_total"))
            ingresos = _valor_mas_reciente(eerr.get("ingresos_ordinarios"))

            roe_pct = None
            margen_neto_pct = None
            if utilidad and patrimonio and patrimonio > 0:
                roe_pct = (utilidad / patrimonio) * 100
            if utilidad and ingresos and ingresos != 0:
                margen_neto_pct = (utilidad / ingresos) * 100
            # --- ROE ANUAL (fix): utilidad de 12 meses / patrimonio ---
            # El roe_pct de arriba usa la utilidad del periodo (ej. Q1 = 3
            # meses) -> subestima ~4x. Aca lo recalculamos con el cierre anual.
            roe_anual_pct = None
            roe_periodo = None
            margen_anual_pct = None
            margen_periodo = None
            try:
                _mm, _aa = cmf_ef.periodo.split("/")
                if _mm == "12":
                    roe_anual_pct = roe_pct
                    roe_periodo = cmf_ef.periodo
                    margen_anual_pct = margen_neto_pct
                    margen_periodo = cmf_ef.periodo
                else:
                    _aa_cierre = int(_aa) - 1
                    cmf_anual = obtener_estados_financieros(ticker, mm=12, aa=_aa_cierre)
                    if cmf_anual:
                        _cta = extraer_cuentas_clave(cmf_anual)
                        _eerr_a = _cta.get("eerr", {})
                        _bal_a = _cta.get("balance", {})
                        _util_a = (
                            _valor_mas_reciente(_eerr_a.get("ganancia_perdida_atribuible_controladora"))
                            or _valor_mas_reciente(_eerr_a.get("ganancia_perdida"))
                        )
                        _patr_a = _valor_mas_reciente(_bal_a.get("patrimonio_total"))
                        if _util_a and _patr_a and _patr_a > 0:
                            roe_anual_pct = (_util_a / _patr_a) * 100
                            roe_periodo = f"12/{_aa_cierre}"
                        _ing_a = _valor_mas_reciente(_eerr_a.get("ingresos_ordinarios"))
                        if _util_a and _ing_a and _ing_a != 0:
                            margen_anual_pct = (_util_a / _ing_a) * 100
                            margen_periodo = f"12/{_aa_cierre}"
                if roe_anual_pct is None and roe_pct is not None:
                    _meses = int(_mm)
                    if 1 <= _meses <= 12:
                        roe_anual_pct = roe_pct * (12 / _meses)
                        roe_periodo = f"{cmf_ef.periodo} anualizado (aprox)"
                if margen_anual_pct is None and margen_neto_pct is not None:
                    margen_anual_pct = margen_neto_pct
                    margen_periodo = cmf_ef.periodo
            except Exception as _e_roe:
                log.warning(f"  ROE anual fallo para {ticker}: {_e_roe}")
                if roe_anual_pct is None:
                    roe_anual_pct = roe_pct
                    roe_periodo = cmf_ef.periodo

            resultado["cmf"] = {
                "periodo": cmf_ef.periodo,
                "tipo_balance": cmf_ef.tipo_balance,
                "moneda": cmf_ef.moneda,
                "unidad": cmf_ef.unidad,
                "roe_pct": round(roe_anual_pct, 2) if roe_anual_pct else None,
                "roe_periodo": roe_periodo,
                "margen_neto_pct": round(margen_anual_pct, 2) if margen_anual_pct is not None else (round(margen_neto_pct, 2) if margen_neto_pct else None),
                "margen_periodo": margen_periodo,
                "razon_endeudamiento_pct": round(indicadores.get("razon_endeudamiento_pct", 0), 2)
                    if "razon_endeudamiento_pct" in indicadores else None,
                "razon_corriente": round(indicadores.get("razon_corriente", 0), 2)
                    if "razon_corriente" in indicadores else None,
                "capital_trabajo": indicadores.get("capital_trabajo"),
                "patrimonio_total": patrimonio,
            }
    except Exception as e:
        log.warning(f"  Error CMF para {ticker}: {e}")
        if not resultado["error"]:
            resultado["error"] = f"CMF: {e}"

    return resultado


# ============================================================================
# MAIN
# ============================================================================


def _precio_ok(resultado):
    """True si la accion trae un precio valido (no cayo en captcha)."""
    p = resultado.get("precio_actual_clp") or 0
    try:
        return float(p) > 0
    except (TypeError, ValueError):
        return False


def _limpiar_cache_nemo(bcs, ticker):
    """Borra la cache del nemo para forzar un fetch fresco en el reintento."""
    nm = ticker.upper()
    for attr in ("_cache_variaciones", "_cache_resumen"):
        cache = getattr(bcs, attr, None)
        if isinstance(cache, dict):
            cache.pop(nm, None)


# Tope de reintentos contra el captcha aleatorio de la BCS. Alto para vencer
# casi cualquier captcha, pero finito para no quedar en bucle si la BCS esta
# caida de verdad. Las pausas entre intentos crecen para dar tiempo a que el
# bloqueo temporal se libere.
MAX_INTENTOS_CAPTCHA = 8


async def analizar_con_reintentos(ticker_config, bcs, max_intentos=MAX_INTENTOS_CAPTCHA):
    """Analiza una accion insistiendo en la BCS hasta obtener precio FRESCO.
    El captcha de la BCS es aleatorio: reintentando con pausas crecientes,
    casi siempre termina pasando. El precio es SIEMPRE de la Bolsa de Santiago
    (sin fuentes alternativas que puedan venir desfasadas)."""
    ticker = ticker_config["ticker"]
    resultado = None
    for intento in range(1, max_intentos + 1):
        if intento > 1:
            _limpiar_cache_nemo(bcs, ticker)
            pausa = min(3 * (intento - 1), 20)  # 3,6,9,...,tope 20s
            log.info(f"  Reintento {intento}/{max_intentos} para {ticker} "
                     f"(BCS en captcha, esperando {pausa}s para precio fresco)...")
            await asyncio.sleep(pausa)
        resultado = await analizar_ticker(ticker_config, bcs)
        if _precio_ok(resultado):
            if intento > 1:
                log.info(f"  {ticker}: precio BCS fresco obtenido en intento {intento}.")
            return resultado
    log.warning(f"  {ticker}: la BCS siguio en captcha tras {max_intentos} intentos. "
                f"Se usara el ultimo precio BCS valido (preservado).")
    return resultado


def _cargar_json_previo():
    """Carga el acciones_chilenas.json anterior como dict {ticker: registro}."""
    try:
        with open(JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {a["ticker"]: a for a in data.get("acciones", []) if a.get("ticker")}
    except Exception:
        return {}


def _preservar_si_roto(resultado, previo_por_ticker):
    """Si la accion quedo en precio 0 pero habia un dato bueno previo,
    conserva el dato anterior (precio/dy/evaluacion/etc.) marcado como stale.
    Devuelve (registro_final, fue_preservado:bool)."""
    if _precio_ok(resultado):
        return resultado, False
    prev = previo_por_ticker.get(resultado.get("ticker"))
    if not prev:
        return resultado, False
    prev_precio = prev.get("precio_actual_clp") or 0
    try:
        prev_ok = float(prev_precio) > 0
    except (TypeError, ValueError):
        prev_ok = False
    if not prev_ok:
        return resultado, False
    # Conservar campos de mercado del dato previo; mantener CAGR/CMF nuevos si llegaron
    preservado = dict(prev)
    for campo in ("cagr_3y", "cagr_5y", "cagr_10y"):
        if resultado.get(campo) is not None:
            preservado[campo] = resultado[campo]
    if resultado.get("cmf"):
        preservado["cmf"] = resultado["cmf"]
    preservado["stale"] = True
    preservado["stale_desde"] = prev.get("_fecha_dato") or prev.get("stale_desde") or "anterior"
    preservado["error"] = None
    return preservado, True


async def main():
    log.info("=" * 70)
    log.info("ACCIONES CHILENAS - Analisis Watchlist Dividendero")
    log.info("=" * 70)

    inicio = datetime.now()

    # Inicializar cliente BCS (singleton)
    bcs = BCSClient()

    # Warm-up del browser: la primera llamada a Playwright tiende a fallar
    # por race condition del listener de red. Hacemos un fetch dummy del
    # primer ticker para calentar el browser antes del loop principal.
    if WATCHLIST:
        warmup_ticker = WATCHLIST[0]["ticker"]
        log.info(f"Warm-up del browser con {warmup_ticker}...")
        try:
            await bcs.fetch_data_for_nemo(warmup_ticker)
            log.info("Warm-up OK")
        except Exception as e:
            log.warning(f"Warm-up fallo (no critico): {e}")

    # Cargar datos previos para preservar el ultimo dato bueno si una accion
    # cae en el captcha aleatorio de la BCS.
    previo_por_ticker = _cargar_json_previo()
    n_preservadas = 0

    # Analizar todos los tickers (secuencial porque BCS usa el mismo browser)
    resultados = []
    for ticker_config in WATCHLIST:
        try:
            resultado = await analizar_con_reintentos(ticker_config, bcs)
            resultado, fue_preservado = _preservar_si_roto(resultado, previo_por_ticker)
            if fue_preservado:
                n_preservadas += 1
                log.warning(f"  {ticker_config['ticker']}: se conservo el ultimo dato bueno (captcha BCS).")
            resultados.append(resultado)
        except Exception as e:
            log.error(f"Fallo critico con {ticker_config['ticker']}: {e}")
            resultados.append({
                "ticker": ticker_config["ticker"],
                "nombre": ticker_config["nombre"],
                "sector": ticker_config["sector"],
                "error": str(e),
            })

    # Cerrar el browser de Playwright limpiamente
    try:
        await bcs._stop()
    except Exception:
        pass

    # Sellar la fecha del dato fresco en cada registro con precio valido
    _ahora_iso = datetime.now().isoformat()
    for r in resultados:
        if (r.get("precio_actual_clp") or 0) and not r.get("stale"):
            r["_fecha_dato"] = _ahora_iso

    if n_preservadas:
        log.warning(f"{n_preservadas} accion(es) conservaron su ultimo dato bueno por captcha BCS.")

    # Construir output
    output = {
        "fecha_actualizacion": datetime.now().isoformat(),
        "duracion_segundos": (datetime.now() - inicio).total_seconds(),
        "n_acciones": len(resultados),
        "acciones": resultados,
    }

    # Guardar JSON
    JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    log.info(f"OK: {len(resultados)} acciones procesadas en {output['duracion_segundos']:.1f}s")
    log.info(f"JSON guardado en: {JSON_PATH}")

    # Resumen visual
    log.info("")
    log.info("RESUMEN:")
    log.info(f"{'Ticker':<12} {'Sector':<16} {'Precio CLP':>12} {'DY 3y':>8} {'Status':<20}")
    log.info("-" * 75)
    for r in resultados:
        if r.get("error"):
            log.info(f"{r['ticker']:<12} {r.get('sector', 'N/A'):<16} {'ERROR':>12} {'-':>8} {r['error'][:30]:<20}")
            continue
        precio = r.get("precio_actual_clp") or 0
        dy_str = f"{r['dy']['dy_pct']:.2f}%" if r.get("dy") and r["dy"].get("dy_pct") else "N/A"
        status = r.get("evaluacion", {}).get("status", "N/A") if r.get("evaluacion") else "N/A"
        emoji = r.get("evaluacion", {}).get("emoji", "") if r.get("evaluacion") else ""
        log.info(f"{r['ticker']:<12} {r.get('sector', 'N/A'):<16} ${precio:>10,.0f} {dy_str:>8} {emoji} {status}")

    log.info("")
    log.info(f"Listo. JSON: {JSON_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
