# -*- coding: utf-8 -*-
"""
publicar.py - PUBLICADOR UNICO de Value Signal.

El UNICO script que toca git. Las tareas de datos solo generan JSONs; este
script corre cada 15 min (tarea programada) y publica todo lo que cambio:

    git pull --no-rebase  ->  git add (JSONs)  ->  commit  ->  push

Al ser el unico proceso que usa git, la concurrencia es imposible: no hay
con quien chocar. Usa git_lock por defensa en profundidad (por si alguien
corre un update a mano con la version vieja).

Uso:
    python publicar.py
"""
import subprocess
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
    # fallback: sin lock (somos el unico que hace git de todos modos)
    from contextlib import contextmanager
    @contextmanager
    def git_lock():
        yield True

def git(args, timeout=120):
    """Ejecuta git y devuelve (code, stdout, stderr)."""
    try:
        r = subprocess.run(
            ["git"] + args, cwd=REPO, capture_output=True,
            text=True, timeout=timeout,
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"timeout tras {timeout}s"
    except Exception as e:
        return -1, "", str(e)

def main():
    log.info("=" * 60)
    log.info(f"PUBLICAR - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 0. Sanity: el repo no debe estar en rebase/merge a medias
    code, out, _ = git(["status"])
    if "rebase in progress" in out or "MERGE_HEAD" in out:
        log.error("Repo en estado inconsistente (rebase/merge a medias). Abortando.")
        log.error("Ejecuta: git rebase --abort / git merge --abort y reintenta.")
        return 1

    # 1. Ver si hay cambios que publicar
    code, out, _ = git(["status", "--porcelain"])
    cambios = [l for l in out.split("\n") if l.strip()]
    if not cambios:
        log.info("Sin cambios que publicar.")
        return 0
    log.info(f"Cambios detectados: {len(cambios)} archivo(s)")
    for c in cambios[:15]:
        log.info(f"  {c}")

    with git_lock():
        # 2. Traer lo remoto primero (merge, nunca rebase)
        code, out, err = git(["pull", "--no-rebase", "--no-edit"])
        if code != 0:
            log.warning(f"git pull devolvio {code}: {err[:300]}")
            # Si el pull fallo por conflicto, abortar el merge para no dejar
            # el repo a medias, y salir. El proximo ciclo reintenta.
            git(["merge", "--abort"])
            log.error("Pull con conflicto: merge abortado. Reintento en el proximo ciclo.")
            return 1
        log.info("git pull OK")

        # 3. Add de todo lo modificado (JSONs de datos)
        code, _, err = git(["add", "-A"])
        if code != 0:
            log.error(f"git add fallo: {err[:300]}")
            return 1

        # 4. Commit
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        code, out, err = git(["commit", "-m", f"data: publicacion consolidada {ts} [skip ci]"])
        if code != 0:
            # "nothing to commit" si el pull ya trajo lo mismo — no es error
            log.info(f"git commit: {out or err}".strip()[:200])
            return 0
        log.info("git commit OK")

        # 5. Push (con un retry)
        code, _, err = git(["push"])
        if code != 0:
            log.warning(f"git push fallo (intento 1): {err[:300]}")
            code, _, err = git(["pull", "--no-rebase", "--no-edit"])
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
