"""
UPDATE PRICES - Local Edition
==============================
Ejecuta el scraper de Bolsa de Santiago y guarda los precios en prices.json.
Diseñado para correr en PC local cada 5 minutos via Tarea Programada Windows.

Características:
- Solo commitea al repo si el precio cambió >0.1%
- Loggea todo en update.log para diagnóstico
- Tolerante a fallos (si BCS está caído, conserva precio previo como stale)
- Hace git pull antes (por si Streamlit modificó algo) y push después
"""

import asyncio
import json
import sys
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from bcs_scraper import fetch_bcs_resumen, extraer_datos_precio

# Configuración logging: archivo + consola
SCRIPT_DIR = Path(__file__).parent
# Log va a una subcarpeta logs\ dentro del repo (gitignored)
LOG_DIR = SCRIPT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "update.log"

# Si el log es muy grande (>5MB), rotarlo
try:
    if LOG_FILE.exists() and LOG_FILE.stat().st_size > 5_000_000:
        backup = LOG_DIR / "update.log.old"
        if backup.exists():
            backup.unlink()
        LOG_FILE.rename(backup)
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger(__name__)

# Configuración
TICKERS = ["CFISPETF", "CFINASDAQ"]
OUTPUT_FILE = SCRIPT_DIR / "prices.json"
UMBRAL_CAMBIO_PCT = 0.1  # Solo commitea si cambia >0.1%


def precio_cambió_significativamente(precio_anterior, precio_nuevo):
    """Devuelve True si el precio cambió más del umbral."""
    if precio_anterior is None or precio_nuevo is None:
        return True
    if precio_anterior == 0:
        return True
    cambio_pct = abs((precio_nuevo - precio_anterior) / precio_anterior * 100)
    return cambio_pct >= UMBRAL_CAMBIO_PCT


from git_lock import git_lock  # serializa acceso a git entre tareas


def ejecutar_git(args, cwd=None):
    """Ejecuta un comando git y devuelve (exit_code, stdout, stderr)."""
    cwd = cwd or SCRIPT_DIR
    try:
        result = subprocess.run(
            ['git'] + args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=60,
            encoding='utf-8',
            errors='replace',
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"
    except Exception as e:
        return -1, "", str(e)


async def main():
    log.info("=" * 60)
    log.info("=== Update Prices LOCAL ===")
    log.info(f"Hora: {datetime.now().isoformat()}")


    # 2. Cargar precios previos
    previous_data = {}
    if OUTPUT_FILE.exists():
        try:
            with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
                previous_data = json.load(f)
            log.info("Cargado prices.json previo")
        except Exception as e:
            log.warning(f"No se pudo cargar prices.json: {e}")

    # 3. Scrapear cada ticker
    new_prices = {}
    # Forzamos hubo_cambios=True para que SIEMPRE commitee tras cada run exitoso.
    # Esto garantiza que el indicador de frescura del dashboard refleje la realidad.
    # Los logs igual indican si los precios cambiaron o no (informativo).
    hubo_cambios = True
    errores = []

    for ticker in TICKERS:
        log.info(f"--- Scraping {ticker} ---")
        try:
            resumen = await fetch_bcs_resumen(ticker)
            datos = extraer_datos_precio(resumen, ticker)

            precio_actual = datos.get("precio_cierre") or datos.get("precio_actual")

            if precio_actual is not None:
                # Comparar con precio anterior
                ant = previous_data.get("prices", {}).get(ticker, {})
                precio_anterior = ant.get("precio_cierre") or ant.get("precio_actual")

                if precio_cambió_significativamente(precio_anterior, precio_actual):
                    log.info(f"  PRECIO CAMBIÓ: {precio_anterior} -> {precio_actual}")
                    hubo_cambios = True
                else:
                    log.info(f"  Sin cambios significativos (anterior: {precio_anterior}, nuevo: {precio_actual})")

                new_prices[ticker] = datos
            else:
                errores.append(f"{ticker}: sin precio")
                log.warning(f"  Sin precio para {ticker}")
                # Conservar dato previo como stale
                if ticker in previous_data.get("prices", {}):
                    new_prices[ticker] = previous_data["prices"][ticker]
                    new_prices[ticker]["stale"] = True
        except Exception as e:
            errores.append(f"{ticker}: {str(e)}")
            log.error(f"  ERROR: {e}")
            if ticker in previous_data.get("prices", {}):
                new_prices[ticker] = previous_data["prices"][ticker]
                new_prices[ticker]["stale"] = True

    # 4. Construir output
    output_data = {
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "updated_at_local": datetime.now().isoformat(),
        "tickers_solicitados": TICKERS,
        "errores": errores,
        "prices": new_prices,
    }

    # 5. Guardar siempre (aunque no haya cambios, para refrescar timestamp)
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False, default=str)
    log.info(f"Guardado en {OUTPUT_FILE.name}")

    # 6. Si hubo cambios significativos, commit + push
    if hubo_cambios:
        log.info("--- Hay cambios significativos: committing ---")
        # push con lock compartido para evitar choques entre tareas
        if False:  # [PLAN-B] git neutralizado: publicar.py se encarga del push
            code, out, err = ejecutar_git(['add', 'prices.json'])
            if code != 0:
                log.error(f"git add falló: {err}")
                return 1
    
            ts = datetime.now().strftime("%Y-%m-%d %H:%M")
            code, out, err = ejecutar_git(['commit', '-m', f"chore: update BCS prices {ts} [skip ci]"])
            if code != 0:
                # Puede ser "nothing to commit" si solo cambió el timestamp
                log.warning(f"git commit dijo: {out or err}")
            else:
                log.info("git commit OK")
    
            code, out, err = ejecutar_git(['push'])
            if code != 0:
                log.error(f"git push falló: {err}")
                return 1
            log.info("git push OK")
    else:
        log.info("Sin cambios significativos, no commiteo")

    # 7. Resumen final
    log.info("Resumen:")
    for ticker, data in new_prices.items():
        precio = data.get("precio_cierre") or data.get("precio_actual") or "N/A"
        stale = " (STALE)" if data.get("stale") else ""
        log.info(f"  {ticker}: ${precio} CLP{stale}")

    log.info(f"=== FIN ({datetime.now().strftime('%H:%M:%S')}) ===\n")
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
