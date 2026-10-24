# -*- coding: utf-8 -*-
"""
RECALCULAR CALIDAD SOBRE EL JSON EXISTENTE
==========================================
Aplica la puerta de calidad a acciones_chilenas.json sin tocar la BCS.
El BPA, los flujos y los datos CMF ya estan en el JSON, asi que esto corre
en segundos y no depende del captcha de la bolsa.

Requisito: haber corrido antes fix_puerta_calidad.py (necesita la funcion
evaluar_calidad dentro de acciones_chilenas.py).

Uso:
    cd C:\\value-signal-local\\repo
    python recalcular_calidad.py
"""
import datetime
import io
import json
import os
import shutil
import sys

REPO = r"C:\value-signal-local\repo"
STAMP = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def buscar_jsons():
    """Todos los acciones_chilenas.json del repo (raiz y docs)."""
    encontrados = []
    for carpeta, _dirs, archivos in os.walk(REPO):
        if ".git" in carpeta:
            continue
        for a in archivos:
            if a == "acciones_chilenas.json":
                encontrados.append(os.path.join(carpeta, a))
    return encontrados


def main():
    if not os.path.isdir(REPO):
        print("ERROR: no encuentro " + REPO)
        return 1

    sys.path.insert(0, REPO)
    os.chdir(REPO)

    try:
        import acciones_chilenas as ac
    except Exception as e:
        print("ERROR importando acciones_chilenas.py: %s" % e)
        return 1

    if not hasattr(ac, "evaluar_calidad"):
        print("ERROR: acciones_chilenas.py no tiene evaluar_calidad().")
        print("       Corre primero:  python fix_puerta_calidad.py")
        return 1

    jsons = buscar_jsons()
    if not jsons:
        print("ERROR: no encontre ningun acciones_chilenas.json en el repo")
        return 1

    filas = []
    for ruta in jsons:
        data = json.loads(io.open(ruta, encoding="utf-8-sig").read())
        accs = data.get("acciones") or []
        if not accs:
            print("  (sin acciones) " + ruta)
            continue

        for a in accs:
            q = ac.evaluar_calidad(a)
            a["calidad"] = q
            ev = a.get("evaluacion") or {}
            antes = ev.get("status_original") or ev.get("status")

            # reponer el status original antes de volver a decidir (idempotente)
            if ev.get("status_original"):
                ev["status"] = ev.pop("status_original")
                ev["emoji"] = "\U0001f7e2"

            if ev.get("status") == "Sobre benchmark" and not q["aprobado"]:
                ev["status_original"] = "Sobre benchmark"
                ev["status"] = "Con reparos"
                ev["emoji"] = "\u26a0\ufe0f"

            a["evaluacion"] = ev
            if ruta == jsons[0]:
                filas.append((a.get("ticker", "?"), antes, ev.get("status"), q))

        data["calidad_recalculada"] = datetime.datetime.now().isoformat()
        shutil.copy(ruta, ruta + "." + STAMP + ".bak")
        salida = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        with open(ruta, "wb") as f:
            f.write(salida)
        print("  actualizado: %s (%d acciones)" % (ruta, len(accs)))

    # ---------------- informe ----------------
    print("")
    print("=" * 94)
    print("%-11s %-16s %-16s %-20s %-8s %-7s" %
          ("TICKER", "STATUS ANTES", "STATUS AHORA", "BPA", "DEUDA", "FCF"))
    print("=" * 94)

    def nx(v):
        return "s/d" if v is None else ("%.2fx" % v).replace(".", ",")

    aprobadas = 0
    degradadas = 0
    for tk, antes, ahora, q in sorted(filas, key=lambda r: (not r[3]["aprobado"], r[0])):
        if q["aprobado"]:
            aprobadas += 1
        if antes == "Sobre benchmark" and ahora == "Con reparos":
            degradadas += 1
        bpa = q["bpa_veredicto"]
        if q["bpa_cagr_pct"] is not None:
            bpa += " %s%%" % ("%.1f" % q["bpa_cagr_pct"]).replace(".", ",")
        print("%-11s %-16s %-16s %-20s %-8s %-7s %s" % (
            tk, antes or "-", ahora or "-", bpa,
            nx(q["endeudamiento_x"]), nx(q["fcf_cobertura"]),
            "OK" if q["aprobado"] else ""))
        for r in q["reparos"]:
            print("%-11s   \u26a0 %s" % ("", r))
        for r in q.get("avisos") or []:
            print("%-11s   i %s" % ("", r))
        for r in q["faltantes"]:
            print("%-11s   \u00b7 %s" % ("", r))

    con_nota = sum(1 for _tk, _a, _h, q in filas if q["aprobado"] and (q.get("avisos") or []))
    print("=" * 94)
    print("Aprobadas por la puerta de calidad: %d de %d (%d con nota)"
          % (aprobadas, len(filas), con_nota))
    print("Bajadas de 'Sobre benchmark' a 'Con reparos': %d" % degradadas)
    print("")
    print("Backups: *.%s.bak" % STAMP)
    print("El Publisher sube el cambio en la proxima pasada (hasta 15 min).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
