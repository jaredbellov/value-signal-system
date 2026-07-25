# -*- coding: utf-8 -*-
"""
BPA 5 anos - Paso 1: numero de acciones emitidas por empresa.

Fuente: yfinance (sharesOutstanding; fallback marketCap/precio BCS).
VALIDACION AUTOMATICA por empresa: el P/B implicito
   (precio BCS x acciones) / patrimonio CMF
debe caer en rango razonable (0.15 - 12). Si no, la empresa queda marcada
como NO VALIDADA y el BPA no se mostrara para ella (mejor sin dato que
con dato falso - leccion yfinance en chilenas chicas).

Guarda acciones_emitidas.json:
  {ticker: {"n_acciones": int, "fuente": str, "pb_implicito": float,
            "valida": bool}}

Uso:  python construir_acciones_emitidas.py
"""
import io, json, sys, time
from pathlib import Path

REPO = Path(r"C:\value-signal-local\repo")
OUT = REPO / "acciones_emitidas.json"

try:
    import yfinance as yf
except ImportError:
    sys.exit("ERROR: yfinance no instalado en este Python.")

d = json.load(io.open(REPO / "acciones_chilenas.json", encoding="utf-8"))
acciones = d.get("acciones", [])
print(f"Watchlist: {len(acciones)} tickers")

resultado = {}
if OUT.exists():
    resultado = json.load(io.open(OUT, encoding="utf-8"))
    print(f"Archivo previo: {len(resultado)} registros (se re-validan igual)")

for a in acciones:
    t = a["ticker"]
    precio = a.get("precio_actual_clp") or 0
    patr_miles = ((a.get("cmf") or {}).get("patrimonio_total")) or 0
    yft = t.replace("-", ".") + ".SN"   # AGUAS-A -> AGUAS.A.SN? probar directo
    n = None
    fuente = None
    try:
        tk = yf.Ticker(t + ".SN")
        info = tk.info or {}
        n = info.get("sharesOutstanding")
        fuente = "yfinance sharesOutstanding"
        if not n:
            mc = info.get("marketCap")
            if mc and precio:
                n = mc / precio
                fuente = "yfinance marketCap / precio BCS"
    except Exception as e:
        print(f"  {t}: yfinance fallo ({e})")

    if not n and t == "AGUAS-A":
        # ticker alternativo tipico
        try:
            tk = yf.Ticker("AGUAS-A.SN")
            info = tk.info or {}
            n = info.get("sharesOutstanding") or (
                (info.get("marketCap") or 0) / precio if precio else None)
            fuente = "yfinance (AGUAS-A.SN)"
        except Exception:
            pass

    if not n:
        resultado[t] = {"n_acciones": None, "fuente": "no disponible",
                        "pb_implicito": None, "valida": False}
        print(f"  {t:<12} SIN DATO de acciones -> BPA no se mostrara")
        time.sleep(0.5)
        continue

    n = int(n)
    # validacion cruzada: P/B implicito con patrimonio CMF (miles CLP)
    pb = None
    valida = False
    if precio and patr_miles:
        pb = (precio * n) / (patr_miles * 1000)
        valida = 0.15 <= pb <= 12
    resultado[t] = {"n_acciones": n, "fuente": fuente,
                    "pb_implicito": round(pb, 2) if pb else None,
                    "valida": bool(valida)}
    estado = "OK" if valida else "NO VALIDA (P/B fuera de rango)"
    print(f"  {t:<12} {n:>15,} acc  P/B impl: {pb if pb else '—':>6}  {estado}")
    time.sleep(0.5)

io.open(OUT, "w", encoding="utf-8").write(
    json.dumps(resultado, ensure_ascii=False, indent=1))
print()
validas = sum(1 for v in resultado.values() if v.get("valida"))
print(f"Guardado {OUT.name}: {validas}/{len(resultado)} empresas validadas.")
print("Las no validadas quedan sin BPA (puedes corregirlas a mano en el JSON")
print("con el N de acciones de la memoria anual y valida=true).")
