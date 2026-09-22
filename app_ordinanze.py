#!/usr/bin/env python3
"""
Ricerca nelle ordinanze del Comune di Gravellona Lomellina
- estrae testo dai PDF
- se il PDF è scansionato, usa OCR (Tesseract, italiano)
- interfaccia Streamlit usabile anche da telefono
"""

import re
import time
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin

import requests
import streamlit as st
from bs4 import BeautifulSoup
from pypdf import PdfReader

# --- OCR opzionale ---
OCR_OK = True
try:
    from pdf2image import convert_from_bytes
    import pytesseract
    # Windows: decommenta e adatta il percorso se serve
    # pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
except Exception:
    OCR_OK = False

BASE = "https://www.comune.gravellonalomellina.pv.it"
LIST_URL = f"{BASE}/it-it/amministrazione/atti-pubblicazioni/ordinanze"
CACHE_DIR = Path("ordinanze_cache")
CACHE_DIR.mkdir(exist_ok=True)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 (OrdinanzeSearch/2.0)"})


def get_html(url: str) -> str:
    r = SESSION.get(url, timeout=30)
    r.raise_for_status()
    return r.text


def anni_disponibili() -> list[str]:
    soup = BeautifulSoup(get_html(LIST_URL), "html.parser")
    anni = {
        m.group(1)
        for a in soup.find_all("a", href=True)
        if (m := re.search(r"/ordinanze/(\d{4})(?:/|#|$)", a["href"]))
    }
    return sorted(anni, reverse=True)


def lista_ordinanze(anno: str) -> list[dict]:
    url = f"{LIST_URL}/{anno}"
    soup = BeautifulSoup(get_html(url), "html.parser")
    items, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if f"/ordinanze/{anno}/" not in href or href.count("/") < 6:
            continue
        titolo = a.get_text(" ", strip=True)
        if len(titolo) < 8:
            continue
        full = urljoin(BASE, href)
        if full in seen:
            continue
        seen.add(full)
        row = a.find_parent("tr")
        numero = data = ""
        if row:
            tds = [td.get_text(strip=True) for td in row.find_all("td")]
            if len(tds) >= 2:
                numero, data = tds[0], tds[1]
        items.append({
            "anno": anno, "numero": numero, "data": data,
            "titolo": titolo, "url": full,
        })
    return items


def trova_pdf(url_scheda: str) -> str | None:
    soup = BeautifulSoup(get_html(url_scheda), "html.parser")
    for a in soup.find_all("a", href=True):
        href, text = a["href"], a.get_text(" ", strip=True).lower()
        if "/download/" in href and (".pdf" in href.lower() or "pdf" in text):
            return urljoin(BASE, href)
    return None


def estrai_testo_pdf(content: bytes) -> str:
    """Testo nativo dal PDF; se troppo poco, prova OCR."""
    testo = ""
    try:
        reader = PdfReader(BytesIO(content))
        parti = []
        for page in reader.pages:
            try:
                parti.append(page.extract_text() or "")
            except Exception:
                pass
        testo = "\n".join(parti)
    except Exception:
        testo = ""

    # se quasi vuoto → probabilmente scansionato
    if len(testo.strip()) < 80 and OCR_OK:
        try:
            images = convert_from_bytes(content, dpi=200)
            ocr_parts = []
            for img in images:
                ocr_parts.append(
                    pytesseract.image_to_string(img, lang="ita+eng")
                )
            testo = "\n".join(ocr_parts)
        except Exception as e:
            testo += f"\n[OCR error: {e}]"
    return testo


def testo_documento(pdf_url: str, cache_key: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", cache_key)[:120]
    cache_file = CACHE_DIR / f"{safe}.txt"
    if cache_file.exists():
        return cache_file.read_text(encoding="utf-8", errors="ignore")

    r = SESSION.get(pdf_url, timeout=90)
    r.raise_for_status()
    testo = estrai_testo_pdf(r.content)
    cache_file.write_text(testo, encoding="utf-8")
    return testo


def snippet(testo: str, termine: str, raggio: int = 70) -> str:
    pos = testo.lower().find(termine.lower())
    if pos < 0:
        return ""
    a, b = max(0, pos - raggio), min(len(testo), pos + len(termine) + raggio)
    return " ".join(testo[a:b].split())


def cerca(termine: str, anni: list[str], solo_titoli: bool = False, progress=None):
    risultati = []
    totale = 0
    termine_l = termine.lower()

    for anno in anni:
        try:
            lista = lista_ordinanze(anno)
        except Exception as e:
            risultati.append({"errore": f"Lista {anno}: {e}"})
            continue

        for item in lista:
            totale += 1
            if progress:
                progress(totale, item)

            hit_titolo = termine_l in item["titolo"].lower()
            if solo_titoli:
                if hit_titolo:
                    risultati.append({**item, "dove": "titolo", "snippet": item["titolo"]})
                continue

            try:
                pdf = trova_pdf(item["url"])
                if not pdf:
                    if hit_titolo:
                        risultati.append({**item, "dove": "titolo (no PDF)", "snippet": item["titolo"]})
                    continue

                key = f"{anno}_{item['numero']}_{item['titolo'][:40]}"
                testo = testo_documento(pdf, key)
                hit_pdf = termine_l in testo.lower()
                if hit_pdf or hit_titolo:
                    dove = "PDF+titolo" if hit_pdf and hit_titolo else ("PDF" if hit_pdf else "titolo")
                    risultati.append({
                        **item,
                        "dove": dove,
                        "snippet": snippet(testo, termine) or item["titolo"],
                        "pdf": pdf,
                    })
            except Exception as e:
                risultati.append({**item, "errore": str(e)})

            time.sleep(0.35)

    return risultati, totale


# -------------------- STREAMLIT UI --------------------
st.set_page_config(page_title="Ordinanze Gravellona", page_icon="📄", layout="centered")
st.title("📄 Cerca nelle Ordinanze")
st.caption("Comune di Gravellona Lomellina — PDF + OCR")

if not OCR_OK:
    st.warning("OCR non disponibile (manca Tesseract/Poppler). Funziona solo sui PDF con testo.")

termine = st.text_input("Termine da cercare", placeholder="es. circolazione, vetro, mensa...")
col1, col2 = st.columns(2)
with col1:
    solo_titoli = st.checkbox("Solo titoli (veloce)", value=False)
with col2:
    max_anni = st.number_input("Quanti anni recenti", min_value=1, max_value=20, value=3)

if st.button("Cerca", type="primary") and termine.strip():
    with st.spinner("Scarico elenco anni..."):
        try:
            anni = anni_disponibili()[: int(max_anni)]
        except Exception as e:
            st.error(f"Errore connessione sito: {e}")
            st.stop()

    st.write(f"Anni: **{', '.join(anni)}**")
    bar = st.progress(0.0)
    status = st.empty()

    def on_progress(n, item):
        status.write(f"Analizzo: {item.get('titolo', '')[:60]}...")
        # stima grezza
        bar.progress(min(0.95, n / 40))

    risultati, totale = cerca(termine.strip(), anni, solo_titoli, on_progress)
    bar.progress(1.0)
    status.write(f"Fatto. Documenti visti: {totale}")

    hits = [r for r in risultati if "errore" not in r or r.get("dove")]
    hits = [r for r in risultati if r.get("dove")]
    errori = [r for r in risultati if r.get("errore") and not r.get("dove")]

    st.subheader(f"Risultati: {len(hits)}")
    if not hits:
        st.info("Nessuna occorrenza trovata.")
    for r in hits:
        st.markdown(
            f"**n.{r.get('numero','?')}** — {r.get('data','?')}  \n"
            f"{r.get('titolo','')}  \n"
            f"Trovato in: `{r.get('dove')}`  \n"
            f"«{r.get('snippet','')}»  \n"
            f"[Scheda]({r.get('url')})"
            + (f" · [PDF]({r.get('pdf')})" if r.get("pdf") else "")
        )
        st.divider()

    if errori:
        with st.expander("Errori"):
            for e in errori:
                st.write(e)
