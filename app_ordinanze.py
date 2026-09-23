#!/usr/bin/env python3
"""
Ricerca nelle Ordinanze e nelle Delibere di Giunta
del Comune di Gravellona Lomellina.

Funzioni:
- ricerca nel titolo/oggetto
- ricerca nel testo dei PDF
- OCR per PDF scansionati
- cache locale dei documenti
- interfaccia Streamlit
- utilizzabile anche da smartphone
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


# ============================================================
# CONFIGURAZIONE
# ============================================================

BASE = "https://www.comune.gravellonalomellina.pv.it"

URL_ORDINANZE = (
    f"{BASE}/it-it/amministrazione/atti-pubblicazioni/ordinanze"
)

URL_GIUNTA = (
    f"{BASE}/it-it/amministrazione/atti-pubblicazioni/delibere-di-giunta"
)

CACHE_DIR = Path("atti_cache")
CACHE_DIR.mkdir(exist_ok=True)


# ============================================================
# SESSIONE HTTP
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/140.0 Safari/537.36 "
        "GravellonaAttiSearch/3.0"
    )
})


# ============================================================
# OCR
# ============================================================

OCR_OK = True

try:
    from pdf2image import convert_from_bytes
    import pytesseract

    # --------------------------------------------------------
    # SE TESSERACT NON VIENE TROVATO AUTOMATICAMENTE,
    # DECOMMENTA QUESTA RIGA E MODIFICA IL PERCORSO:
    #
    # pytesseract.pytesseract.tesseract_cmd = (
    #     r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    # )
    # --------------------------------------------------------

except Exception:
    OCR_OK = False


# ============================================================
# FUNZIONI HTTP
# ============================================================

def get_html(url: str) -> str:
    """Scarica una pagina HTML."""

    r = SESSION.get(
        url,
        timeout=30
    )

    r.raise_for_status()

    return r.text


# ============================================================
# ANNI ORDINANZE
# ============================================================

def anni_ordinanze() -> list[str]:
    """Trova gli anni disponibili per le ordinanze."""

    soup = BeautifulSoup(
        get_html(URL_ORDINANZE),
        "html.parser"
    )

    anni = set()

    for a in soup.find_all("a", href=True):

        href = a["href"]

        m = re.search(
            r"/ordinanze/(\d{4})(?:/|#|$)",
            href
        )

        if m:
            anni.add(m.group(1))

    return sorted(
        anni,
        reverse=True
    )


# ============================================================
# ANNI DELIBERE GIUNTA
# ============================================================

def anni_giunta() -> list[str]:
    """Trova gli anni disponibili per le delibere di giunta."""

    soup = BeautifulSoup(
        get_html(URL_GIUNTA),
        "html.parser"
    )

    anni = set()

    for a in soup.find_all("a", href=True):

        href = a["href"]

        m = re.search(
            r"/delibere-di-giunta/(\d{4})(?:/|#|$)",
            href
        )

        if m:
            anni.add(m.group(1))

    return sorted(
        anni,
        reverse=True
    )


# ============================================================
# LISTA ORDINANZE
# ============================================================

def lista_ordinanze(anno: str) -> list[dict]:
    """Recupera le ordinanze di un anno."""

    url = f"{URL_ORDINANZE}/{anno}"

    soup = BeautifulSoup(
        get_html(url),
        "html.parser"
    )

    items = []
    seen = set()

    for a in soup.find_all("a", href=True):

        href = a["href"]

        if f"/ordinanze/{anno}/" not in href:
            continue

        titolo = a.get_text(
            " ",
            strip=True
        )

        if len(titolo) < 5:
            continue

        full_url = urljoin(
            BASE,
            href
        )

        if full_url in seen:
            continue

        seen.add(full_url)

        numero = ""
        data = ""

        row = a.find_parent("tr")

        if row:

            tds = [
                td.get_text(
                    " ",
                    strip=True
                )
                for td in row.find_all("td")
            ]

            if len(tds) >= 2:

                numero = tds[0]
                data = tds[1]

        items.append({
            "tipo": "Ordinanza",
            "anno": anno,
            "numero": numero,
            "data": data,
            "titolo": titolo,
            "url": full_url,
        })

    return items


# ============================================================
# LISTA DELIBERE GIUNTA
# ============================================================

def lista_giunta(anno: str) -> list[dict]:
    """
    Recupera le Delibere di Giunta di un determinato anno.

    La struttura del sito mostra:
    Numero | Data | Oggetto
    """

    url = f"{URL_GIUNTA}/{anno}"

    soup = BeautifulSoup(
        get_html(url),
        "html.parser"
    )

    items = []
    seen = set()

    for a in soup.find_all("a", href=True):

        href = a["href"]

        if f"/delibere-di-giunta/{anno}/" not in href:
            continue

        full_url = urljoin(
            BASE,
            href
        )

        if full_url in seen:
            continue

        seen.add(full_url)

        row = a.find_parent("tr")

        numero = ""
        data = ""
        titolo = a.get_text(
            " ",
            strip=True
        )

        if row:

            tds = [
                td.get_text(
                    " ",
                    strip=True
                )
                for td in row.find_all("td")
            ]

            if len(tds) >= 3:

                numero = tds[0]
                data = tds[1]
                titolo = tds[2]

            elif len(tds) >= 2:

                numero = tds[0]
                data = tds[1]

        if len(titolo) < 5:
            continue

        items.append({
            "tipo": "Delibera di Giunta",
            "anno": anno,
            "numero": numero,
            "data": data,
            "titolo": titolo,
            "url": full_url,
        })

    return items


# ============================================================
# TROVA PDF
# ============================================================

def trova_pdf(url_scheda: str) -> str | None:
    """
    Cerca il PDF nella pagina della singola scheda.

    Funziona sia con Ordinanze che Delibere.
    """

    soup = BeautifulSoup(
        get_html(url_scheda),
        "html.parser"
    )

    for a in soup.find_all("a", href=True):

        href = a["href"]

        text = a.get_text(
            " ",
            strip=True
        ).lower()

        if (
            "/download/" in href
            and (
                ".pdf" in href.lower()
                or "pdf" in text
            )
        ):

            return urljoin(
                BASE,
                href
            )

    # Secondo tentativo:
    # cerca qualsiasi link PDF

    for a in soup.find_all("a", href=True):

        href = a["href"]

        if ".pdf" in href.lower():

            return urljoin(
                BASE,
                href
            )

    return None


# ============================================================
# ESTRAZIONE TESTO PDF
# ============================================================

def estrai_testo_pdf(content: bytes) -> str:
    """
    Estrae testo nativo dal PDF.

    Se il testo è quasi assente, prova OCR.
    """

    testo = ""

    # --------------------------------------------------------
    # TENTATIVO 1: PDF CON TESTO
    # --------------------------------------------------------

    try:

        reader = PdfReader(
            BytesIO(content)
        )

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

    # --------------------------------------------------------
    # TENTATIVO 2: OCR
    # --------------------------------------------------------

    if len(testo.strip()) < 80 and OCR_OK:

        try:

            images = convert_from_bytes(
                content,
                dpi=200
            )

            ocr_parts = []

            for img in images:

                ocr_text = pytesseract.image_to_string(
                    img,
                    lang="ita+eng"
                )

                if ocr_text:
                    ocr_parts.append(
                        ocr_text
                    )

            testo = "\n".join(
                ocr_parts
            )

        except Exception as e:

            # Non bloccare la ricerca
            # se OCR non funziona.

            if not testo:

                testo = (
                    f"[OCR error: {e}]"
                )

    return testo


# ============================================================
# TESTO DOCUMENTO + CACHE
# ============================================================

def testo_documento(
    pdf_url: str,
    cache_key: str
) -> str:
    """Scarica il PDF e ne estrae il testo."""

    safe = re.sub(
        r"[^a-zA-Z0-9_-]+",
        "_",
        cache_key
    )[:150]

    cache_file = CACHE_DIR / (
        f"{safe}.txt"
    )

    # --------------------------------------------------------
    # CACHE
    # --------------------------------------------------------

    if cache_file.exists():

        return cache_file.read_text(
            encoding="utf-8",
            errors="ignore"
        )

    # --------------------------------------------------------
    # DOWNLOAD
    # --------------------------------------------------------

    r = SESSION.get(
        pdf_url,
        timeout=90
    )

    r.raise_for_status()

    # --------------------------------------------------------
    # ESTRAZIONE
    # --------------------------------------------------------

    testo = estrai_testo_pdf(
        r.content
    )

    cache_file.write_text(
        testo,
        encoding="utf-8"
    )

    return testo


# ============================================================
# SNIPPET
# ============================================================

def snippet(
    testo: str,
    termine: str,
    raggio: int = 90
) -> str:

    testo_lower = testo.lower()
    termine_lower = termine.lower()

    pos = testo_lower.find(
        termine_lower
    )

    if pos < 0:
        return ""

    a = max(
        0,
        pos - raggio
    )

    b = min(
        len(testo),
        pos + len(termine) + raggio
    )

    return " ".join(
        testo[a:b].split()
    )


# ============================================================
# RICERCA
# ============================================================

def cerca(
    termine: str,
    anni: list[str],
    tipo: str = "Entrambi",
    solo_titoli: bool = False,
    progress=None
):

    risultati = []

    totale = 0

    termine_l = termine.lower()

    # --------------------------------------------------------
    # QUALI ATTI CERCARE
    # --------------------------------------------------------

    cerca_ordinanze = tipo in (
        "Entrambi",
        "Ordinanze"
    )

    cerca_giunta = tipo in (
        "Entrambi",
        "Delibere di Giunta"
    )

    # ========================================================
    # ANNI
    # ========================================================

    for anno in anni:

        liste = []

        # ----------------------------------------------------
        # ORDINANZE
        # ----------------------------------------------------

        if cerca_ordinanze:

            try:

                lista = lista_ordinanze(
                    anno
                )

                liste.extend(
                    lista
                )

            except Exception as e:

                risultati.append({
                    "errore": (
                        f"Ordinanze {anno}: {e}"
                    )
                })

        # ----------------------------------------------------
        # DELIBERE GIUNTA
        # ----------------------------------------------------

        if cerca_giunta:

            try:

                lista = lista_giunta(
                    anno
                )

                liste.extend(
                    lista
                )

            except Exception as e:

                risultati.append({
                    "errore": (
                        f"Delibere Giunta {anno}: {e}"
                    )
                })

        # ====================================================
        # ANALISI DOCUMENTI
        # ====================================================

        for item in liste:

            totale += 1

            if progress:

                progress(
                    totale,
                    item
                )

            titolo = item.get(
                "titolo",
                ""
            )

            hit_titolo = (
                termine_l
                in titolo.lower()
            )

            # ------------------------------------------------
            # SOLO TITOLI
            # ------------------------------------------------

            if solo_titoli:

                if hit_titolo:

                    risultati.append({
                        **item,
                        "dove": "titolo/oggetto",
                        "snippet": titolo
                    })

                continue

            # ------------------------------------------------
            # CERCA NEL PDF
            # ------------------------------------------------

            try:

                pdf = trova_pdf(
                    item["url"]
                )

                # --------------------------------------------
                # PDF NON TROVATO
                # --------------------------------------------

                if not pdf:

                    if hit_titolo:

                        risultati.append({
                            **item,
                            "dove": "titolo/oggetto (no PDF)",
                            "snippet": titolo
                        })

                    continue

                # --------------------------------------------
                # CACHE KEY
                # --------------------------------------------

                key = (
                    f"{item['tipo']}_"
                    f"{item['anno']}_"
                    f"{item['numero']}_"
                    f"{titolo[:50]}"
                )

                testo = testo_documento(
                    pdf,
                    key
                )

                hit_pdf = (
                    termine_l
                    in testo.lower()
                )

                # --------------------------------------------
                # RISULTATO
                # --------------------------------------------

                if hit_pdf or hit_titolo:

                    if hit_pdf and hit_titolo:

                        dove = "PDF + titolo/oggetto"

                    elif hit_pdf:

                        dove = "PDF"

                    else:

                        dove = "titolo/oggetto"

                    risultati.append({
                        **item,
                        "dove": dove,
                        "snippet": (
                            snippet(
                                testo,
                                termine
                            )
                            or titolo
                        ),
                        "pdf": pdf,
                    })

            except Exception as e:

                risultati.append({
                    **item,
                    "errore": str(e)
                })

            # ------------------------------------------------
            # PAUSA
            # ------------------------------------------------

            time.sleep(
                0.25
            )

    return risultati, totale


# ============================================================
# STREAMLIT
# ============================================================

st.set_page_config(
    page_title="Atti Gravellona Lomellina",
    page_icon="📚",
    layout="centered"
)


# ============================================================
# HEADER
# ============================================================

st.title(
    "📚 Ricerca Atti"
)

st.caption(
    "Comune di Gravellona Lomellina"
)

st.markdown(
    "Cerca nelle **Ordinanze** e nelle "
    "**Delibere di Giunta**, anche all'interno "
    "dei PDF."
)


# ============================================================
# AVVISO OCR
# ============================================================

if not OCR_OK:

    st.warning(
        "⚠️ OCR non disponibile. "
        "I PDF che contengono solo immagini "
        "non potranno essere ricercati."
    )


# ============================================================
# TERMINE
# ============================================================

termine = st.text_input(
    "🔎 Termine da cercare",
    placeholder=(
        "es. circolazione, scuola, "
        "videosorveglianza, contributo..."
    )
)


# ============================================================
# TIPO ATTO
# ============================================================

tipo = st.radio(
    "📂 Cerca in",
    [
        "Entrambi",
        "Ordinanze",
        "Delibere di Giunta"
    ],
    horizontal=True
)


# ============================================================
# ANNI
# ============================================================

col1, col2 = st.columns(2)

with col1:

    max_anni = st.number_input(
        "📅 Anni recenti",
        min_value=1,
        max_value=20,
        value=3,
        step=1
    )

with col2:

    solo_titoli = st.checkbox(
        "⚡ Solo titoli/oggetti",
        value=False
    )


# ============================================================
# INFO
# ============================================================

if solo_titoli:

    st.info(
        "La ricerca nei soli titoli è molto più veloce "
        "perché non scarica i PDF."
    )


# ============================================================
# PULSANTE
# ============================================================

cerca_button = st.button(
    "🔍 CERCA",
    type="primary",
    use_container_width=True
)


# ============================================================
# ESECUZIONE RICERCA
# ============================================================

if cerca_button:

    if not termine.strip():

        st.warning(
            "Inserisci una parola o una frase da cercare."
        )

        st.stop()

    # --------------------------------------------------------
    # RECUPERO ANNI
    # --------------------------------------------------------

    with st.spinner(
        "Recupero gli anni disponibili..."
    ):

        try:

            anni_set = set()

            if tipo in (
                "Entrambi",
                "Ordinanze"
            ):

                anni_set.update(
                    anni_ordinanze()
                )

            if tipo in (
                "Entrambi",
                "Delibere di Giunta"
            ):

                anni_set.update(
                    anni_giunta()
                )

            anni = sorted(
                anni_set,
                reverse=True
            )[:int(max_anni)]

        except Exception as e:

            st.error(
                f"Errore durante la connessione "
                f"al sito del Comune: {e}"
            )

            st.stop()

    # --------------------------------------------------------
    # INFO RICERCA
    # --------------------------------------------------------

    st.markdown(
        f"**Tipo:** {tipo}  \n"
        f"**Anni:** {', '.join(anni)}  \n"
        f"**Termine:** `{termine.strip()}`"
    )

    st.divider()

    # --------------------------------------------------------
    # PROGRESS BAR
    # --------------------------------------------------------

    bar = st.progress(
        0.0
    )

    status = st.empty()

    # --------------------------------------------------------
    # CALLBACK
    # --------------------------------------------------------

    def on_progress(
        n,
        item
    ):

        status.write(
            f"🔎 Analizzo "
            f"**{item.get('tipo', '')}** "
            f"n.{item.get('numero', '?')} — "
            f"{item.get('titolo', '')[:70]}"
        )

        # Non conosciamo il totale in anticipo.
        # Usiamo una barra indicativa.

        bar.progress(
            min(
                0.95,
                n / 100
            )
        )

    # --------------------------------------------------------
    # RICERCA
    # --------------------------------------------------------

    risultati, totale = cerca(
        termine.strip(),
        anni,
        tipo=tipo,
        solo_titoli=solo_titoli,
        progress=on_progress
    )

    bar.progress(
        1.0
    )

    status.success(
        f"Ricerca completata. "
        f"Documenti analizzati: {totale}"
    )


    # ========================================================
    # SEPARA RISULTATI ED ERRORI
    # ========================================================

    hits = [
        r
        for r in risultati
        if r.get("dove")
    ]

    errori = [
        r
        for r in risultati
        if r.get("errore")
        and not r.get("dove")
    ]


    # ========================================================
    # RISULTATI
    # ========================================================

    st.subheader(
        f"📌 Risultati: {len(hits)}"
    )


    if not hits:

        st.info(
            "Nessuna occorrenza trovata."
        )


    # ========================================================
    # CARD RISULTATI
    # ========================================================

    for r in hits:

        tipo_atto = r.get(
            "tipo",
            "Atto"
        )

        numero = r.get(
            "numero",
            "?"
        )

        data = r.get(
            "data",
            "?"
        )

        titolo = r.get(
            "titolo",
            ""
        )

        dove = r.get(
            "dove",
            ""
        )

        testo_snippet = r.get(
            "snippet",
            ""
        )

        st.markdown(
            f"### {tipo_atto}"
        )

        st.markdown(
            f"**N. {numero}** — **{data}**"
        )

        st.markdown(
            f"**{titolo}**"
        )

        # ----------------------------------------------------
        # DOVE È STATA TROVATA LA PAROLA
        # ----------------------------------------------------

        if dove == "PDF":

            st.success(
                "📄 Termine trovato nel PDF"
            )

        elif dove == "PDF + titolo/oggetto":

            st.success(
                "📄 Termine trovato nel PDF "
                "e nel titolo/oggetto"
            )

        else:

            st.info(
                f"🔎 Trovato in: {dove}"
            )

        # ----------------------------------------------------
        # SNIPPET
        # ----------------------------------------------------

        if testo_snippet:

            st.markdown(
                f"> {testo_snippet}"
            )

        # ----------------------------------------------------
        # LINK
        # ----------------------------------------------------

        col_a, col_b = st.columns(2)

        with col_a:

            st.link_button(
                "🌐 Apri scheda",
                r.get("url", ""),
                use_container_width=True
            )

        with col_b:

            if r.get("pdf"):

                st.link_button(
                    "📄 Apri PDF",
                    r["pdf"],
                    use_container_width=True
                )

        st.divider()


    # ========================================================
    # ERRORI
    # ========================================================

    if errori:

        with st.expander(
            f"⚠️ Errori ({len(errori)})"
        ):

            for errore in errori:

                st.write(
                    errore
                )
