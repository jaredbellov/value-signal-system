# -*- coding: utf-8 -*-
"""Validacion local del cache v2 (solo lectura, imprime resumen)."""
import json, io

d = json.load(io.open(r"C:\value-signal-local\repo\flujos_historicos.json", encoding="utf-8"))

print("MONEDAS POR EMPRESA:")
problemas = []
for t in sorted(d):
    regs = d[t]
    monedas = "/".join(sorted(set(r.get("moneda_origen", "?") for r in regs)))
    utils = [r["utilidad"] for r in regs if r.get("utilidad")]
    salto = ""
    for i in range(1, len(utils)):
        a, b = abs(utils[i-1]), abs(utils[i])
        if a > 0 and b > 0 and (a/b > 50 or b/a > 50):
            salto = "  SALTO>50x"
            problemas.append(t)
    print(f"  {t:<12} {monedas}{salto}")

def reg(t, aa):
    return next((r for r in d.get(t, []) if r["ano"] == aa), None)

oxi = reg("OXIQUIM", 2025)
p24, p25 = reg("PEHUENCHE", 2024), reg("PEHUENCHE", 2025)
col = reg("COLBUN", 2025)
enel = reg("ENELGXCH", 2025)
soq = reg("SOQUICOM", 2025)

print()
print(f"OXIQUIM 2025 op: {oxi['flujo_op']:,.0f}  (esperado ~45.594.838)")
if p24 and p25 and p24.get("utilidad") and p25.get("utilidad"):
    print(f"PEHUENCHE 24->25: {p24['moneda_origen']}->{p25['moneda_origen']}, "
          f"ratio {p25['utilidad']/p24['utilidad']:.2f}  (esperado 0.3-3)")
if col:
    print(f"COLBUN 2025: {col['moneda_origen']} tc={col.get('tc')}  "
          f"util {col['utilidad']/1e6:,.0f} mil MM CLP")
if enel:
    print(f"ENELGXCH 2025: {enel['moneda_origen']}  util {enel['utilidad']/1e6:,.0f} mil MM CLP")
if soq:
    print(f"SOQUICOM 2025: {soq['moneda_origen']}  util {soq['utilidad']/1e6:,.1f} mil MM CLP")
con_patr = sum(1 for regs in d.values() for r in regs if r.get("patrimonio"))
print()
print(f"Saltos >50x restantes: {problemas if problemas else 'NINGUNO'}")
print(f"Registros con patrimonio: {con_patr}/85")
