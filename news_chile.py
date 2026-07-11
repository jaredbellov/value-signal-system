"""
Fetch noticias macro de Chile para acciones dividenderas desde Google News RSS.
Cubre: BCCh/TPM, IPSA, dolar/cobre, economia Chile, politica/regulacion, sectores watchlist.
Sin API key necesaria.

Output: noticias_chile.json
"""
import json
import logging
import re
import subprocess as _sp
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
LOG_DIR = SCRIPT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "news_chile.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

OUTPUT_FILE = SCRIPT_DIR / "noticias_chile.json"
VENTANA_DIAS = 3
MAX_POR_CATEGORIA = 5
ASSET_LABEL_RESUMEN = "las acciones dividenderas chilenas del IPSA"

QUERIES = {
    "bcch_tpm": [
        "Banco Central Chile TPM",
        "tasa politica monetaria Chile",
        "Banco Central tasa decision",
    ],
    "ipsa": [
        "IPSA bolsa Santiago",
        "bolsa chilena hoy",
        "acciones chilenas dividendos",
    ],
    "dolar_cobre": [
        "precio cobre hoy",
        "dolar Chile peso chileno",
        "cobre proyeccion demanda",
    ],
    "economia_chile": [
        "Imacec Chile",
        "inflacion Chile IPC",
        "economia chilena crecimiento",
    ],
    "politica_regulacion": [
        "reforma tributaria Chile",
        "Congreso Chile proyecto ley empresas",
        "regulacion servicios basicos Chile",
    ],
    "pensiones_afp": [
        "reforma pensiones Chile AFP",
        "AFP Habitat PlanVital utilidades",
        "retiro fondos pensiones Chile",
        "cotizacion adicional pensiones Chile",
    ],
    "energia_tarifas": [
        "tarifas electricas Chile CNE",
        "descongelamiento tarifas electricas",
        "hidrologia embalses generacion Chile",
        "Colbun Enel Generacion Pehuenche",
    ],
    "sectores_watchlist": [
        "Zofri zona franca Iquique",
        "Cenco Malls centros comerciales Chile",
        "Lipigas gas licuado Chile",
        "SQM fertilizantes litio resultados",
    ],
}

# ============================================================
# HECHOS ESENCIALES CMF (via visfin.cl)
# ============================================================
# Mapeo ticker -> keywords de razon social (CMF/visfin usan mayusculas).
# Matching por substring sobre texto normalizado (sin tildes, minusculas).
WATCHLIST_HECHOS_ESENCIALES = {
    "HABITAT":    ["afp habitat"],
    "ZOFRI":      ["zona franca de iquique", "zofri"],
    "PEHUENCHE":  ["pehuenche"],
    "TRICAHUE":   ["fondo de inversion tricahue", "tricahue capital"],
    "COLBUN":     ["colbun"],
    "ENELGXCH":   ["enel generacion", "enel chile"],
    "LIPIGAS":    ["empresas lipigas", "lipigas"],
    "NTGCLGAS":   ["empresas gasco", "gasco sa", "gasco s a"],
    "SOQUICOM":   ["sociedad quimica y minera"],
    "QUINENCO":   ["quinenco"],
    "CENCOMALLS": ["cencosud shopping"],
    "OXIQUIM":    ["oxiquim"],
}
VISFIN_HECHOS_URL = "https://visfin.cl/hechos-esenciales"


CATEGORIA_META = {
    "bcch_tpm":            {"emoji": "🏦", "label": "Banco Central / TPM"},
    "ipsa":                {"emoji": "📈", "label": "IPSA / Bolsa local"},
    "dolar_cobre":         {"emoji": "🪙", "label": "Dolar / Cobre"},
    "economia_chile":      {"emoji": "🇨🇱", "label": "Economia Chile"},
    "politica_regulacion": {"emoji": "⚖️", "label": "Politica / Regulacion"},
    "pensiones_afp":       {"emoji": "🧓", "label": "Pensiones / AFP"},
    "energia_tarifas":     {"emoji": "⚡", "label": "Energia / Tarifas"},
    "sectores_watchlist":  {"emoji": "🏢", "label": "Sectores del watchlist"},
}

# ============================================================
# Diccionario de keywords con peso e impacto en BTC
# ============================================================
IMPACTO_KEYWORDS = {
    # === POSITIVO PARA DIVIDENDERAS CHILENAS ===
    # --- TPM / tasas (dividenderas compiten contra depositos) ---
    "baja la tasa":        ("positivo", 3, "TPM baja: dividenderas mas atractivas vs depositos"),
    "recorta la tasa":     ("positivo", 3, "BCCh recorta: positivo para dividenderas"),
    "recorte de tasa":     ("positivo", 3, "Recorte de TPM: positivo para dividenderas"),
    "tpm baja":            ("positivo", 3, "TPM baja: positivo para dividenderas"),
    "reduce la tasa":      ("positivo", 3, "BCCh reduce tasa: positivo"),
    "tasa se mantiene":    ("positivo", 1, "TPM estable: sin presion adicional"),
    # --- Cobre / CLP ---
    "cobre sube":          ("positivo", 2, "Cobre al alza: soporte para economia y CLP"),
    "cobre se dispara":    ("positivo", 3, "Cobre disparado: muy positivo para Chile"),
    "precio cobre alza":   ("positivo", 2, "Cobre al alza: soporte macro"),
    "copper rises":        ("positivo", 2, "Cobre al alza: soporte para Chile"),
    "demanda de cobre":    ("positivo", 2, "Demanda de cobre: viento a favor"),
    "dolar baja":          ("positivo", 1, "Dolar baja: CLP fuerte"),
    "peso se aprecia":     ("positivo", 1, "CLP fuerte: estabilidad"),
    # --- IPSA / mercado ---
    "ipsa sube":           ("positivo", 2, "IPSA al alza"),
    "ipsa record":         ("positivo", 3, "IPSA en records"),
    "ipsa maximo":         ("positivo", 3, "IPSA en maximos"),
    "bolsa chilena sube":  ("positivo", 2, "Bolsa local al alza"),
    "bolsa de santiago sube": ("positivo", 2, "Bolsa local al alza"),
    # --- Economia ---
    "imacec crece":        ("positivo", 2, "Imacec creciendo: soporte macro"),
    "imacec sobre lo esperado": ("positivo", 3, "Imacec sorprende al alza"),
    "inflacion baja":      ("positivo", 2, "IPC cede: abre espacio a recortes de TPM"),
    "ipc bajo lo esperado":("positivo", 2, "IPC bajo lo esperado: positivo"),
    "inflacion cede":      ("positivo", 2, "Inflacion cede: positivo"),
    "crecimiento economico":("positivo", 1, "Crecimiento: soporte macro"),
    "inversion extranjera":("positivo", 1, "Inversion extranjera: confianza"),
    "empleo mejora":       ("positivo", 1, "Empleo mejorando"),
    # --- Dividendos / utilidades ---
    "utilidades crecen":   ("positivo", 3, "Utilidades crecientes: mas dividendos"),
    "aumenta utilidades":  ("positivo", 3, "Utilidades al alza: mas dividendos"),
    "mayores dividendos":  ("positivo", 3, "Dividendos al alza"),
    "reparto de dividendos":("positivo", 2, "Reparto de dividendos confirmado"),
    "dividend yield":      ("positivo", 1, "Foco en dividend yield"),
    # --- Pensiones / AFP ---
    "fin a los retiros":   ("positivo", 2, "Sin nuevos retiros: estabilidad de AUM para AFPs"),
    # peso 4: compensa colision con "retiro de fondos"/"nuevo retiro" (neg 3)
    "rechaza retiro":      ("positivo", 4, "Retiro rechazado: alivio para AFPs"),
    "rechaza el retiro":   ("positivo", 4, "Retiro rechazado: alivio para AFPs"),
    "rechazo del retiro":  ("positivo", 4, "Retiro rechazado: alivio para AFPs"),
    "aumenta cotizacion":  ("positivo", 2, "Mayor cotizacion: mas flujo administrado por AFPs"),
    "alza de cotizacion":  ("positivo", 2, "Mayor cotizacion: mas AUM para AFPs"),
    "fondos suben":        ("positivo", 1, "Fondos de pensiones al alza: mas AUM y comisiones"),
    "rentabilidad de los fondos":("positivo", 1, "Rentabilidad de fondos en foco"),
    "utilidades afp":      ("positivo", 2, "Utilidades AFP: directo al watchlist (HABITAT)"),
    # --- Energia / Tarifas ---
    # peso 3: compensa colision con "congelamiento de tarifas" (substring)
    "descongelamiento":    ("positivo", 3, "Descongelamiento de tarifas: electricas recuperan ingresos"),
    "alza de tarifas electricas":("positivo", 2, "Tarifas al alza: positivo para reguladas"),
    "normalizacion tarifaria":("positivo", 2, "Normalizacion tarifaria: recupera flujos electricos"),
    "pago deuda tarifaria":("positivo", 2, "Pago de deuda tarifaria: caja para electricas"),
    # --- Watchlist especifico ---
    "hidrologia favorable":("positivo", 3, "Buena hidrologia: positivo hidroelectricas (PEHUENCHE/COLBUN)"),
    "lluvias benefician":  ("positivo", 2, "Lluvias: positivo para generacion hidro"),
    "embalses llenos":     ("positivo", 2, "Embalses llenos: positivo hidro"),

    # === NEGATIVO PARA DIVIDENDERAS CHILENAS ===
    # --- TPM / tasas ---
    "sube la tasa":        ("negativo", 3, "TPM sube: depositos compiten contra dividenderas"),
    "alza de tasa":        ("negativo", 3, "Alza de TPM: presion para dividenderas"),
    "tpm sube":            ("negativo", 3, "TPM sube: presion"),
    "tasa mas alta":       ("negativo", 2, "Tasas altas: presion para dividenderas"),
    # --- Cobre / CLP ---
    "cobre cae":           ("negativo", 2, "Cobre a la baja: presion para Chile"),
    "cobre se desploma":   ("negativo", 3, "Cobre desplomado: muy negativo"),
    "copper falls":        ("negativo", 2, "Cobre a la baja"),
    "dolar sube":          ("negativo", 1, "Dolar al alza: presion CLP"),
    "dolar se dispara":    ("negativo", 2, "Dolar disparado: estres cambiario"),
    # --- IPSA / mercado ---
    "ipsa cae":            ("negativo", 2, "IPSA a la baja"),
    "bolsa chilena cae":   ("negativo", 2, "Bolsa local a la baja"),
    "fuga de capitales":   ("negativo", 3, "Fuga de capitales: muy negativo"),
    # --- Economia ---
    "imacec cae":          ("negativo", 2, "Imacec cayendo: desaceleracion"),
    "imacec bajo lo esperado": ("negativo", 2, "Imacec decepciona"),
    "contraccion":         ("negativo", 2, "Contraccion economica"),
    "recesion":            ("negativo", 3, "Riesgo de recesion: muy negativo"),
    "inflacion sube":      ("negativo", 2, "IPC sube: aleja recortes de TPM"),
    "ipc sobre lo esperado":("negativo", 2, "IPC sorprende al alza: presion"),
    "desempleo sube":      ("negativo", 2, "Desempleo al alza: desaceleracion"),
    "rebaja clasificacion":("negativo", 3, "Downgrade: muy negativo"),
    "downgrade":           ("negativo", 3, "Downgrade: muy negativo"),
    # --- Politica / regulacion ---
    "reforma tributaria":  ("negativo", 2, "Reforma tributaria: incertidumbre impositiva"),
    "alza de impuestos":   ("negativo", 2, "Mas impuestos: presion sobre utilidades"),
    "royalty":             ("negativo", 1, "Royalty: presion sectorial"),
    "incertidumbre politica":("negativo", 2, "Incertidumbre politica: presion"),
    "estallido":           ("negativo", 3, "Conflicto social: muy negativo"),
    "protestas":           ("negativo", 1, "Protestas: ruido politico"),
    # --- Watchlist especifico ---
    "retiro de fondos":    ("negativo", 3, "Retiros AFP: muy negativo para HABITAT/PLANVITAL"),
    "retiros afp":         ("negativo", 3, "Retiros AFP: muy negativo para AFPs"),
    "nuevo retiro":        ("negativo", 3, "Nuevo retiro en discusion: riesgo de AUM para AFPs"),
    "reforma de pensiones":("negativo", 2, "Reforma pensiones: riesgo regulatorio para AFPs"),
    "reforma previsional": ("negativo", 2, "Reforma previsional: riesgo regulatorio para AFPs"),
    "afp estatal":         ("negativo", 2, "AFP estatal: competencia regulatoria"),
    "fin de las afp":      ("negativo", 3, "Fin de las AFP: riesgo existencial del modelo"),
    "expropiacion de fondos":("negativo", 3, "Expropiacion de fondos: muy negativo para AFPs"),
    "licitacion del stock":("negativo", 2, "Licitacion del stock de afiliados: presion a incumbentes"),
    "rebaja de comisiones":("negativo", 2, "Comisiones a la baja: presion de margenes AFP"),
    "congelamiento de tarifas":("negativo", 2, "Tarifas congeladas: presion electricas reguladas"),
    "rebaja de tarifas":   ("negativo", 2, "Tarifas a la baja: menos ingresos regulados"),
    "racionamiento electrico":("negativo", 3, "Racionamiento: estres del sistema electrico"),
    "decreto de racionamiento":("negativo", 3, "Decreto de racionamiento: estres electrico"),
    "sequia":              ("negativo", 2, "Sequia: presion para generacion hidro (PEHUENCHE/COLBUN)"),
    "megasequia":          ("negativo", 3, "Megasequia: muy negativo para hidroelectricas"),
    "embalses bajos":      ("negativo", 2, "Embalses bajos: menor generacion hidro"),
    "tarifas electricas congeladas":("negativo", 2, "Tarifas congeladas: presion electricas"),
    "sequia":              ("negativo", 3, "Sequia: negativo hidroelectricas (PEHUENCHE/COLBUN)"),
    "estres hidrico":      ("negativo", 2, "Estres hidrico: presion generacion hidro"),
    "recorta dividendo":   ("negativo", 3, "Recorte de dividendo: golpe directo"),
    "menores utilidades":  ("negativo", 3, "Utilidades a la baja: menos dividendos"),
    "caen utilidades":     ("negativo", 3, "Utilidades cayendo: menos dividendos"),
    "perdidas":            ("negativo", 2, "Perdidas reportadas"),
}


def build_query_url(keyword):
    q = urllib.parse.quote_plus(keyword)
    return f"https://news.google.com/rss/search?q={q}&hl=es-CL&gl=CL&ceid=CL:es"


def fetch_rss(url, timeout=20):
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        log.warning(f"  fetch_rss fallo: {e}")
        return None


def parse_rss(xml_text):
    items = []
    if not xml_text:
        return items
    try:
        root = ET.fromstring(xml_text)
        channel = root.find("channel")
        if channel is None:
            return items
        for item in channel.findall("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pubdate = (item.findtext("pubDate") or "").strip()
            source_el = item.find("source")
            source = (source_el.text if source_el is not None else "") or ""
            items.append({
                "title": title, "link": link, "pubdate": pubdate, "source": source.strip(),
            })
    except Exception as e:
        log.warning(f"  parse_rss fallo: {e}")
    return items


def parse_pubdate(pubdate_str):
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(pubdate_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def is_reciente(item, cutoff):
    dt = parse_pubdate(item.get("pubdate", ""))
    return dt is not None and dt >= cutoff


def clean_title(title):
    return re.sub(r"\s+-\s+[^-]+$", "", title).strip()


def normalizar(s):
    import unicodedata
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def analizar_impacto(titulo, categoria):
    """
    Analiza impacto del titulo. Mejoras:
    - Word boundary matching (no substring) para keywords cortas
    - Razonamiento del factor de mayor peso dominante en la direccion final
    """
    import re as _re
    titulo_norm = normalizar(titulo)
    score_pos = 0
    score_neg = 0
    matches_pos = []   # (peso, razon)
    matches_neg = []
    for kw, (direccion, peso, razon) in IMPACTO_KEYWORDS.items():
        kw_norm = normalizar(kw)
        # Si la keyword tiene >1 palabra, usar substring match (es especifica)
        # Si tiene 1 palabra, usar word boundary (\b...\b) para evitar falsos positivos
        if " " in kw_norm:
            matched = kw_norm in titulo_norm
        else:
            matched = bool(_re.search(r"\b" + _re.escape(kw_norm) + r"\b", titulo_norm))
        if matched:
            if direccion == "positivo":
                score_pos += peso
                matches_pos.append((peso, razon))
            else:
                score_neg += peso
                matches_neg.append((peso, razon))

    diff = score_pos - score_neg
    if abs(diff) < 1:
        direccion = "neutral"
        intensidad = 0
        razonamiento = "Sin senal direccional clara en el titular"
    elif diff > 0:
        direccion = "positivo"
        intensidad = min(3, max(1, abs(diff) // 2 + 1))
        # Tomar el razon del match positivo de mayor peso
        razonamiento = max(matches_pos, key=lambda x: x[0])[1] if matches_pos else "Sesgo positivo para dividenderas chilenas"
    else:
        direccion = "negativo"
        intensidad = min(3, max(1, abs(diff) // 2 + 1))
        razonamiento = max(matches_neg, key=lambda x: x[0])[1] if matches_neg else "Sesgo negativo para dividenderas chilenas"

    emoji_map = {
        ("positivo", 3): "🟢🟢", ("positivo", 2): "🟢", ("positivo", 1): "🟢",
        ("negativo", 3): "🔴🔴", ("negativo", 2): "🔴", ("negativo", 1): "🔴",
        ("neutral", 0): "⚪",
    }
    emoji = emoji_map.get((direccion, intensidad), "⚪")
    intensidad_label = {0: "neutral", 1: "leve", 2: "moderado", 3: "fuerte"}[intensidad]
    return {
        "direccion": direccion, "intensidad": intensidad,
        "intensidad_label": intensidad_label, "emoji": emoji, "razonamiento": razonamiento,
    }


def generar_resumen_ejecutivo(noticias_por_categoria):
    """
    Calcula sentimiento neto del feed y genera narrativa (Groq con fallback a plantilla).
    Retorna dict con sentimiento, contadores, narrativa y veredicto.
    """
    # --- 1. Sentimiento neto desde los impactos ya calculados ---
    score_neto = 0
    n_pos = 0
    n_neg = 0
    n_neu = 0
    titulares_con_senal = []
    for cat, items in noticias_por_categoria.items():
        for it in items:
            imp = it.get("impacto", {}) or {}
            d = imp.get("direccion", "neutral")
            i = imp.get("intensidad", 0)
            if d == "positivo":
                score_neto += i
                n_pos += 1
                titulares_con_senal.append(("+", i, it.get("title", ""), imp.get("razonamiento", "")))
            elif d == "negativo":
                score_neto -= i
                n_neg += 1
                titulares_con_senal.append(("-", i, it.get("title", ""), imp.get("razonamiento", "")))
            else:
                n_neu += 1

    # --- 2. Veredicto direccional ---
    if score_neto >= 4:
        sentimiento = "alcista"
        veredicto = "ALCISTA a corto plazo"
    elif score_neto <= -4:
        sentimiento = "bajista"
        veredicto = "BAJISTA a corto plazo"
    elif score_neto >= 2:
        sentimiento = "levemente alcista"
        veredicto = "Levemente ALCISTA"
    elif score_neto <= -2:
        sentimiento = "levemente bajista"
        veredicto = "Levemente BAJISTA"
    else:
        sentimiento = "neutral"
        veredicto = "NEUTRAL - sin sesgo dominante"

    # --- 3. Narrativa: Groq con fallback a plantilla ---
    narrativa = None
    narrativa_fuente = "plantilla"
    try:
        from groq_interpreter import groq_available, query_groq
        available, api_key = groq_available()
        if available and titulares_con_senal:
            # Ordenar por intensidad y tomar los 10 mas fuertes
            top_senales = sorted(titulares_con_senal, key=lambda x: x[1], reverse=True)[:10]
            lista_txt = "\n".join(
                f"[{s}{i}] {t[:100]} ({r})" for s, i, t, r in top_senales
            )
            prompt = (
                f"Eres analista financiero. Estas son las noticias de hoy sobre {ASSET_LABEL_RESUMEN}, "
                f"con su impacto estimado ([+N] positivo, [-N] negativo, N=intensidad 1-3):\n\n"
                f"{lista_txt}\n\n"
                f"El sentimiento neto del feed es {sentimiento} (score {score_neto:+d}, "
                f"{n_pos} positivas vs {n_neg} negativas).\n\n"
                f"Escribe un resumen de maximo 3 frases y 60 palabras en espanol que sintetice el panorama "
                f"y los principales catalizadores. Sin markdown, sin titulos, solo el texto plano."
            )
            respuesta = query_groq(prompt, api_key)
            if respuesta and len(respuesta.strip()) > 20:
                narrativa = respuesta.strip()[:600]
                narrativa_fuente = "groq"
    except Exception as e:
        log.warning(f"Groq no disponible para resumen: {e}")

    if not narrativa:
        # Plantilla determinista
        if titulares_con_senal:
            top = sorted(titulares_con_senal, key=lambda x: x[1], reverse=True)[:2]
            razones_top = "; ".join(r for _, _, _, r in top if r)
            narrativa = (
                f"El feed muestra {n_pos} senales positivas y {n_neg} negativas "
                f"(neto {score_neto:+d}). Factores dominantes: {razones_top}."
            )
        else:
            narrativa = "Sin senales direccionales claras en las noticias de las ultimas 72 horas."

    log.info(f"Resumen ejecutivo: {sentimiento} (neto {score_neto:+d}) via {narrativa_fuente}")
    return {
        "sentimiento": sentimiento,
        "score_neto": score_neto,
        "n_positivas": n_pos,
        "n_negativas": n_neg,
        "n_neutrales": n_neu,
        "narrativa": narrativa,
        "narrativa_fuente": narrativa_fuente,
        "veredicto": veredicto,
    }


def fetch_categoria(categoria, keywords, cutoff):
    log.info(f"\n--- Categoria: {categoria} ---")
    items_brutos = []
    for kw in keywords:
        url = build_query_url(kw)
        xml = fetch_rss(url)
        items = parse_rss(xml)
        items_brutos.extend(items)
        log.info(f"  '{kw}': {len(items)} resultados")
    recientes = [it for it in items_brutos if is_reciente(it, cutoff)]
    log.info(f"  Recientes (<={VENTANA_DIAS}d): {len(recientes)}")
    vistos = set()
    unicos = []
    for it in recientes:
        clave = normalizar(clean_title(it["title"]))
        if clave and clave not in vistos:
            vistos.add(clave)
            titulo_limpio = clean_title(it["title"])
            impacto = analizar_impacto(titulo_limpio, categoria)
            unicos.append({
                "title": titulo_limpio, "link": it["link"], "source": it["source"],
                "pubdate": it["pubdate"],
                "pubdate_iso": parse_pubdate(it["pubdate"]).isoformat() if parse_pubdate(it["pubdate"]) else "",
                "impacto": impacto,
            })
    unicos.sort(key=lambda x: x.get("pubdate_iso", ""), reverse=True)
    top = unicos[:MAX_POR_CATEGORIA]
    log.info(f"  Unicos: {len(unicos)}, top {len(top)} seleccionados")
    return top


def ejecutar_git(args):
    try:
        r = _sp.run(["git"] + args, cwd=SCRIPT_DIR, capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return -1, "", str(e)


def git_sync_and_push():
    # [PLAN-B] git neutralizado: publicar.py se encarga del push
    return
    code, out, _ = ejecutar_git(["status", "--porcelain", "noticias_chile.json"])
    if not out.strip():
        log.info("Sin cambios en noticias_chile.json")
        return
    ejecutar_git(["pull", "--rebase", "--autostash"])
    ejecutar_git(["add", "noticias_chile.json"])
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    ejecutar_git(["commit", "-m", f"chore: update noticias Chile {ts} [skip ci]"])
    code, _, err = ejecutar_git(["push"])
    if code != 0:
        ejecutar_git(["pull", "--rebase", "--autostash"])
        ejecutar_git(["push"])
    log.info("git push OK")



# ============================================================
# Hechos esenciales: scraping + filtrado por ticker
# ============================================================
def fetch_hechos_esenciales():
    """Descarga la tabla de hechos esenciales de visfin.cl (CMF, ultimos dias).
    Devuelve lista de dicts: fecha, empresa, materia, link, doc_id."""
    log.info("Descargando hechos esenciales de visfin.cl...")
    html = fetch_rss(VISFIN_HECHOS_URL, timeout=30)
    if not html:
        log.warning("  visfin no devolvio HTML")
        return []

    import html as _html_lib
    hechos = []
    row_pattern = re.compile(
        r'<tr[^>]*>\s*'
        r'<td[^>]*>(.*?)</td>\s*'      # fecha
        r'<td[^>]*>(.*?)</td>\s*'      # empresa
        r'<td[^>]*>(.*?)</td>\s*'      # materia
        r'<td[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>([^<]+)</a>\s*</td>',
        re.DOTALL | re.IGNORECASE,
    )
    for m in row_pattern.finditer(html):
        fecha_str = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        empresa = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        materia = re.sub(r"<[^>]+>", "", m.group(3)).strip()
        link = m.group(4).strip()
        doc_id = m.group(5).strip()
        if not empresa or not link or "cmfchile.cl" not in link:
            continue
        hechos.append({
            "fecha": fecha_str,
            "empresa": _html_lib.unescape(empresa),
            "materia": _html_lib.unescape(materia),
            "link": _html_lib.unescape(link),
            "doc_id": _html_lib.unescape(doc_id),
        })
    log.info(f"Hechos esenciales descargados: {len(hechos)}")
    return hechos


def filtrar_hechos_por_ticker(hechos, keywords):
    """Filtra hechos cuya razon social matchea alguna keyword del ticker."""
    out = []
    for h in hechos:
        empresa_norm = normalizar(h["empresa"])
        for kw in keywords:
            if kw in empresa_norm:
                out.append({
                    "title": f"{h['empresa']}: {h['materia']}",
                    "link": h["link"],
                    "pubdate": h["fecha"],
                    "source": "CMF (Hecho Esencial)",
                    "es_hecho_esencial": True,
                    "doc_id": h.get("doc_id", ""),
                    "empresa_cmf": h["empresa"],
                    "materia_cmf": h["materia"],
                })
                break  # un hecho cuenta una sola vez por ticker
    return out


def construir_hechos_por_ticker():
    """Descarga hechos globales y los agrupa por ticker. Con robustez:
    si visfin devuelve 0 (blip), reusa los del JSON anterior por doc_id."""
    hechos = fetch_hechos_esenciales()

    if not hechos and OUTPUT_FILE.exists():
        log.warning("Visfin devolvio 0 hechos. Reusando hechos del JSON anterior.")
        try:
            with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                prev = json.load(f)
            vistos = set()
            for lst in prev.get("hechos_esenciales_por_ticker", {}).values():
                for n in lst:
                    did = n.get("doc_id", "")
                    if did and did not in vistos:
                        vistos.add(did)
                        hechos.append({
                            "fecha": n.get("pubdate", ""),
                            "empresa": n.get("empresa_cmf", ""),
                            "materia": n.get("materia_cmf", ""),
                            "link": n.get("link", ""),
                            "doc_id": did,
                        })
            log.info(f"Hechos recuperados del JSON anterior: {len(hechos)}")
        except Exception as e:
            log.warning(f"No se pudo recuperar hechos anteriores: {e}")

    por_ticker = {}
    for tk, kws in WATCHLIST_HECHOS_ESENCIALES.items():
        f = filtrar_hechos_por_ticker(hechos, kws)
        if f:
            # ordenar por fecha desc cuando sea parseable (formato dd-mm-aaaa o aaaa-mm-dd)
            por_ticker[tk] = f
    total = sum(len(v) for v in por_ticker.values())
    log.info(f"Hechos esenciales asignados a watchlist: {total} en {len(por_ticker)} tickers")
    return por_ticker



def main():
    log.info("=" * 60)
    log.info("=== Update Noticias Chile ===")
    cutoff = datetime.now(timezone.utc) - timedelta(days=VENTANA_DIAS)
    log.info(f"Cutoff: {cutoff.isoformat()}")
    noticias_por_categoria = {}
    total = 0
    for categoria, keywords in QUERIES.items():
        noticias = fetch_categoria(categoria, keywords, cutoff)
        noticias_por_categoria[categoria] = noticias
        total += len(noticias)
    log.info("--- Cargando Hechos Esenciales CMF (visfin) ---")
    hechos_esenciales_por_ticker = construir_hechos_por_ticker()
    resumen_ejecutivo = generar_resumen_ejecutivo(noticias_por_categoria)
    output = {
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "ventana_dias": VENTANA_DIAS,
        "categorias_meta": CATEGORIA_META,
        "noticias_por_categoria": noticias_por_categoria,
        "hechos_esenciales_por_ticker": hechos_esenciales_por_ticker,
        "stats": {
            "total_noticias": total,
            "por_categoria": {k: len(v) for k, v in noticias_por_categoria.items()},
            "hechos_esenciales": sum(len(v) for v in hechos_esenciales_por_ticker.values()),
        },
        "resumen_ejecutivo": resumen_ejecutivo,
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    log.info(f"\nGuardado: {OUTPUT_FILE} ({total} noticias)")
    git_sync_and_push()
    log.info(f"=== FIN ({datetime.now().strftime('%H:%M:%S')}) ===")


if __name__ == "__main__":
    main()
