# -*- coding: utf-8 -*-
"""
VIGILANTE DE FRESCURA - Value Signal System
===========================================
Corre en GitHub Actions y avisa por correo cuando los datos de la pagina
dejan de actualizarse o llegan incompletos.

POR QUE EXISTE
--------------
Todas las fallas de este sistema han sido silenciosas:
  - el scraper chileno estuvo 32 dias caido y se descubrio mirando la pagina
  - update_prices.py corrio 6 meses sin escribir una sola linea de log
  - prices.json mostro un precio 12% equivocado durante semanas
En los tres casos la pagina siguio mostrando numeros con cara de frescos y
nadie se entero hasta que una persona se dio cuenta. alert_monitor.py vigila
zonas de score, pero no vigila si los datos siguen llegando.

POR QUE EN LA NUBE
------------------
Si lo que murio es el PC que genera los datos, un vigilante local se muere
con el. Este corre en GitHub Actions, independiente de esa maquina.

ANTIGUEDAD = SELLO INTERNO DEL JSON, NO mtime
---------------------------------------------
En un runner el checkout reescribe las fechas de todos los archivos, asi que
el mtime siempre diria "recien". La edad real esta dentro de cada JSON.
"""
import io
import json
import os
import smtplib
import sys
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

REPO       = Path(__file__).resolve().parent
ESTADO     = REPO / "vigilante_state.json"
SMTP_HOST  = "smtp.gmail.com"
SMTP_PORT  = 587
SMTP_USER  = os.environ.get("GMAIL_USER", "")
SMTP_PASS  = os.environ.get("GMAIL_APP_PASSWORD", "")
EMAIL_TO   = os.environ.get("EMAIL_TO", SMTP_USER)

# Cada fuente tiene su propio ritmo. El umbral es generoso a proposito:
# vale mas un aviso tardio que un vigilante que grita en falso y se ignora.
#   archivo: (clave de fecha, horas de tolerancia, nombre legible)
FUENTES = {
    "acciones_chilenas.json":  ("fecha_actualizacion",  8, "Acciones chilenas"),
    "prices.json":             ("updated_at_utc",       6, "Fondos BCS (CFI)"),
    "btc_data.json":           ("updated_at_utc",       6, "Score Bitcoin"),
    "gld_data.json":           ("updated_at_utc",       6, "Score Oro"),
    "dividend_etfs_data.json": ("updated_at_utc",       8, "ETFs de dividendos"),
    "acciones_usa_data.json":  ("updated_at_utc",       8, "Acciones USA"),
    "sp_nq_data.json":         ("updated_at_utc",      12, "ETFs de crecimiento"),
    "noticias_chile.json":     ("updated_at_utc",      24, "Noticias Chile"),
    "noticias_usa.json":       ("updated_at_utc",      24, "Noticias USA"),
    "noticias_btc.json":       ("updated_at_utc",      24, "Noticias BTC"),
    "noticias_gld.json":       ("updated_at_utc",      24, "Noticias Oro"),
    "noticias_etfs.json":      ("updated_at_utc",      24, "Noticias ETFs"),
}

COBERTURA_MIN = 70     # % de senales con dato para que un score valga
REAVISAR_H    = 24     # no repetir el mismo aviso antes de esto


def ahora():
    return datetime.now(timezone.utc)


def parse_fecha(txt):
    """Acepta ISO con zona (updated_at_utc) y sin zona (fecha_actualizacion,
    que el scraper escribe en hora de Chile)."""
    if not txt:
        return None
    try:
        d = datetime.fromisoformat(str(txt).replace("Z", "+00:00"))
    except Exception:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone(timedelta(hours=-3)))   # hora de Chile
    return d.astimezone(timezone.utc)


def revisar():
    """Devuelve {id_problema: descripcion} con todo lo que esta mal ahora."""
    problemas = {}
    for archivo, (clave, umbral_h, nombre) in FUENTES.items():
        ruta = REPO / archivo
        if not ruta.exists():
            problemas["falta:" + archivo] = "%s: el archivo %s no existe en el repo" % (nombre, archivo)
            continue
        try:
            d = json.loads(io.open(ruta, encoding="utf-8-sig").read())
        except Exception as e:
            problemas["ilegible:" + archivo] = "%s: %s no es JSON valido (%s)" % (nombre, archivo, e)
            continue

        f = parse_fecha(d.get(clave))
        if f is None:
            problemas["sinsello:" + archivo] = "%s: %s no trae fecha utilizable en '%s'" % (nombre, archivo, clave)
            continue
        edad_h = (ahora() - f).total_seconds() / 3600.0
        if edad_h > umbral_h:
            problemas["viejo:" + archivo] = (
                "%s: sin actualizarse hace %.1f horas (tolerancia %d h). "
                "Ultimo dato: %s" % (nombre, edad_h, umbral_h, f.astimezone(timezone(timedelta(hours=-3))).strftime("%d-%m %H:%M"))
            )

        # --- revisiones de contenido, no solo de fecha ---
        if archivo in ("btc_data.json", "gld_data.json"):
            cob = d.get("peso_aplicado_pct")
            if isinstance(cob, (int, float)) and cob < COBERTURA_MIN:
                faltan = [k for k, v in (d.get("sub_scores") or {}).items() if v is None]
                problemas["cobertura:" + archivo] = (
                    "%s: score calculado con solo %d%% de sus senales. Sin dato: %s"
                    % (nombre, cob, ", ".join(faltan) or "?")
                )
        if archivo == "acciones_chilenas.json":
            if d.get("parcial"):
                problemas["parcial:" + archivo] = (
                    "%s: la ultima corrida quedo incompleta (%s de %s con dato fresco)"
                    % (nombre, d.get("frescas"), d.get("total"))
                )
        if archivo == "prices.json":
            errs = d.get("errores") or []
            stales = [t for t, v in (d.get("prices") or {}).items() if v.get("stale")]
            if errs or stales:
                problemas["precios:" + archivo] = (
                    "%s: %s%s" % (nombre,
                                  ("errores: " + "; ".join(errs)) if errs else "",
                                  ("  precios congelados: " + ", ".join(stales)) if stales else "")
                )
    return problemas


def cargar_estado():
    try:
        return json.loads(io.open(ESTADO, encoding="utf-8-sig").read())
    except Exception:
        return {}


def guardar_estado(e):
    io.open(ESTADO, "w", encoding="utf-8").write(json.dumps(e, ensure_ascii=False, indent=2))


def enviar(asunto, html):
    if not SMTP_USER or not SMTP_PASS:
        print("ERROR: faltan GMAIL_USER / GMAIL_APP_PASSWORD")
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"] = asunto
    msg["From"] = SMTP_USER
    msg["To"] = EMAIL_TO
    msg.attach(MIMEText(html, "html"))
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
        print("Email enviado a %s" % EMAIL_TO)
        return True
    except Exception as e:
        print("ERROR enviando email: %s" % e)
        return False


def html_aviso(nuevos, resueltos):
    p = ['<div style="font-family:system-ui,sans-serif;max-width:620px">']
    if nuevos:
        p.append('<h2 style="color:#c0392b;margin:0 0 4px">Datos sin actualizar</h2>')
        p.append('<p style="color:#555;margin:0 0 14px">La pagina sigue mostrando el ultimo valor conocido de estas fuentes.</p><ul>')
        for d in nuevos.values():
            p.append('<li style="margin-bottom:7px">%s</li>' % d)
        p.append("</ul>")
    if resueltos:
        p.append('<h3 style="color:#27ae60;margin:16px 0 4px">Se normalizo</h3><ul>')
        for d in resueltos:
            p.append("<li>%s</li>" % d)
        p.append("</ul>")
    p.append('<p style="color:#888;font-size:12px;margin-top:18px">'
             'Vigilante de frescura · corre en GitHub Actions, independiente del PC '
             'que genera los datos · %s UTC</p></div>' % ahora().strftime("%Y-%m-%d %H:%M"))
    return "".join(p)


def main():
    seco = "--dry-run" in sys.argv
    problemas = revisar()
    estado = cargar_estado()
    avisados = estado.get("avisados", {})

    nuevos = {}
    for pid, desc in problemas.items():
        ultimo = parse_fecha(avisados.get(pid))
        if ultimo is None or (ahora() - ultimo).total_seconds() / 3600.0 >= REAVISAR_H:
            nuevos[pid] = desc

    resueltos = [pid for pid in avisados if pid not in problemas]

    print("=" * 66)
    print("VIGILANTE DE FRESCURA  %s UTC" % ahora().strftime("%Y-%m-%d %H:%M"))
    print("=" * 66)
    print("Fuentes revisadas : %d" % len(FUENTES))
    print("Problemas ahora   : %d" % len(problemas))
    for d in problemas.values():
        print("   - %s" % d)
    print("Avisos a enviar   : %d" % len(nuevos))
    print("Resueltos         : %d  %s" % (len(resueltos), ", ".join(resueltos) or ""))

    if seco:
        print("\n[--dry-run] no envio correo ni toco el estado.")
        return 0

    if nuevos or resueltos:
        asunto = "Value Signal: %d fuente(s) sin actualizar" % len(nuevos) if nuevos \
                 else "Value Signal: datos normalizados"
        enviar(asunto, html_aviso(nuevos, [avisados[r].get("desc", r) if isinstance(avisados[r], dict) else r
                                           for r in resueltos]))

    for pid in nuevos:
        avisados[pid] = ahora().isoformat()
    for pid in resueltos:
        avisados.pop(pid, None)
    estado["avisados"] = avisados
    estado["ultima_revision"] = ahora().isoformat()
    guardar_estado(estado)
    return 0


if __name__ == "__main__":
    sys.exit(main())
