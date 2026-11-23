# -*- coding: utf-8 -*-
"""
PERSISTENCIA DE SENALES
=======================
Las fuentes externas fallan de a ratos, no para siempre: Yahoo responde
"possibly delisted" para un ticker que existe, y las APIs de Fear & Greed y
MVRV se quedan sin contestar. Antes, una corrida que fallaba reescribia el
JSON con SOLO lo que consiguio esa vez, y una senal buena de hace 30 minutos
se perdia. El score se recalculaba sin ella y la pagina mostraba un veredicto
peor informado que el de media hora antes.

Una senal que funciono hace poco no es una senal perdida: es la misma senal
con unas horas encima. Esto la conserva, la marca con su edad, y recien la
descarta cuando de verdad envejecio.
"""
import io
import json
import os
from datetime import datetime, timezone

MAX_EDAD_H = 24  # pasado esto, la senal si cuenta como perdida


def _ahora():
    return datetime.now(timezone.utc)


def _parse(ts):
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def fusionar(ruta_json, sub_scores, sub_detalles, pesos, log=None,
             max_edad_h=MAX_EDAD_H):
    """Rellena las senales que fallaron en ESTA corrida con el ultimo valor
    bueno conocido, siempre que no haya envejecido.

    Devuelve (sub_scores, sub_detalles, sub_fechas, reusadas):
      sub_fechas[nombre] = ISO de cuando se obtuvo DE VERDAD ese valor
      reusadas           = nombres que vienen del archivo anterior
    """
    ahora = _ahora()
    fechas, reusadas = {}, []

    prev = {}
    try:
        if os.path.exists(ruta_json):
            prev = json.loads(io.open(ruta_json, encoding="utf-8-sig").read())
    except Exception as e:
        if log:
            log.warning("  persistencia: no pude leer el JSON previo: %s" % e)
        prev = {}

    prev_subs = prev.get("sub_scores") or {}
    prev_dets = prev.get("sub_detalles") or {}
    prev_fechas = prev.get("sub_fechas") or {}

    for nombre in pesos:
        if sub_scores.get(nombre) is not None:
            fechas[nombre] = ahora.isoformat()
            continue

        valor = prev_subs.get(nombre)
        if valor is None:
            continue

        f = _parse(prev_fechas.get(nombre))
        if f is None:
            # JSON antiguo sin sub_fechas: lo damos por bueno ahora para no
            # botarlo, y de esta corrida en adelante queda fechado.
            f = ahora
        edad_h = (ahora - f).total_seconds() / 3600.0

        if edad_h > max_edad_h:
            if log:
                log.warning("  %s: el ultimo dato tiene %.1f h (tope %d h): se descarta"
                            % (nombre, edad_h, max_edad_h))
            continue

        sub_scores[nombre] = valor
        if prev_dets.get(nombre) is not None:
            sub_detalles[nombre] = prev_dets.get(nombre)
        fechas[nombre] = f.isoformat()
        reusadas.append(nombre)
        if log:
            log.info("  %s: fallo en esta corrida, reutilizo el dato de hace %.1f h"
                     % (nombre, edad_h))

    return sub_scores, sub_detalles, fechas, reusadas
