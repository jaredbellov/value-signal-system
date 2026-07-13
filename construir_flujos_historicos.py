# -*- coding: utf-8 -*-
"""
DETECTOR LA POLAR - Fase 1: cache historico de flujos.

Construye flujos_historicos.json con, por cada accion chilena de la watchlist:
  [{ano, utilidad, flujo_op, flujo_inv, flujo_fin, fcf}]   (miles CLP, CMF oficial)

FCF aproximado = flujo operacion + flujo inversion (la CMF no desglosa la
linea de CAPEX; el bloque inversion completo es una aproximacion CONSERVADORA).

- Fuente: cierres anuales (diciembre) de la CMF, 2021-2025.
- Idempotente: los anos ya presentes en el cache NO se re-descargan.
  La corrida inicial demora ~7-12 min (85 descargas con pausa cortesia).
  Las siguientes solo agregan lo que falte (ej. el cierre nuevo cada ano).
- Sonda: imprime la estructura de claves del primer ticker con datos,
  para verificar que el matching tolerante encontro los campos correctos.
- Validacion: OXIQUIM 2025 debe dar flujo_op ~ 45,595,000 (miles CLP).

Uso:  python construir_flujos_historicos.py
"""
import io, json, re, sys, time
from pathlib import Path

MCP = r"C:\mcp-bolsa"
if MCP not in sys.path:
    sys.path.insert(0, MCP)
from cmf import obtener_estados_financieros, extraer_cuentas_clave  # noqa

REPO = Path(r"C:\value-signal-local\repo")
CACHE_PATH = REPO / "flujos_historicos.json"
WATCHLIST_JSON = REPO / "acciones_chilenas.json"
ANOS = [2021, 2022, 2023, 2024, 2025]
PAUSA_SEG = 1.0

def _num(v):
    """Extrae un numero de valores que pueden venir como numero, dict
    {periodo: valor} o lista. Devuelve None si no hay numero."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict):
        try:
            ks = sorted(v.keys())
            for k in reversed(ks):
                n = _num(v[k])
                if n is not None:
                    return n
        except Exception:
            pass
        for x in v.values():
            n = _num(x)
            if n is not None:
                return n
        return None
    if isinstance(v, (list, tuple)):
        for x in v:
            n = _num(x)
            if n is not None:
                return n
        return None
    try:
        return float(str(v).replace(".", "").replace(",", "."))
    except Exception:
        return None

def _buscar(d, *patrones):
    """Primera clave del dict que matchee alguno de los patrones (regex, i)."""
    if not isinstance(d, dict):
        return None, None
    for pat in patrones:
        rx = re.compile(pat, re.I)
        for k in d.keys():
            if rx.search(str(k)):
                n = _num(d[k])
                if n is not None:
                    return n, str(k)
    return None, None

def main():
    # Watchlist desde el JSON publicado (los 17 tickers actuales)
    wl = json.load(io.open(WATCHLIST_JSON, encoding="utf-8"))
    tickers = [a["ticker"] for a in wl.get("acciones", [])]
    if not tickers:
        sys.exit("ERROR: no encontre tickers en acciones_chilenas.json")
    print(f"Watchlist: {len(tickers)} tickers -> {tickers}")

    cache = {}
    if CACHE_PATH.exists():
        cache = json.load(io.open(CACHE_PATH, encoding="utf-8"))
        print(f"Cache existente: {sum(len(v) for v in cache.values())} registros")

    sonda_impresa = False
    for t in tickers:
        registros = {r["ano"]: r for r in cache.get(t, [])}
        faltan = [a for a in ANOS if a not in registros]
        if not faltan:
            print(f"{t}: completo ({len(registros)} anos), omito.")
            continue
        print(f"{t}: descargando cierres {faltan}...")
        for aa in faltan:
            try:
                ef = obtener_estados_financieros(t, mm=12, aa=aa)
            except Exception as e:
                print(f"  {aa}: error descarga ({e})")
                time.sleep(PAUSA_SEG)
                continue
            if not ef:
                print(f"  {aa}: sin datos en CMF")
                time.sleep(PAUSA_SEG)
                continue
            cuentas = extraer_cuentas_clave(ef)
            flujo = cuentas.get("flujo") or {}
            eerr = cuentas.get("eerr") or {}

            if not sonda_impresa and (flujo or eerr):
                print(f"  [SONDA {t} {aa}] claves flujo: {list(flujo.keys())}")
                print(f"  [SONDA {t} {aa}] claves eerr:  {list(eerr.keys())[:8]}")
                sonda_impresa = True

            util, k_u = _buscar(eerr, r"atribuible.*controlador", r"^ganancia_perdida$", r"ganancia")
            op, k_o = _buscar(flujo, r"operaci")
            inv, k_i = _buscar(flujo, r"inversi")
            fin, k_f = _buscar(flujo, r"financ")
            fcf = (op + inv) if (op is not None and inv is not None) else None

            reg = {"ano": aa, "utilidad": util, "flujo_op": op,
                   "flujo_inv": inv, "flujo_fin": fin, "fcf": fcf,
                   "periodo": getattr(ef, "periodo", f"12/{aa}"),
                   "unidad": getattr(ef, "unidad", "Miles")}
            registros[aa] = reg
            ok = "OK" if (util is not None and op is not None) else "PARCIAL"
            print(f"  {aa}: {ok}  util={util}  op={op}  inv={inv}  fcf={fcf}")
            time.sleep(PAUSA_SEG)

        cache[t] = sorted(registros.values(), key=lambda r: r["ano"])
        # guardado incremental por si se corta
        io.open(CACHE_PATH, "w", encoding="utf-8").write(
            json.dumps(cache, ensure_ascii=False, indent=1))

    print()
    print("=" * 60)
    print("RESUMEN DEL CACHE:")
    for t in tickers:
        regs = cache.get(t, [])
        completos = [r["ano"] for r in regs if r.get("utilidad") is not None and r.get("fcf") is not None]
        print(f"  {t:<12} anos con datos completos: {completos}")

    # Validacion de oro: OXIQUIM 2025 flujo_op ~ 45,595,000 miles
    oxi = {r["ano"]: r for r in cache.get("OXIQUIM", [])}
    if 2025 in oxi and oxi[2025].get("flujo_op"):
        v = oxi[2025]["flujo_op"]
        estado = "COINCIDE con CMF conocida (~45,595,000)" if 40_000_000 < v < 50_000_000 else "REVISAR: no calza con el valor CMF conocido"
        print(f"\nVALIDACION OXIQUIM 2025: flujo_op = {v:,.0f} -> {estado}")
    print("\nCache guardado en:", CACHE_PATH)

if __name__ == "__main__":
    main()
