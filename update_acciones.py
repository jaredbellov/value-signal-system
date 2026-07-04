"""
UPDATE ACCIONES CHILENAS - Wrapper para Tarea Programada Windows
=================================================================
1. Ejecuta acciones_chilenas.py (genera acciones_chilenas.json)
2. Git pull (trae cambios remotos para evitar conflictos)
3. Git add + commit + push del JSON

Configuración: ejecutar cada 30 min via Tarea Programada Windows.
"""

import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ============================================================================
# CONFIGURACIÓN
# ============================================================================

REPO_PATH = Path(r"C:\value-signal-local\repo")
LOG_PATH = REPO_PATH / "logs" / "update_acciones.log"
JSON_FILE = "acciones_chilenas.json"

# Crear carpeta de logs si no existe
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

# Logging a archivo + consola
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler(LOG_PATH, encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("update-acciones")

from git_lock import git_lock  # serializa acceso a git entre tareas


# ============================================================================
# HELPERS GIT
# ============================================================================

def run_command(cmd, cwd=None, check=True):
    """Ejecuta un comando y devuelve el output."""
    log.info(f"$ {' '.join(cmd) if isinstance(cmd, list) else cmd}")
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd or REPO_PATH,
            capture_output=True,
            text=True,
            shell=isinstance(cmd, str),
            timeout=120,
        )
        if result.stdout:
            log.info(f"  stdout: {result.stdout.strip()[:500]}")
        if result.stderr and result.returncode != 0:
            log.warning(f"  stderr: {result.stderr.strip()[:500]}")
        if check and result.returncode != 0:
            log.error(f"  Comando falló con código {result.returncode}")
            return None
        return result
    except subprocess.TimeoutExpired:
        log.error(f"  TIMEOUT (>120s)")
        return None
    except Exception as e:
        log.error(f"  Excepción: {e}")
        return None


def git_pull():
    """Trae cambios remotos para evitar conflictos en push."""
    log.info("Git pull (trayendo cambios remotos)...")
    result = run_command(
        ["git", "pull", "--no-rebase", "--autostash"],
        check=False,
    )
    if result is None:
        log.warning("Pull falló (no es bloqueante)")
        return False
    return True


def git_commit_and_push():
    """Add + commit + push del JSON de acciones chilenas."""
    log.info("Git add + commit + push...")

    # Verificar si hay cambios
    result = run_command(["git", "status", "--porcelain", JSON_FILE], check=False)
    if result is None or not result.stdout.strip():
        log.info("Sin cambios en el JSON, nada que hacer")
        return True

    # Add
    if run_command(["git", "add", JSON_FILE]) is None:
        return False

    # Commit con timestamp
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    commit_msg = f"Update acciones chilenas - {timestamp}"
    if run_command(["git", "commit", "-m", commit_msg]) is None:
        return False

    # Push
    if run_command(["git", "push"]) is None:
        log.warning("Push falló, intentando pull + push...")
        # Si el push falla, hacer pull y reintentar
        git_pull()
        if run_command(["git", "push"]) is None:
            return False

    log.info(f"OK: commit {commit_msg} pusheado")
    return True


# ============================================================================
# MAIN
# ============================================================================

def main():
    inicio = datetime.now()
    log.info("=" * 70)
    log.info(f"UPDATE ACCIONES CHILENAS - {inicio.strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 70)


    # 2. Ejecutar el scraper (genera acciones_chilenas.json)
    log.info("Ejecutando acciones_chilenas.py...")
    script_path = REPO_PATH / "acciones_chilenas.py"
    if not script_path.exists():
        log.error(f"No existe {script_path}")
        return 1

    result = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=REPO_PATH,
        capture_output=True,
        text=True,
        timeout=300,  # 5 minutos máximo
        encoding='utf-8',
        errors='replace',
    )

    if result.returncode != 0:
        log.error(f"Scraper falló (código {result.returncode})")
        log.error(f"stderr: {result.stderr[:1000]}")
        return 1

    log.info("Scraper terminó OK")

    # 3. Verificar que el JSON fue generado
    json_path = REPO_PATH / JSON_FILE
    if not json_path.exists():
        log.error(f"No se generó {json_path}")
        return 1

    # 4. Commit + push (con lock compartido para evitar choques entre tareas)
    with git_lock():
        if not git_commit_and_push():
            log.error("Git push fallo")
            return 1

    duracion = (datetime.now() - inicio).total_seconds()
    log.info(f"OK: completado en {duracion:.1f}s")
    log.info("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
