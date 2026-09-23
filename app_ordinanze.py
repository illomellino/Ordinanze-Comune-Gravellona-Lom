#!/usr/bin/env python3
"""
Ricerca in TUTTE le categorie di Atti e Pubblicazioni
del Comune di Gravellona Lomellina.

Novità rispetto alla versione precedente:
- le sezioni (Ordinanze, Delibere di Giunta, Delibere di Consiglio,
  Determine, Regolamenti, Bandi, Statuto, ecc.) NON sono più
  hardcodate: vengono scoperte automaticamente leggendo la pagina
  indice "Atti e pubblicazioni". Se il Comune aggiunge/rinomina una
  sezione, lo script continua a funzionare senza modifiche.
- lista_ordinanze() e lista_giunta() (duplicate al 90%) sono state
  unificate in un'unica funzione generica lista_atti().
- ricerca in parallelo dei PDF (ThreadPoolExecutor) invece che uno
  alla volta: sui casi con molti atti è nettamente più veloce.
- sessione HTTP con retry/backoff automatico sugli errori di rete.
- filtri: multiselezione categorie + intervallo di anni (da/a)
  invece del solo "numero di anni recenti".
- piccola guardia anti-crescita infinita della cache locale.
"""

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin

import requests
import streamlit as st
from bs4 import BeautifulSoup
from pypdf import PdfReader
from requests.adapters import HTTPAdapter, Retry


# ============================================================
# CONFIGURAZIONE
# ============================================================

BASE = "https://www.comune.gravellonalomellina.pv.it"
INDICE_ATTI = f"{BASE}/it-it/amministrazione/atti-pubblicazioni"

CACHE_DIR = Path("atti_cache")
CACHE_DIR.mkdir(exist_ok=True)
CACHE_MAX_FILES = 4000  # sopra questa soglia, ripulisce i file più vecchi

MAX_WORKERS = 4  # download PDF in parallelo (non esagerare: è comunque
                  # il sito di un piccolo Comune, meglio essere garbati)


# ============================================================
# SESSIONE HTTP con retry/backoff
# ============================================================

def crea_sessione() -> requests.Session:

    s = requests.Session()

    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/140.0 Safari/537.36 GravellonaAttiSearch/4.0"
        )
    })

    retries = Retry(
        total=3,
        backoff_factor=0.6,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )

    adapter = HTTPAdapter(max_retries=retries, pool_maxsize=MAX_WORKERS + 2)
    s.mount("https://", adapter)
    s.mount("http://", adapter)

    return s


SESSION = crea_sessione()


def get_html(url: str) -> str:
    r = SESSION.get(url, timeout=30)
    r.raise_for_status()
    return r.text


# ============================================================
# OCR (opzionale)
# ============================================================

OCR_OK = True

try:
    from pdf2image import convert_from_bytes
    import pytesseract

    # Se Tesseract non viene trovato automaticamente, decommenta e
    # imposta il percorso corretto:
    # pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

except Exception:
    OCR_OK = False


# ============================================================
# SCOPERTA AUTOMATICA DELLE SEZIONI
# ============================================================

def scopri_sezioni() -> dict[str, dict]:
    """
    Legge la pagina indice "Atti e pubblicazioni" e trova tutte le
    sottosezioni (Ordinanze, Delibere di Giunta, ecc.), con il loro
    slug URL. Ritorna {nome_visualizzato: {"url": ..., "slug": ...}}.
    """

    soup = BeautifulSoup(get_html(INDICE_ATTI), "html.parser")

    sezioni = {}

    for a in soup.find_all("a", href=True):

        href = a["href"]

        m = re.search(r"/atti-pubblicazioni/([a-z0-9\-]+)/?(?:$|\?|#)", href)

        if not m:
            continue

        slug = m.group(1)

        nome = a.get_text(" ", strip=True)

        if len(nome) < 3:
            continue

        full_url = urljoin(BASE, href)

        # Evita doppioni (stesso slug con testo leggermente diverso)
        sezioni[nome] = {"url": full_url, "slug": slug}

    return sezioni


# Fallback statico, usato solo se la scoperta automatica fallisce
# (es. il sito è irraggiungibile al momento dell'avvio).
SEZIONI_FALLBACK = {
    "Albo pretorio":            {"url": f"{INDICE_ATTI}/albo-pretorio", "slug": "albo-pretorio"},
    "Delibere di giunta":       {"url": f"{INDICE_ATTI}/delibere-di-giunta", "slug": "delibere-di-giunta"},
    "Delibere di consiglio":    {"url": f"{INDICE_ATTI}/delibere-di-consiglio", "slug": "delibere-di-consiglio"},
    "Determine":                {"url": f"{INDICE_ATTI}/determine", "slug": "determine"},
    "Ordinanze":                {"url": f"{INDICE_ATTI}/ordinanze", "slug": "ordinanze"},
    "Regolamenti":              {"url": f"{INDICE_ATTI}/regolamenti", "slug": "regolamenti"},
    "Bandi e avvisi di gara":   {"url": f"{INDICE_ATTI}/bandi-e-avvisi-di-gara", "slug": "bandi-e-avvisi-di-gara"},
    "Procedure senza bando":    {"url": f"{INDICE_ATTI}/procedure-senza-bando", "slug": "procedure-senza-bando"},
    "Bandi di concorso":        {"url": f"{INDICE_ATTI}/bandi-di-concorso", "slug": "bandi-di-concorso"},
    "Altri atti":                {"url": f"{INDICE_ATTI}/altri-atti", "slug": "altri-atti"},
    "Statuto":                  {"url": f"{INDICE_ATTI}/statuto", "slug": "statuto"},
}


# ============================================================
# ANNI DISPONIBILI (generica, sostituisce anni_ordinanze/anni_giunta)
# ============================================================

def anni_sezione(url_sezione: str, slug: str) -> list[str]:

    soup = BeautifulSoup(get_html(url_sezione), "html.parser")

    anni = set()

    for a in soup.find_all("a", href=True):

        m = re.search(rf"/{re.escape(slug)}/(\d{{4}})(?:/|#|$)", a["href"])

        if m:
            anni.add(m.group(1))

    # Alcune sezioni (es. Statuto, Regolamenti) potrebbero non essere
    # organizzate per anno: in quel caso la lista sarà vuota e verrà
    # gestita a parte da lista_atti_senza_anno().
    return sorted(anni, reverse=True)


# ============================================================
# LISTA ATTI PER ANNO (generica, sostituisce lista_ordinanze/lista_giunta)
# ============================================================

def lista_atti(url_sezione: str, slug: str, anno: str, tipo_label: str) -> list[dict]:

    url = f"{url_sezione}/{anno}"

    soup = BeautifulSoup(get_html(url), "html.parser")

    items = []
    seen = set()

    for a in soup.find_all("a", href=True):

        href = a["href"]

        if f"/{slug}/{anno}/" not in href:
            continue

        full_url = urljoin(BASE, href)

        if full_url in seen:
            continue

        seen.add(full_url)

        row = a.find_parent("tr")

        numero, data = "", ""
        titolo = a.get_text(" ", strip=True)

        if row:

            tds = [td.get_text(" ", strip=True) for td in row.find_all("td")]

            if len(tds) >= 3:
                numero, data, titolo = tds[0], tds[1], tds[2]
            elif len(tds) >= 2:
                numero, data = tds[0], tds[1]

        if len(titolo) < 5:
            continue

        items.append({
            "tipo": tipo_label,
            "anno": anno,
            "numero": numero,
            "data": data,
            "titolo": titolo,
            "url": full_url,
        })

    return items


def lista_atti_senza_anno(url_sezione: str, tipo_label: str) -> list[dict]:
    """Per sezioni non organizzate per anno (es. Statuto, Regolamenti)."""

    soup = BeautifulSoup(get_html(url_sezione), "html.parser")

    items = []
    seen = set()

    for a in soup.find_all("a", href=True):

        href = a["href"]

        if "/download/" not in href and ".pdf" not in href.lower():
            continue

        full_url = urljoin(BASE, href)

        if full_url in seen:
            continue

        seen.add(full_url)

        titolo = a.get_text(" ", strip=True)

        if len(titolo) < 5:
            continue

        items.append({
            "tipo": tipo_label,
            "anno": "",
            "numero": "",
            "data": "",
            "titolo": titolo,
            "url": full_url,
        })

    return items


# ============================================================
# TROVA PDF NELLA SCHEDA
# ============================================================

def trova_pdf(url_scheda: str) -> str | None:

    soup = BeautifulSoup(get_html(url_scheda), "html.parser")

    for a in soup.find_all("a", href=True):

        href = a["href"]
        text = a.get_text(" ", strip=True).lower()

        if "/download/" in href and (".pdf" in href.lower() or "pdf" in text):
            return urljoin(BASE, href)

    for a in soup.find_all("a", href=True):

        if ".pdf" in a["href"].lower():
            return urljoin(BASE, a["href"])

    return None


# ============================================================
# ESTRAZIONE TESTO PDF (con fallback OCR)
# ============================================================

def estrai_testo_pdf(content: bytes) -> str:

    testo = ""

    try:
        reader = PdfReader(BytesIO(content))
        parti = []
        for page in reader.pages:
            try:
                pagina = page.extract_text()
                if pagina:
                    parti.append(pagina)
            except Exception:
                pass
        testo = "\n".join(parti)
    except Exception:
        testo = ""

    if len(testo.strip()) < 80 and OCR_OK:

        try:
            images = convert_from_bytes(content, dpi=200)
            ocr_parts = []
            for img in images:
                ocr_text = pytesseract.image_to_string(img, lang="ita+eng")
                if ocr_text:
                    ocr_parts.append(ocr_text)
            testo = "\n".join(ocr_parts)
        except Exception as e:
            if not testo:
                testo = f"[OCR error: {e}]"

    return testo


def _pulisci_cache_se_troppo_grande():

    file_cache = list(CACHE_DIR.glob("*.txt"))

    if len(file_cache) <= CACHE_MAX_FILES:
        return

    file_cache.sort(key=lambda p: p.stat().st_mtime)

    for p in file_cache[: len(file_cache) - CACHE_MAX_FILES]:
        p.unlink(missing_ok=True)


def testo_documento(pdf_url: str, cache_key: str) -> str:

    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", cache_key)[:150]
    cache_file = CACHE_DIR / f"{safe}.txt"

    if cache_file.exists():
        return cache_file.read_text(encoding="utf-8", errors="ignore")

    r = SESSION.get(pdf_url, timeout=90)
    r.raise_for_status()

    testo = estrai_testo_pdf(r.content)

    _pulisci_cache_se_troppo_grande()
    cache_file.write_text(testo, encoding="utf-8")

    return testo


# ============================================================
# SNIPPET
# ============================================================

def snippet(testo: str, termine: str, raggio: int = 90) -> str:

    testo_lower = testo.lower()
    pos = testo_lower.find(termine.lower())

    if pos < 0:
        return ""

    a = max(0, pos - raggio)
    b = min(len(testo), pos + len(termine) + raggio)

    return " ".join(testo[a:b].split())


# ============================================================
# ANALISI DI UN SINGOLO ATTO (usata dal pool di thread)
# ============================================================

def analizza_atto(item: dict, termine_l: str, solo_titoli: bool) -> dict | None:

    titolo = item.get("titolo", "")
    hit_titolo = termine_l in titolo.lower()

    if solo_titoli:
        if hit_titolo:
            return {**item, "dove": "titolo/oggetto", "snippet": titolo}
        return None

    try:
        pdf = trova_pdf(item["url"])

        if not pdf:
            if hit_titolo:
                return {**item, "dove": "titolo/oggetto (no PDF)", "snippet": titolo}
            return None

        key = f"{item['tipo']}_{item['anno']}_{item['numero']}_{titolo[:50]}"
        testo = testo_documento(pdf, key)
        hit_pdf = termine_l in testo.lower()

        if not (hit_pdf or hit_titolo):
            return None

        if hit_pdf and hit_titolo:
            dove = "PDF + titolo/oggetto"
        elif hit_pdf:
            dove = "PDF"
        else:
            dove = "titolo/oggetto"

        return {
            **item,
            "dove": dove,
            "snippet": snippet(testo, termine_l) or titolo,
            "pdf": pdf,
        }

    except Exception as e:
        return {**item, "errore": str(e)}


# ============================================================
# RICERCA PRINCIPALE
# ============================================================

def cerca(
    termine: str,
    categorie: dict[str, dict],  # {nome: {"url":..., "slug":...}}
    anno_da: int,
    anno_a: int,
    solo_titoli: bool = False,
    progress=None,
):

    risultati = []
    termine_l = termine.lower()

    # --------------------------------------------------------
    # 1. RECUPERA LA LISTA DI TUTTI GLI ATTI DA ANALIZZARE
    # --------------------------------------------------------

    da_analizzare = []

    for nome, info in categorie.items():

        try:
            anni_disp = anni_sezione(info["url"], info["slug"])
        except Exception as e:
            risultati.append({"errore": f"{nome}: impossibile leggere gli anni ({e})"})
            continue

        if anni_disp:

            anni_filtrati = [a for a in anni_disp if anno_da <= int(a) <= anno_a]

            for anno in anni_filtrati:
                try:
                    da_analizzare.extend(lista_atti(info["url"], info["slug"], anno, nome))
                except Exception as e:
                    risultati.append({"errore": f"{nome} {anno}: {e}"})

        else:

            # Sezione non organizzata per anno (Statuto, Regolamenti...)
            try:
                da_analizzare.extend(lista_atti_senza_anno(info["url"], nome))
            except Exception as e:
                risultati.append({"errore": f"{nome}: {e}"})

    totale = len(da_analizzare)

    # --------------------------------------------------------
    # 2. ANALIZZA GLI ATTI (in parallelo se serve aprire i PDF)
    # --------------------------------------------------------

    if solo_titoli:

        # Nessun download di PDF: nessun bisogno di parallelismo/pause.
        for n, item in enumerate(da_analizzare, start=1):
            if progress:
                progress(n, totale, item)
            hit = analizza_atto(item, termine_l, solo_titoli=True)
            if hit:
                risultati.append(hit)

    else:

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:

            futures = {
                pool.submit(analizza_atto, item, termine_l, False): item
                for item in da_analizzare
            }

            n = 0
            for future in as_completed(futures):
                n += 1
                if progress:
                    progress(n, totale, futures[future])
                hit = future.result()
                if hit:
                    risultati.append(hit)

    return risultati, totale


# ============================================================
# STREAMLIT
# ============================================================

st.set_page_config(page_title="Atti Gravellona Lomellina", page_icon="📚", layout="centered")

st.title("📚 Ricerca Atti")
st.caption("Comune di Gravellona Lomellina")
st.markdown(
    "Cerca in **tutte le categorie** di Atti e Pubblicazioni "
    "(Ordinanze, Delibere, Determine, Bandi, Regolamenti, Statuto...), "
    "anche all'interno dei PDF."
)

if not OCR_OK:
    st.warning(
        "⚠️ OCR non disponibile. I PDF che contengono solo immagini "
        "non potranno essere ricercati."
    )

# --------------------------------------------------------
# CARICAMENTO SEZIONI (con cache di sessione, per non ricaricare
# la pagina indice a ogni interazione)
# --------------------------------------------------------

if "sezioni" not in st.session_state:

    with st.spinner("Carico l'elenco delle categorie disponibili..."):

        try:
            sez = scopri_sezioni()
            st.session_state["sezioni"] = sez if sez else SEZIONI_FALLBACK
        except Exception:
            st.session_state["sezioni"] = SEZIONI_FALLBACK

sezioni_disponibili = st.session_state["sezioni"]

# ============================================================
# INPUT
# ============================================================

termine = st.text_input(
    "🔎 Termine da cercare",
    placeholder="es. circolazione, scuola, videosorveglianza, contributo...",
)

categorie_scelte = st.multiselect(
    "📂 Categorie",
    options=list(sezioni_disponibili.keys()),
    default=list(sezioni_disponibili.keys()),
)

col1, col2, col3 = st.columns(3)

anno_corrente = time.localtime().tm_year

with col1:
    anno_da = st.number_input("📅 Dal", min_value=1990, max_value=anno_corrente, value=anno_corrente - 2, step=1)

with col2:
    anno_a = st.number_input("📅 Al", min_value=1990, max_value=anno_corrente, value=anno_corrente, step=1)

with col3:
    solo_titoli = st.checkbox("⚡ Solo titoli/oggetti", value=False)

if solo_titoli:
    st.info("La ricerca nei soli titoli è molto più veloce perché non scarica i PDF.")

cerca_button = st.button("🔍 CERCA", type="primary", use_container_width=True)

# ============================================================
# ESECUZIONE
# ============================================================

if cerca_button:

    if not termine.strip():
        st.warning("Inserisci una parola o una frase da cercare.")
        st.stop()

    if not categorie_scelte:
        st.warning("Seleziona almeno una categoria.")
        st.stop()

    if anno_da > anno_a:
        st.warning("L'anno di inizio deve essere minore o uguale all'anno di fine.")
        st.stop()

    categorie_filtrate = {k: v for k, v in sezioni_disponibili.items() if k in categorie_scelte}

    st.markdown(
        f"**Categorie:** {', '.join(categorie_scelte)}  \n"
        f"**Anni:** {anno_da}–{anno_a}  \n"
        f"**Termine:** `{termine.strip()}`"
    )

    st.divider()

    bar = st.progress(0.0)
    status = st.empty()

    def on_progress(n, totale, item):
        status.write(
            f"🔎 [{n}/{totale or '?'}] **{item.get('tipo', '')}** "
            f"n.{item.get('numero', '?')} — {item.get('titolo', '')[:70]}"
        )
        if totale:
            bar.progress(min(1.0, n / totale))
        else:
            bar.progress(min(0.95, n / 100))

    risultati, totale = cerca(
        termine.strip(),
        categorie_filtrate,
        int(anno_da),
        int(anno_a),
        solo_titoli=solo_titoli,
        progress=on_progress,
    )

    bar.progress(1.0)
    status.success(f"Ricerca completata. Documenti analizzati: {totale}")

    hits = [r for r in risultati if r.get("dove")]
    errori = [r for r in risultati if r.get("errore") and not r.get("dove")]

    st.subheader(f"📌 Risultati: {len(hits)}")

    if not hits:
        st.info("Nessuna occorrenza trovata.")

    for r in hits:

        st.markdown(f"### {r.get('tipo', 'Atto')}")

        intestazione = []
        if r.get("numero"):
            intestazione.append(f"**N. {r['numero']}**")
        if r.get("data"):
            intestazione.append(f"**{r['data']}**")
        if intestazione:
            st.markdown(" — ".join(intestazione))

        st.markdown(f"**{r.get('titolo', '')}**")

        dove = r.get("dove", "")
        if dove == "PDF":
            st.success("📄 Termine trovato nel PDF")
        elif dove == "PDF + titolo/oggetto":
            st.success("📄 Termine trovato nel PDF e nel titolo/oggetto")
        else:
            st.info(f"🔎 Trovato in: {dove}")

        if r.get("snippet"):
            st.markdown(f"> {r['snippet']}")

        col_a, col_b = st.columns(2)
        with col_a:
            st.link_button("🌐 Apri scheda", r.get("url", ""), use_container_width=True)
        with col_b:
            if r.get("pdf"):
                st.link_button("📄 Apri PDF", r["pdf"], use_container_width=True)

        st.divider()

    if errori:
        with st.expander(f"⚠️ Errori ({len(errori)})"):
            for errore in errori:
                st.write(errore)
