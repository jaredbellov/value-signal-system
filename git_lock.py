# -*- coding: utf-8 -*-
"""
git_lock.py - Candado compartido para serializar el acceso a git entre las
tareas programadas de Value Signal.

Problema que resuelve: ~13 tareas hacen git pull/push en ventanas solapadas.
Cuando dos coinciden, un 'pull --rebase' queda a medias y bloquea el repo
("interactive rebase in progress"). Este lock garantiza que solo UNA tarea
toque git a la vez. Las demas esperan su turno (o siguen sin lock si se
agota la espera, para no perder la corrida).

Uso en cualquier script:

    from git_lock import git_lock

    with git_lock():
        git_pull()
        git_commit_and_push()
    # el lock se libera solo al salir del 'with', aunque haya excepcion

El lock usa creacion atomica de archivo (os.O_CREAT | os.O_EXCL): si dos
procesos intentan crearlo en el mismo instante, solo uno gana. Robusto en
Windows. Maneja locks huerfanos (proceso que murio sin liberar) por edad.
"""
import os
import time
import logging
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger("git-lock")

# Ruta fija del candado (fuera de .git para no confundir a git con su propio
# index.lock). Todas las tareas apuntan aca.
LOCK_FILE = Path(r"C:\value-signal-local\repo\.vs_git_push.lock")

STALE_SECONDS = 300     # 5 min: lock mas viejo que esto se considera huerfano
WAIT_SECONDS = 3        # espera entre reintentos
MAX_WAIT_SECONDS = 180  # 3 min: tope total de espera antes de seguir sin lock


def _intentar_crear():
    """Creacion atomica del lock. True si lo logro, False si ya existia."""
    try:
        fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, f"{os.getpid()} {time.time():.0f}".encode())
        finally:
            os.close(fd)
        return True
    except FileExistsError:
        return False
    except OSError:
        # Cualquier otro error de OS: no bloquear la tarea, seguir sin lock.
        return False


def adquirir_lock():
    """
    Intenta adquirir el lock. Devuelve True si lo obtuvo (hay que liberarlo
    despues), o False si tras MAX_WAIT_SECONDS no pudo (se continua sin lock
    para no perder la actualizacion; es preferible un choque raro a saltarse
    una corrida entera).
    """
    inicio = time.time()
    while True:
        # Limpiar lock huerfano si quedo de una tarea que murio.
        if LOCK_FILE.exists():
            try:
                edad = time.time() - LOCK_FILE.stat().st_mtime
                if edad > STALE_SECONDS:
                    log.warning(f"Lock huerfano ({edad:.0f}s), lo elimino.")
                    LOCK_FILE.unlink(missing_ok=True)
            except OSError:
                pass

        if _intentar_crear():
            return True

        if time.time() - inicio > MAX_WAIT_SECONDS:
            log.warning(
                f"No pude adquirir el lock tras {MAX_WAIT_SECONDS}s. "
                f"Continuo SIN lock (otra tarea lo retiene)."
            )
            return False

        time.sleep(WAIT_SECONDS)


def liberar_lock():
    """Libera el lock. Seguro de llamar siempre."""
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except OSError:
        pass


@contextmanager
def git_lock():
    """
    Context manager. Envuelve la zona de operaciones git:

        with git_lock():
            ... pull / commit / push ...

    Garantiza liberacion del lock al salir, incluso si hay excepcion.
    """
    tengo = adquirir_lock()
    try:
        yield tengo
    finally:
        if tengo:
            liberar_lock()
