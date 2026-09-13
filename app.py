from __future__ import annotations

import html
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

import folium
import requests
import streamlit as st
from folium.plugins import Fullscreen
from streamlit_folium import st_folium

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
DEFAULT_DB = DATA_DIR / "locais.json"
RESOLVED_DB = DATA_DIR / "locais_resolvidos.json"

WORLD_GEOJSON_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
    "master/geojson/ne_110m_admin_0_countries.geojson"
)
COMMONS_API_URL = "https://commons.wikimedia.org/w/api.php"
COMMONS_USER_AGENT = "AtlasArtePreHistorica/1.2 (Streamlit educational project)"
COMMONS_THUMB_WIDTH = 900
COMMONS_CANDIDATES = 6

PAGE_BG = "#F4F1EA"
PANEL_BG = "#FCFAF6"
LAND_COLOR = "#AAB8A6"
COUNTRY_BORDER = "#F7F4ED"
MARKER_COLOR = "#8C5741"
MARKER_BORDER = "#FFF8EF"
TEXT_COLOR = "#262824"
MUTED_TEXT = "#666A62"
OCEAN_COLOR = "#E4EBE7"

POSITIVE_TERMS = {
    "rock art": 16, "cave art": 16, "cave painting": 18, "painting": 10,
    "pictograph": 14, "petroglyph": 16, "engraving": 12, "engraved": 10,
    "stencil": 12, "mural": 10, "ochre": 12, "prehistoric": 8,
    "paleolithic": 8, "palaeolithic": 8, "artifact": 7, "artefact": 7,
}
NEGATIVE_TERMS = {
    "map": 20, "locator": 24, "flag": 20, "logo": 20, "icon": 16,
    "diagram": 12, "museum": 7, "entrance": 10, "visitor": 8,
    "sign": 10, "road": 8, "parking": 10, "panorama": 8,
    "landscape": 7, "aerial": 8, "satellite": 20,
    "reconstruction": 12, "replica": 14, "facsimile": 10,
}

st.set_page_config(
    page_title="Atlas da Arte Pré-Histórica",
    page_icon="◉",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    f"""
    <style>
        .stApp {{ background:{PAGE_BG}; color:{TEXT_COLOR}; }}
        [data-testid="stSidebar"] {{ background:{PANEL_BG}; }}
        [data-testid="stHeader"] {{ background:rgba(0,0,0,0); }}
        .block-container {{ padding-top:1.4rem; padding-bottom:1rem; max-width:1500px; }}
        h1,h2,h3 {{ letter-spacing:-.03em; color:{TEXT_COLOR}; }}
        .atlas-kicker {{
            color:{MUTED_TEXT}; font-size:.82rem; text-transform:uppercase;
            letter-spacing:.12em; font-weight:700; margin-bottom:.25rem;
        }}
        .atlas-subtitle {{
            color:{MUTED_TEXT}; max-width:850px; line-height:1.55; margin-top:-.35rem;
        }}
        .atlas-stat {{
            background:{PANEL_BG}; border:1px solid rgba(38,40,36,.10);
            padding:.75rem .9rem; border-radius:0;
        }}
        .atlas-stat strong {{ font-size:1.15rem; }}
        .atlas-footnote {{ color:{MUTED_TEXT}; font-size:.78rem; line-height:1.45; }}
    </style>
    """,
    unsafe_allow_html=True,
)


def normalize(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.casefold().strip()


def clean_html(value: Any) -> str:
    if value is None:
        return ""
    text = re.sub(r"<br\s*/?>", " ", str(value), flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


def meta_value(metadata: dict[str, Any], key: str) -> str:
    item = metadata.get(key, {})
    return clean_html(item.get("value", "")) if isinstance(item, dict) else clean_html(item)


@st.cache_data(show_spinner=False)
def load_database() -> dict[str, Any]:
    path = RESOLVED_DB if RESOLVED_DB.exists() else DEFAULT_DB
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if isinstance(data, list):
        return {"metadata": {}, "locais": data, "fonte": path.name}
    data["fonte"] = path.name
    return data


@st.cache_data(ttl=60 * 60 * 24 * 7, show_spinner=False)
def load_world_geojson() -> dict[str, Any]:
    response = requests.get(
        WORLD_GEOJSON_URL,
        timeout=30,
        headers={"User-Agent": COMMONS_USER_AGENT},
    )
    response.raise_for_status()
    return response.json()


def score_image(item: dict[str, Any], query: str) -> float:
    title = normalize(item.get("commons_file", ""))
    desc = normalize(item.get("descricao_imagem", ""))
    cats = normalize(item.get("categorias_commons", ""))
    combined = f"{title} {desc} {cats}"
    score = 0.0

    for token in [x for x in re.findall(r"[a-z0-9]+", normalize(query)) if len(x) >= 3]:
        score += 5 if token in title else (1.5 if token in combined else 0)

    for term, weight in POSITIVE_TERMS.items():
        n = normalize(term)
        score += weight if n in title else (weight * .35 if n in combined else 0)

    for term, weight in NEGATIVE_TERMS.items():
        n = normalize(term)
        score -= weight if n in title else (weight * .35 if n in combined else 0)

    width = int(item.get("width") or 0)
    height = int(item.get("height") or 0)
    if width >= 800 and height >= 500:
        score += 3
    if item.get("licenca"):
        score += 2
    return round(score, 2)


@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)
def resolve_commons_image(search_query: str) -> dict[str, Any] | None:
    if not search_query:
        return None

    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": search_query,
        "gsrnamespace": 6,
        "gsrlimit": COMMONS_CANDIDATES,
        "prop": "imageinfo",
        "iiprop": "url|size|mime|mediatype|extmetadata",
        "iiurlwidth": COMMONS_THUMB_WIDTH,
        "iiextmetadatalanguage": "en",
        "iiextmetadatafilter": (
            "Artist|Credit|LicenseShortName|LicenseUrl|UsageTerms|"
            "ImageDescription|ObjectName|Categories"
        ),
        "format": "json",
        "formatversion": 2,
        "maxlag": 5,
    }

    response = requests.get(
        COMMONS_API_URL,
        params=params,
        timeout=25,
        headers={"User-Agent": COMMONS_USER_AGENT, "Accept": "application/json"},
    )
    response.raise_for_status()
    pages = response.json().get("query", {}).get("pages", [])
    candidates: list[dict[str, Any]] = []

    for page in pages:
        infos = page.get("imageinfo") or []
        if not infos:
            continue
        info = infos[0]
        mime = str(info.get("mime") or "").lower()
        media_type = str(info.get("mediatype") or "").upper()
        if mime and not mime.startswith("image/"):
            continue
        if media_type and media_type not in {"BITMAP", "DRAWING"}:
            continue

        ext = info.get("extmetadata") or {}
        item = {
            "commons_file": str(page.get("title") or ""),
            "thumbnail_url": info.get("thumburl") or info.get("url"),
            "original_url": info.get("url"),
            "page_url": info.get("descriptionurl"),
            "width": info.get("width"),
            "height": info.get("height"),
            "autor": meta_value(ext, "Artist") or meta_value(ext, "Credit") or None,
            "licenca": meta_value(ext, "LicenseShortName") or meta_value(ext, "UsageTerms") or None,
            "licenca_url": meta_value(ext, "LicenseUrl") or None,
            "descricao_imagem": meta_value(ext, "ImageDescription") or meta_value(ext, "ObjectName") or None,
            "categorias_commons": meta_value(ext, "Categories") or None,
            "search_index": int(page.get("index") or 999),
        }
        item["auto_score"] = score_image(item, search_query)
        candidates.append(item)

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: (float(x.get("auto_score") or -9999), -int(x.get("search_index") or 999)),
        reverse=True,
    )
    return candidates[0]


def ensure_image(local: dict[str, Any]) -> dict[str, Any]:
    image = dict(local.get("imagem") or {})
    if image.get("thumbnail_url") or image.get("original_url"):
        return image

    query = str(image.get("commons_search") or "").strip()
    if not query:
        return image

    try:
        resolved = resolve_commons_image(query)
    except Exception:
        return image

    if resolved:
        image.update(resolved)
        image["status"] = "resolvido_em_tempo_de_execucao"
    return image


def enrich_images(locais: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not locais:
        return []
    enriched: list[dict[str, Any]] = []
    progress = st.progress(0, text="Carregando imagens do Wikimedia Commons…")
    total = len(locais)

    for index, local in enumerate(locais, 1):
        item = dict(local)
        item["imagem"] = ensure_image(local)
        enriched.append(item)
        progress.progress(index / total, text=f"Carregando imagens… {index}/{total}")

    progress.empty()
    return enriched


def image_block(local: dict[str, Any]) -> str:
    image = local.get("imagem") or {}
    url = image.get("thumbnail_url") or image.get("original_url")
    if not url:
        return (
            '<div style="height:170px;background:#E9E5DC;display:flex;align-items:center;'
            'justify-content:center;color:#777268;font-size:12px;margin-bottom:12px;">'
            "Imagem não encontrada</div>"
        )
    safe_url = html.escape(str(url), quote=True)
    return (
        f'<img src="{safe_url}" alt="Arte do sítio" '
        'style="display:block;width:100%;height:190px;object-fit:cover;'
        'margin:0 0 12px;background:#E9E5DC;">'
    )


def popup_html(local: dict[str, Any]) -> str:
    image = local.get("imagem") or {}

    def esc(key: str, default: str = "—") -> str:
        return html.escape(str(local.get(key) or default))

    credits = []
    if image.get("autor"):
        credits.append(f"Foto: {html.escape(str(image['autor']))}")
    if image.get("licenca"):
        credits.append(html.escape(str(image["licenca"])))
    if image.get("page_url"):
        url = html.escape(str(image["page_url"]), quote=True)
        credits.append(f'<a href="{url}" target="_blank" rel="noopener">Wikimedia Commons</a>')

    note = ""
    if local.get("observacao_datacao"):
        note = (
            '<div style="margin-top:9px;padding-top:9px;border-top:1px solid #E3DDD2;'
            'font-size:11px;color:#706B62;line-height:1.35;">'
            f"<strong>Nota de datação:</strong> {html.escape(str(local['observacao_datacao']))}</div>"
        )

    footer = ""
    if credits:
        footer = (
            '<div style="margin-top:10px;font-size:10px;color:#777268;line-height:1.35;">'
            + " · ".join(credits) + "</div>"
        )

    return f"""
    <div style="width:310px;font-family:Arial,Helvetica,sans-serif;color:#272823;">
        {image_block(local)}
        <div style="font-size:18px;font-weight:700;line-height:1.15;margin-bottom:4px;">{esc('nome')}</div>
        <div style="font-size:12px;color:#6B6C65;margin-bottom:11px;">{esc('pais_atual')}</div>
        <div style="font-size:12px;line-height:1.45;margin-bottom:7px;"><strong>Datação:</strong> {esc('idade_aproximada')}</div>
        <div style="font-size:12px;line-height:1.45;margin-bottom:7px;"><strong>Tipo:</strong> {esc('tipo')}</div>
        <div style="font-size:12px;line-height:1.48;color:#454740;">{esc('descricao_curta')}</div>
        {note}{footer}
    </div>
    """


def build_map(locais: list[dict[str, Any]], world_geojson: dict[str, Any]) -> folium.Map:
    world_map = folium.Map(
        location=[13, 5],
        zoom_start=2,
        min_zoom=2,
        max_zoom=8,
        tiles=None,
        prefer_canvas=True,
        zoom_control=True,
        attribution_control=True,
    )

    world_map.get_root().header.add_child(
        folium.Element(
            f"""
            <style>
                .leaflet-container {{ background:{OCEAN_COLOR} !important; }}
                .leaflet-control-zoom a {{ border-radius:0 !important; color:#3B3D38 !important; }}
                .leaflet-popup-content-wrapper,.leaflet-popup-tip {{
                    background:{PANEL_BG}; border-radius:0 !important;
                }}
                .leaflet-popup-content {{ margin:14px !important; }}
            </style>
            """
        )
    )

    folium.GeoJson(
        world_geojson,
        style_function=lambda _feature: {
            "fillColor": LAND_COLOR,
            "color": COUNTRY_BORDER,
            "weight": .75,
            "fillOpacity": 1,
        },
        smooth_factor=.7,
    ).add_to(world_map)

    for local in locais:
        lat, lon = local.get("latitude"), local.get("longitude")
        if lat is None or lon is None:
            continue

        folium.CircleMarker(
            [float(lat), float(lon)],
            radius=5.5,
            color=MARKER_BORDER,
            weight=1.5,
            fill=True,
            fill_color=MARKER_COLOR,
            fill_opacity=.96,
            tooltip=folium.Tooltip(html.escape(str(local.get("nome", "Local")))),
            popup=folium.Popup(
                folium.Html(popup_html(local), script=True),
                max_width=350,
                min_width=330,
            ),
        ).add_to(world_map)

    Fullscreen(
        position="topright",
        title="Tela cheia",
        title_cancel="Sair da tela cheia",
        force_separate_button=True,
    ).add_to(world_map)

    world_map.fit_bounds([[-57, -178], [82, 178]])
    return world_map


def apply_filters(locais: list[dict[str, Any]]) -> list[dict[str, Any]]:
    regions = sorted({str(x.get("regiao")) for x in locais if x.get("regiao")})
    types = sorted({str(x.get("tipo")) for x in locais if x.get("tipo")})

    st.sidebar.markdown("### Explorar")
    query = st.sidebar.text_input("Pesquisar", placeholder="Local, país ou região")
    selected_regions = st.sidebar.multiselect("Regiões", regions, default=regions)
    selected_types = st.sidebar.multiselect("Tipos", types, default=types)

    max_age = max(int(x.get("antiguidade_referencia_anos") or 0) for x in locais)
    age_min, age_max = st.sidebar.slider(
        "Antiguidade de referência (anos)",
        0,
        max_age,
        (0, max_age),
        step=500,
    )

    q = normalize(query)
    result = []
    for item in locais:
        age = int(item.get("antiguidade_referencia_anos") or 0)
        haystack = " ".join(
            str(item.get(field) or "")
            for field in ("nome", "pais_atual", "regiao", "subregiao", "tipo")
        )
        if item.get("regiao") not in selected_regions:
            continue
        if item.get("tipo") not in selected_types:
            continue
        if not (age_min <= age <= age_max):
            continue
        if q and q not in normalize(haystack):
            continue
        result.append(item)

    st.sidebar.divider()
    st.sidebar.caption(
        "Clique em um ponto para abrir a ficha. As datações são aproximadas "
        "e podem representar fases diferentes de produção."
    )
    return result


def main() -> None:
    database = load_database()
    locais = database.get("locais", [])

    st.markdown('<div class="atlas-kicker">Atlas interativo</div>', unsafe_allow_html=True)
    st.title("Arte pré-histórica pelo mundo")
    st.markdown(
        '<div class="atlas-subtitle">Mapa de sítios selecionados de arte rupestre e outras '
        'formas de expressão visual pré-histórica. O mapa-base exibe somente os territórios '
        'e suas divisas, sem nomes geográficos.</div>',
        unsafe_allow_html=True,
    )

    filtered = apply_filters(locais)
    if not filtered:
        st.warning("Nenhum local corresponde aos filtros atuais.")
        return

    filtered = enrich_images(filtered)
    loaded = sum(
        1 for item in filtered
        if (item.get("imagem") or {}).get("thumbnail_url")
        or (item.get("imagem") or {}).get("original_url")
    )

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown(
            f'<div class="atlas-stat"><strong>{len(filtered)}</strong><br><span>locais exibidos</span></div>',
            unsafe_allow_html=True,
        )
    with col2:
        st.markdown(
            f'<div class="atlas-stat"><strong>{len(locais)}</strong><br><span>locais no banco</span></div>',
            unsafe_allow_html=True,
        )
    with col3:
        st.markdown(
            f'<div class="atlas-stat"><strong>{loaded}/{len(filtered)}</strong><br><span>imagens carregadas</span></div>',
            unsafe_allow_html=True,
        )

    st.write("")

    try:
        world_geojson = load_world_geojson()
    except Exception as exc:
        st.error("Não foi possível carregar as fronteiras dos países.")
        st.exception(exc)
        return

    st_folium(
        build_map(filtered, world_geojson),
        height=720,
        use_container_width=True,
        returned_objects=[],
        key="world_rock_art_map",
    )

    st.markdown(
        '<div class="atlas-footnote">Banco utilizado: '
        f'<strong>{html.escape(str(database.get("fonte", "locais.json")))}</strong>. '
        'Quando uma fotografia não está salva no banco, o aplicativo pesquisa '
        'automaticamente uma candidata no Wikimedia Commons e mantém o resultado em cache.</div>',
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
