"""
UPDATE DIVIDEND ETFs - Tarea programada local que descarga datos de SCHD y JEPQ
desde Yahoo (sin rate limit porque corre en tu IP, no en cloud), calcula el
score completo con analyze_dividend_etf, y guarda a dividend_etfs_data.json.

Streamlit Cloud despues lee ese JSON sin llamar a Yahoo directamente.

Esto resuelve el problema cronico de Yahoo bloqueando ciertos tickers
(observado con JEPQ) desde IPs cloud.
"""
import json
import logging
import subprocess as _sp
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
LOG_DIR = SCRIPT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "dividend_etfs.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

OUTPUT_FILE = SCRIPT_DIR / "dividend_etfs_data.json"

# Aportes base por defecto (solo para el calculo del aporte sugerido inicial -
# el dashboard recalcula con el aporte real del usuario)
APORTES_DEFAULT = {"SCHD": 140, "JEPQ": 60, "VNQ": 100, "VNQI": 80, "DGRO": 100}

# Config de ETFs: (ticker, categoria, nombre, descripcion).
# categoria "dividend" -> SCHD/JEPQ ; categoria "reit" -> VNQ/VNQI (inmobiliario).
# Los REITs usan el MISMO scoring que los dividend ETFs (DY vs historico,
# drawdown, DGR, momentum): un REIT-ETF se evalua por flujo, no por crecimiento.
ETFS_CONFIG = [
    ("SCHD", "dividend", "Schwab US Dividend Equity",
     "~100 empresas americanas 'aburridas y buenas' que llevan anos pagando y subiendo dividendos "
     "(Coca-Cola, Merck, Abbott...). Dividendo ~3.5% que ademas crece cada ano. "
     "El caballo de batalla del inversor dividendero."),
    ("JEPQ", "dividend", "JPMorgan Nasdaq Equity Premium",
     "Paga una renta mensual alta (~9% al ano) que genera vendiendo opciones sobre las tecnologicas "
     "del Nasdaq. El costo: cuando la bolsa vuela, este fondo sube menos. "
     "Es para tener flujo hoy, no para crecimiento."),
    ("DGRO", "dividend", "iShares Core Dividend Growth",
     "~440 empresas americanas que llevan 5+ anos subiendo su dividendo todos los anos, filtrando "
     "las 'trampas' de yields altos insostenibles. El dividendo parte chico (~2%) pero crece contigo: "
     "es plantar un arbol, no cosechar hoy."),
    ("VNQ",  "reit", "Vanguard Real Estate ETF",
     "Los grandes 'caseros' de EE.UU.: empresas duenas de bodegas, malls, torres de celular y data "
     "centers que cobran arriendo. Por ley reparten ~90% de sus utilidades como dividendo. "
     "Sensible a las tasas de interes; parte del yield es devolucion de capital."),
    ("VNQI", "reit", "Vanguard Global ex-US Real Estate",
     "Lo mismo que VNQ pero fuera de EE.UU. (Asia y Europa, +700 inmobiliarias que reparten arriendos). "
     "Paga mas yield y esta mas barato, a cambio de riesgo cambiario. "
     "Diversifica de verdad: no se mueve igual que el ladrillo americano."),
]


from git_lock import git_lock  # serializa acceso a git entre tareas


def ejecutar_git(args, cwd=None):
    """Ejecuta un comando git y devuelve (exit_code, stdout, stderr)."""
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

    code, out, err = ejecutar_git(["status", "--porcelain", "dividend_etfs_data.json"])
    if not out.strip():
        log.info("Sin cambios en dividend_etfs_data.json, nada que commitear")
        return

    code, out, err = ejecutar_git(["pull", "--no-rebase", "--autostash"])
    if code != 0:
        log.warning(f"git pull devolvio codigo {code}: {err}")
    else:
        log.info("git pull OK")

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    code, out, err = ejecutar_git(["add", "dividend_etfs_data.json"])
    if code != 0:
        log.error(f"git add fallo: {err}")
        return

    code, out, err = ejecutar_git(["commit", "-m", f"chore: update dividend ETFs {ts} [skip ci]"])
    if code != 0:
        log.warning(f"git commit dijo: {out or err}")
        return
    log.info("git commit OK")

    code, out, err = ejecutar_git(["push"])
    if code != 0:
        log.warning(f"git push fallo (intento 1): {err}")
        log.info("Reintentando: pull --no-rebase + push...")
        code_pull, _, err_pull = ejecutar_git(["pull", "--no-rebase", "--autostash"])
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
    log.info("=" * 60)
    log.info("=== Update Dividend ETFs ===")
    log.info(f"Hora: {datetime.now().isoformat()}")

    # Importar la funcion local (no rate-limited porque corremos en tu PC)
    from dividend_etf_signal import analyze_dividend_etf

    resultados = {}
    fallidos = []

    for ticker, categoria, nombre, descripcion in ETFS_CONFIG:
        log.info(f"\n--- Procesando {ticker} ({categoria}) ---")
        try:
            result = analyze_dividend_etf(
                ticker,
                aporte_base_usd=APORTES_DEFAULT[ticker],
                usd_clp=None,
            )
            if result is None:
                log.warning(f"{ticker}: analyze_dividend_etf retorno None")
                fallidos.append(ticker)
            else:
                # Limpiamos los datos para JSON (no incluimos componentes_detalle que puede ser pesado)
                limpio = {
                    k: v for k, v in result.items()
                    if k != "componentes_detalle"
                }
                # Metadata propia: categoria (dividend/reit) + nombre + descripcion
                limpio["categoria"] = categoria
                limpio.setdefault("nombre", nombre)
                limpio.setdefault("long_name", nombre)
                limpio["descripcion"] = descripcion
                resultados[ticker] = limpio
                log.info(f"{ticker}: OK - precio={result.get('precio_usd')}, score={result.get('score')}, zona={result.get('zona')}")
        except Exception as e:
            log.error(f"{ticker}: excepcion - {e}")
            fallidos.append(ticker)

    # Estructura final
    output = {
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "etfs": resultados,
        "fallidos": fallidos,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    log.info(f"\nGuardado: {OUTPUT_FILE}")
    log.info(f"  Exitosos: {list(resultados.keys())}")
    if fallidos:
        log.warning(f"  Fallidos: {fallidos}")

    # push con lock compartido para evitar choques entre tareas
    with git_lock():
        git_sync_and_push()

    log.info(f"=== FIN ({datetime.now().strftime('%H:%M:%S')}) ===")
    return 0


if __name__ == "__main__":
    main()
