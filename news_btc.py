"""
Fetch noticias macro relacionadas con Bitcoin (BTC) e IBIT desde Google News RSS.
Cubre: precio BTC, flujos ETF, regulacion, adopcion, Fed/macro, sentimiento/on-chain.
Sin API key necesaria.

Output: noticias_btc.json
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
LOG_FILE = LOG_DIR / "news_btc.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

OUTPUT_FILE = SCRIPT_DIR / "noticias_btc.json"
VENTANA_DIAS = 3
MAX_POR_CATEGORIA = 5
ASSET_LABEL_RESUMEN = "Bitcoin (BTC) y su ETF IBIT"

QUERIES = {
    "precio": [
        "bitcoin precio hoy",
        "bitcoin price today",
        "BTC USD analisis",
    ],
    "etf_flows": [
        "IBIT BlackRock bitcoin",
        "spot bitcoin ETF flows",
        "bitcoin ETF inflows outflows",
    ],
    "regulacion": [
        "SEC bitcoin crypto",
        "regulacion cripto",
        "crypto regulation news",
        "trump crypto policy",
    ],
    "adopcion": [
        "bitcoin treasury company",
        "MicroStrategy Strategy bitcoin",
        "company buys bitcoin",
        "country adopts bitcoin",
    ],
    "fed_macro": [
        "Federal Reserve bitcoin",
        "inflation bitcoin crypto",
        "Fed rate decision crypto",
    ],
    "sentimiento_onchain": [
        "bitcoin halving cycle",
        "bitcoin whale accumulation",
        "bitcoin fear greed",
    ],
}

CATEGORIA_META = {
    "precio":             {"emoji": "💰", "label": "Precio Bitcoin"},
    "etf_flows":          {"emoji": "🏦", "label": "ETF spot (IBIT, etc)"},
    "regulacion":         {"emoji": "⚖️", "label": "Regulacion"},
    "adopcion":           {"emoji": "🌎", "label": "Adopcion institucional"},
    "fed_macro":          {"emoji": "📊", "label": "Fed / Macro"},
    "sentimiento_onchain":{"emoji": "🔗", "label": "Sentimiento / On-chain"},
}

# ============================================================
# Diccionario de keywords con peso e impacto en BTC
# ============================================================
IMPACTO_KEYWORDS = {
    # === POSITIVO PARA BTC ===
    # --- Fed dovish / inflacion (similar a oro) ---
    "rate cut":            ("positivo", 3, "Fed dovish: rate cut favorece BTC (risk-on + hedge)"),
    "rate cuts":           ("positivo", 3, "Fed dovish: rate cuts favorecen BTC"),
    "lower rates":         ("positivo", 2, "Lower rates favorece BTC"),
    "dovish":              ("positivo", 2, "Tono dovish de Fed favorece BTC"),
    "recorte de tasas":    ("positivo", 3, "Fed dovish: recorte de tasas favorece BTC"),
    "baja tasas":          ("positivo", 3, "Fed dovish favorece BTC"),
    "fed pivot":           ("positivo", 3, "Fed pivot: muy positivo para BTC"),
    "high inflation":      ("positivo", 2, "Alta inflacion: BTC como hedge digital"),
    "higher inflation":    ("positivo", 2, "Inflacion mas alta: BTC como hedge"),
    "rising inflation":    ("positivo", 2, "Inflacion creciente: BTC como hedge"),
    "inflacion mas alta":  ("positivo", 2, "Inflacion mas alta: BTC como hedge"),
    "inflacion sube":      ("positivo", 2, "Inflacion sube: BTC como hedge"),
    "stagflation":         ("positivo", 3, "Estanflacion: muy positivo para BTC"),
    "weak dollar":         ("positivo", 2, "Dolar debil: positivo para BTC"),
    "weaker dollar":       ("positivo", 2, "Dolar debil: positivo para BTC"),
    "dollar drops":        ("positivo", 2, "Dolar baja: positivo para BTC"),
    "dollar tumbles":      ("positivo", 2, "Dolar cae: positivo para BTC"),

    # --- ETF inflows / institucional ---
    "etf inflows":         ("positivo", 3, "Flujos institucionales hacia ETF: muy positivo"),
    "inflows":             ("positivo", 2, "Flujos institucionales hacia BTC"),
    "ibit inflows":        ("positivo", 3, "IBIT recibe flujos: demanda institucional"),
    "blackrock buys":      ("positivo", 3, "BlackRock acumulando: demanda institucional fuerte"),
    "blackrock accumulates":("positivo", 3, "BlackRock acumulando BTC"),
    "fidelity bitcoin":    ("positivo", 2, "Fidelity en BTC: demanda institucional"),
    "ark invest":          ("positivo", 1, "ARK Invest mencionado: senal institucional"),
    "spot etf demand":     ("positivo", 3, "Demanda de ETF spot: muy positivo"),
    "etf record":          ("positivo", 3, "ETFs en records: demanda institucional fuerte"),
    "spot bitcoin etf":    ("positivo", 2, "ETF spot mencionado: positivo institucional"),

    # --- Adopcion ---
    "company buys bitcoin":("positivo", 3, "Empresa compra BTC: adopcion corporativa"),
    "treasury bitcoin":    ("positivo", 3, "Tesoreria corporativa en BTC: tendencia bullish"),
    "bitcoin treasury":    ("positivo", 3, "Tesoreria corporativa en BTC: tendencia bullish"),
    "microstrategy buys":  ("positivo", 3, "MSTR compra BTC: validacion institucional"),
    "strategy buys":       ("positivo", 3, "Strategy compra BTC: validacion institucional"),
    "saylor buys":         ("positivo", 2, "Saylor sumando BTC: senal bullish"),
    "tesla bitcoin":       ("positivo", 2, "Tesla en BTC: validacion corporativa"),
    "country adopts":      ("positivo", 3, "Pais adopta BTC: validacion soberana"),
    "el salvador":         ("positivo", 1, "El Salvador en BTC: contexto adopcion"),
    "adoption":            ("positivo", 1, "Adopcion mencionada"),
    "adopcion":            ("positivo", 1, "Adopcion mencionada"),
    "legal tender":        ("positivo", 3, "BTC como tender legal: muy positivo"),

    # --- Politica / regulacion favorable ---
    "trump crypto":        ("positivo", 3, "Trump pro-crypto: positivo para BTC"),
    "pro-crypto":          ("positivo", 2, "Postura pro-crypto: positivo para BTC"),
    "pro crypto":          ("positivo", 2, "Postura pro-crypto: positivo para BTC"),
    "strategic reserve":   ("positivo", 3, "Reserva estrategica de BTC: muy positivo"),
    "bitcoin reserve":     ("positivo", 3, "Reserva estrategica BTC: validacion soberana"),
    "regulatory clarity":  ("positivo", 2, "Claridad regulatoria: positivo para BTC"),
    "crypto-friendly":     ("positivo", 2, "Marco regulatorio amigable: positivo"),

    # --- Halving / On-chain ---
    "halving":             ("positivo", 2, "Halving: factor estructural bullish"),
    "post-halving":        ("positivo", 2, "Periodo post-halving: historicamente bullish"),
    "whale accumulation":  ("positivo", 3, "Whales acumulando: senal on-chain muy bullish"),
    "whales accumulate":   ("positivo", 3, "Whales acumulando: senal on-chain muy bullish"),
    "whale buying":        ("positivo", 2, "Whales comprando: senal on-chain bullish"),
    "supply shock":        ("positivo", 3, "Shock de oferta: muy bullish estructural"),
    "long-term holders":   ("positivo", 2, "LTH acumulando: senal de fondo"),
    "hodlers":             ("positivo", 1, "Hodlers en accion: senal bullish suave"),
    "extreme fear":        ("positivo", 2, "Extreme fear: contrarian, suelo historico"),

    # --- BTC sube directo ---
    "bitcoin rises":       ("positivo", 2, "BTC al alza"),
    "bitcoin surges":      ("positivo", 3, "BTC salta con fuerza"),
    "bitcoin rallies":     ("positivo", 2, "Rally de BTC"),
    "bitcoin jumps":       ("positivo", 2, "BTC al alza"),
    "bitcoin climbs":      ("positivo", 2, "BTC al alza"),
    "bitcoin gains":       ("positivo", 2, "BTC gana terreno"),
    "btc rises":           ("positivo", 2, "BTC al alza"),
    "btc surges":          ("positivo", 3, "BTC salta con fuerza"),
    "bitcoin record":      ("positivo", 3, "BTC en records historicos"),
    "all-time high":       ("positivo", 3, "ATH: muy positivo (cuidado: tope)"),
    "all time high":       ("positivo", 3, "ATH: muy positivo (cuidado: tope)"),
    "aumento su valor":    ("positivo", 1, "BTC aumenta valor"),
    "criptomoneda aumento":("positivo", 1, "Criptomoneda al alza"),
    "predicciones alcistas":("positivo", 2, "Predicciones bullish"),
    "predicciones bajistas":("negativo", 2, "Predicciones bearish"),
    "se desploma":         ("negativo", 3, "Desplome: muy negativo"),
    "desploma":            ("negativo", 3, "Desplome: muy negativo"),
    "cae fuerte":          ("negativo", 2, "Caida fuerte"),
    "miedo extremo":       ("positivo", 2, "Miedo extremo: contrarian, suelo historico"),
    "outflows":            ("negativo", 3, "Salidas de fondos: muy negativo"),
    "etf outflows":        ("negativo", 3, "Salidas de ETF: muy negativo institucional"),
    "btc etf outflows":    ("negativo", 3, "Salidas de ETF BTC: muy negativo"),
    "pierden terreno":     ("negativo", 2, "Pierden terreno: presion negativa"),
    "convirtiendo etfs":   ("positivo", 2, "BlackRock/Fidelity con ETFs activos"),

    # === NEGATIVO PARA BTC ===
    # --- Fed hawkish ---
    "rate hike":           ("negativo", 3, "Fed hawkish: rate hike presiona BTC"),
    "rate hikes":          ("negativo", 3, "Fed hawkish: rate hikes presionan BTC"),
    "higher rates":        ("negativo", 2, "Tasas mas altas: presion para BTC"),
    "hawkish":             ("negativo", 2, "Tono hawkish: presion para BTC"),
    "sube tasas":          ("negativo", 3, "Fed hawkish: presion para BTC"),
    "stronger dollar":     ("negativo", 2, "Dolar fortalecido: presion para BTC"),
    "strong dollar":       ("negativo", 2, "Dolar fuerte: presion para BTC"),
    "dollar rallies":      ("negativo", 2, "Dolar rallea: presion para BTC"),
    "dollar surges":       ("negativo", 2, "Dolar salta: presion para BTC"),

    # --- Regulacion / accion legal ---
    "sec sues":            ("negativo", 3, "SEC demanda: muy negativo"),
    "sec lawsuit":         ("negativo", 3, "SEC demanda: muy negativo"),
    "sec charges":         ("negativo", 3, "SEC acusa: muy negativo"),
    "sec investigates":    ("negativo", 2, "SEC investiga: presion regulatoria"),
    "lawsuit":             ("negativo", 2, "Accion legal: presion negativa"),
    "ban":                 ("negativo", 3, "Prohibicion: muy negativo"),
    "banned":              ("negativo", 3, "Prohibido: muy negativo"),
    "prohibe":             ("negativo", 3, "Prohibicion: muy negativo"),
    "china ban":           ("negativo", 3, "China ban a cripto: muy negativo"),
    "crackdown":           ("negativo", 3, "Crackdown regulatorio: muy negativo"),
    "restriction":         ("negativo", 2, "Restriccion regulatoria: negativo"),
    "fraud":               ("negativo", 2, "Fraude: presion regulatoria/confianza"),
    "fraude":              ("negativo", 2, "Fraude: presion regulatoria/confianza"),

    # --- Hacks / explotaciones ---
    "exchange hack":       ("negativo", 3, "Hack de exchange: muy negativo confianza"),
    "hack":                ("negativo", 2, "Hack: golpe a confianza del ecosistema"),
    "stolen":              ("negativo", 2, "Robo: golpe a confianza"),
    "exploit":             ("negativo", 2, "Explotacion: vulnerabilidad expuesta"),
    "rug pull":            ("negativo", 2, "Rug pull: golpe a sentimiento"),

    # --- ETF outflows / institucional negativo ---
    "etf outflows":        ("negativo", 3, "Salidas de ETF: muy negativo institucional"),
    "outflows":            ("negativo", 2, "Salidas institucionales"),
    "ibit outflows":       ("negativo", 3, "IBIT pierde flujos: institucional sale"),
    "institutional selling":("negativo", 2, "Venta institucional: presion"),

    # --- Whales vendiendo / liquidaciones ---
    "whale selling":       ("negativo", 3, "Whales vendiendo: senal on-chain bearish"),
    "whales sell":         ("negativo", 3, "Whales vendiendo: senal bearish"),
    "whale dumping":       ("negativo", 3, "Whales soltando: muy bearish"),
    "mt gox":              ("negativo", 2, "Mt Gox venta: presion de oferta"),
    "genesis selling":     ("negativo", 2, "Genesis vendiendo: presion"),
    "liquidations":        ("negativo", 2, "Liquidaciones masivas: cascada bajista"),
    "liquidaciones":       ("negativo", 2, "Liquidaciones masivas: cascada bajista"),
    "selloff":             ("negativo", 2, "Selloff: presion vendedora"),
    "sell-off":            ("negativo", 2, "Sell-off: presion vendedora"),

    # --- Crisis cripto especifica ---
    "stablecoin depeg":    ("negativo", 3, "Depeg de stablecoin: contagio sistemico"),
    "ftx":                 ("negativo", 1, "Referencia FTX: contexto negativo"),
    "luna":                ("negativo", 1, "Referencia LUNA: contexto negativo"),

    # --- BTC baja directo ---
    "bitcoin falls":       ("negativo", 2, "BTC a la baja"),
    "bitcoin drops":       ("negativo", 2, "BTC cae"),
    "bitcoin tumbles":     ("negativo", 3, "BTC cae fuerte"),
    "bitcoin crashes":     ("negativo", 3, "BTC se desploma"),
    "bitcoin plunges":     ("negativo", 3, "BTC plunge"),
    "bitcoin slips":       ("negativo", 1, "BTC baja levemente"),
    "btc falls":           ("negativo", 2, "BTC a la baja"),
    "btc crashes":         ("negativo", 3, "BTC se desploma"),
    "crypto crash":        ("negativo", 3, "Crash cripto: muy negativo"),
    "bear market":         ("negativo", 2, "Bear market cripto"),

    # --- Sentimiento extremo bullish (contrarian negativo) ---
    "extreme greed":       ("negativo", 1, "Extreme greed: contrarian, posible tope"),
    "fomo":                ("negativo", 1, "FOMO mencionado: senal de tope cercano"),
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
        razonamiento = max(matches_pos, key=lambda x: x[0])[1] if matches_pos else "Sesgo positivo para BTC"
    else:
        direccion = "negativo"
        intensidad = min(3, max(1, abs(diff) // 2 + 1))
        razonamiento = max(matches_neg, key=lambda x: x[0])[1] if matches_neg else "Sesgo negativo para BTC"

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
    code, out, _ = ejecutar_git(["status", "--porcelain", "noticias_btc.json"])
    if not out.strip():
        log.info("Sin cambios en noticias_btc.json")
        return
    ejecutar_git(["pull", "--rebase", "--autostash"])
    ejecutar_git(["add", "noticias_btc.json"])
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    ejecutar_git(["commit", "-m", f"chore: update noticias BTC {ts} [skip ci]"])
    code, _, err = ejecutar_git(["push"])
    if code != 0:
        ejecutar_git(["pull", "--rebase", "--autostash"])
        ejecutar_git(["push"])
    log.info("git push OK")


def main():
    log.info("=" * 60)
    log.info("=== Update Noticias BTC ===")
    cutoff = datetime.now(timezone.utc) - timedelta(days=VENTANA_DIAS)
    log.info(f"Cutoff: {cutoff.isoformat()}")
    noticias_por_categoria = {}
    total = 0
    for categoria, keywords in QUERIES.items():
        noticias = fetch_categoria(categoria, keywords, cutoff)
        noticias_por_categoria[categoria] = noticias
        total += len(noticias)
    resumen_ejecutivo = generar_resumen_ejecutivo(noticias_por_categoria)
    output = {
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "ventana_dias": VENTANA_DIAS,
        "categorias_meta": CATEGORIA_META,
        "noticias_por_categoria": noticias_por_categoria,
        "stats": {
            "total_noticias": total,
            "por_categoria": {k: len(v) for k, v in noticias_por_categoria.items()},
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
