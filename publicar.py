# -*- coding: utf-8 -*-
"""
publicar.py v2 - PUBLICADOR UNICO de Value Signal.

El UNICO script que toca git. Las tareas de datos solo generan JSONs; este
script corre cada 15 min y publica todo lo que cambio.

v2 corrige el orden de operaciones (bug del loop de conflicto):
    v1: pull -> add -> commit -> push   (git rechaza pull con archivos sucios)
    v2: add -> COMMIT -> pull -X ours -> push

Con los cambios ya commiteados, el pull puede mergear. Si el remoto toco el
mismo JSON, gana la version local (-X ours): es la mas fresca porque las
tareas la acaban de generar. Si el pull falla por otra causa, aborta el merge
limpio y reintenta al proximo ciclo (el repo NUNCA queda a medias).

Compatible con pythonw.exe (sin consola): loguea solo a publicar.log.
"""
import subprocess

# En Windows, evita que git abra una ventana de consola
_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW
import logging
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(r"C:\value-signal-local\repo")
LOG_PATH = REPO / "publicar.log"

_handlers = [logging.FileHandler(LOG_PATH, encoding="utf-8")]
if sys.stdout is not None:  # con pythonw no hay consola
    _handlers.append(logging.StreamHandler(sys.stdout))
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=_handlers,
)
log = logging.getLogger("publicar")

sys.path.insert(0, str(REPO))
try:
    from git_lock import git_lock
except ImportError:
    from contextlib import contextmanager
    @contextmanager
    def git_lock():
        yield True

def git(args, timeout=120):
    try:
        r = subprocess.run(
            ["git"] + args, cwd=REPO, capture_output=True,
            text=True, timeout=timeout, creationflags=_NO_WINDOW,
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"timeout tras {timeout}s"
    except Exception as e:
        return -1, "", str(e)

def hay_commits_sin_pushear():
    code, out, _ = git(["rev-list", "--count", "origin/main..HEAD"])
    try:
        return code == 0 and int(out) > 0
    except ValueError:
        return False

def main():
    log.info("=" * 60)
    log.info(f"PUBLICAR v2 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 0. Sanity: el repo no debe estar en rebase/merge a medias
    code, out, _ = git(["status"])
    if "rebase in progress" in out or "MERGE_HEAD" in out or "all conflicts fixed" in out:
        log.error("Repo en estado inconsistente (rebase/merge a medias). Abortando.")
        return 1

    # 1. Detectar cambios locales
    code, out, _ = git(["status", "--porcelain"])
    cambios = [l for l in out.split("\n") if l.strip()]
    pendientes = hay_commits_sin_pushear()

    if not cambios and not pendientes:
        log.info("Sin cambios ni commits pendientes.")
        return 0

    with git_lock():
        # 2. COMMIT PRIMERO (asegura los cambios locales antes de mergear)
        if cambios:
            log.info(f"Cambios detectados: {len(cambios)} archivo(s)")
            for c in cambios[:15]:
                log.info(f"  {c}")
            code, _, err = git(["add", "-A"])
            if code != 0:
                log.error(f"git add fallo: {err[:300]}")
                return 1
            ts = datetime.now().strftime("%Y-%m-%d %H:%M")
            code, out, err = git(["commit", "-m", f"data: publicacion consolidada {ts} [skip ci]"])
            if code != 0:
                log.info(f"git commit: {(out or err)[:200]}")
            else:
                log.info("git commit OK")
        elif pendientes:
            log.info("Sin cambios nuevos, pero hay commits sin pushear. Publicando...")

        # 3. PULL con merge; en conflictos de contenido gana lo local (-X ours)
        code, out, err = git(["pull", "--no-rebase", "--no-edit", "-X", "ours"])
        if code != 0:
            log.warning(f"git pull devolvio {code}: {(err or out)[:300]}")
            git(["merge", "--abort"])
            log.error("Pull fallo: merge abortado. Reintento en el proximo ciclo.")
            return 1
        log.info("git pull OK")

        # 4. PUSH (con un retry)
        code, _, err = git(["push"])
        if code != 0:
            log.warning(f"git push fallo (intento 1): {err[:300]}")
            code, _, err = git(["pull", "--no-rebase", "--no-edit", "-X", "ours"])
            if code != 0:
                git(["merge", "--abort"])
                log.error("Pull en retry fallo. Reintento en el proximo ciclo.")
                return 1
            code, _, err = git(["push"])
            if code != 0:
                log.error(f"git push fallo (intento 2): {err[:300]}")
                return 1
            log.info("git push OK (en retry)")
        else:
            log.info("git push OK")

    log.info("Publicacion completada.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
