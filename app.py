from __future__ import annotations

import html
import json
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

# Paleta do projeto
PAGE_BG = "#F4F1EA"
PANEL_BG = "#FCFAF6"
LAND_COLOR = "#AAB8A6"
COUNTRY_BORDER = "#F7F4ED"
MARKER_COLOR = "#8C5741"
MARKER_BORDER = "#FFF8EF"
TEXT_COLOR = "#262824"
MUTED_TEXT = "#666A62"
OCEAN_COLOR = "#E4EBE7"


st.set_page_config(
    page_title="Atlas da Arte Pré-Histórica",
    page_icon="◉",
    layout="wide",
    initial_sidebar_state="expanded",
)


# Toda a personalização visual continua dentro do Python.
st.markdown(
    f"""
    <style>
        .stApp {{ background: {PAGE_BG}; color: {TEXT_COLOR}; }}
        [data-testid="stSidebar"] {{ background: {PANEL_BG}; }}
        [data-testid="stHeader"] {{ background: rgba(0,0,0,0); }}
        .block-container {{ padding-top: 1.4rem; padding-bottom: 1rem; max-width: 1500px; }}
        h1, h2, h3 {{ letter-spacing: -0.03em; color: {TEXT_COLOR}; }}
        .atlas-kicker {{
            color: {MUTED_TEXT}; font-size: .82rem; text-transform: uppercase;
            letter-spacing: .12em; font-weight: 700; margin-bottom: .25rem;
        }}
        .atlas-subtitle {{ color: {MUTED_TEXT}; max-width: 850px; line-height: 1.55; margin-top: -.35rem; }}
        .atlas-stat {{
            background: {PANEL_BG}; border: 1px solid rgba(38,40,36,.10);
            padding: .75rem .9rem; border-radius: 0;
        }}
        .atlas-stat strong {{ font-size: 1.15rem; }}
        div[data-testid="stTextInput"] input,
        div[data-testid="stSelectbox"] > div > div,
        div[data-testid="stMultiSelect"] > div > div {{ border-radius: 0 !important; }}
        .stButton > button {{ border-radius: 0 !important; }}
        .atlas-footnote {{ color: {MUTED_TEXT}; font-size: .78rem; line-height: 1.45; }}
    </style>
    """,
    unsafe_allow_html=True,
)


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return value.casefold().strip()


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
        headers={"User-Agent": "AtlasArtePreHistorica/1.0"},
    )
    response.raise_for_status()
    return response.json()


def image_block(local: dict[str, Any]) -> str:
    image = local.get("imagem") or {}
    url = image.get("thumbnail_url") or image.get("original_url")

    if not url:
        return (
            '<div style="height:170px;background:#E9E5DC;display:flex;align-items:center;'
            'justify-content:center;color:#777268;font-size:12px;margin-bottom:12px;">'
            "Imagem ainda não vinculada"
            "</div>"
        )

    safe_url = html.escape(str(url), quote=True)
    return (
        f'<img src="{safe_url}" alt="Arte do sítio" '
        'style="display:block;width:100%;height:190px;object-fit:cover;'
        'margin:0 0 12px 0;background:#E9E5DC;">'
    )


def popup_html(local: dict[str, Any]) -> str:
    image = local.get("imagem") or {}

    def esc(key: str, default: str = "—") -> str:
        return html.escape(str(local.get(key) or default))

    photo_meta = []
    if image.get("autor"):
        photo_meta.append(f"Foto: {html.escape(str(image['autor']))}")
    if image.get("licenca"):
        photo_meta.append(html.escape(str(image["licenca"])))

    credit_line = " · ".join(photo_meta)
    source_link = ""
    if image.get("page_url"):
        page_url = html.escape(str(image["page_url"]), quote=True)
        source_link = (
            f'<a href="{page_url}" target="_blank" rel="noopener" '
            'style="color:#6C4939;text-decoration:underline;">Wikimedia Commons</a>'
        )

    footer_bits = [bit for bit in (credit_line, source_link) if bit]
    footer = " · ".join(footer_bits)

    observation = local.get("observacao_datacao")
    observation_html = ""
    if observation:
        observation_html = (
            '<div style="margin-top:9px;padding-top:9px;border-top:1px solid #E3DDD2;'
            'font-size:11px;color:#706B62;line-height:1.35;">'
            f"<strong>Nota de datação:</strong> {html.escape(str(observation))}"
            "</div>"
        )

    footer_html = ""
    if footer:
        footer_html = (
            '<div style="margin-top:10px;font-size:10px;color:#777268;line-height:1.35;">'
            f"{footer}</div>"
        )

    return f"""
    <div style="width:310px;font-family:Arial,Helvetica,sans-serif;color:#272823;">
        {image_block(local)}
        <div style="font-size:18px;font-weight:700;line-height:1.15;margin-bottom:4px;">{esc('nome')}</div>
        <div style="font-size:12px;color:#6B6C65;margin-bottom:11px;">{esc('pais_atual')}</div>
        <div style="font-size:12px;line-height:1.45;margin-bottom:7px;">
            <strong>Datação:</strong> {esc('idade_aproximada')}
        </div>
        <div style="font-size:12px;line-height:1.45;margin-bottom:7px;">
            <strong>Tipo:</strong> {esc('tipo')}
        </div>
        <div style="font-size:12px;line-height:1.48;color:#454740;">{esc('descricao_curta')}</div>
        {observation_html}
        {footer_html}
    </div>
    """


def build_map(locais: list[dict[str, Any]], world_geojson: dict[str, Any]) -> folium.Map:
    world_map = folium.Map(
        location=[13, 5],
        zoom_start=2,
        min_zoom=2,
        max_zoom=8,
        tiles=None,
        control_scale=False,
        prefer_canvas=True,
        zoom_control=True,
        attribution_control=True,
    )

    # Fundo do oceano e controles do Leaflet, definidos no próprio Python.
    world_map.get_root().header.add_child(
        folium.Element(
            f"""
            <style>
                .leaflet-container {{ background: {OCEAN_COLOR} !important; }}
                .leaflet-control-zoom a {{ border-radius:0 !important; color:#3B3D38 !important; }}
                .leaflet-popup-content-wrapper, .leaflet-popup-tip {{
                    background:{PANEL_BG}; border-radius:0 !important;
                }}
                .leaflet-popup-content {{ margin:14px !important; }}
            </style>
            """
        )
    )

    folium.GeoJson(
        world_geojson,
        name="Países",
        style_function=lambda _feature: {
            "fillColor": LAND_COLOR,
            "color": COUNTRY_BORDER,
            "weight": 0.75,
            "fillOpacity": 1,
        },
        highlight_function=lambda _feature: {
            "fillColor": LAND_COLOR,
            "color": COUNTRY_BORDER,
            "weight": 0.75,
            "fillOpacity": 1,
        },
        smooth_factor=0.7,
    ).add_to(world_map)

    for local in locais:
        lat = local.get("latitude")
        lon = local.get("longitude")
        if lat is None or lon is None:
            continue

        popup = folium.Popup(
            folium.Html(popup_html(local), script=True),
            max_width=350,
            min_width=330,
        )

        folium.CircleMarker(
            location=[float(lat), float(lon)],
            radius=5.5,
            color=MARKER_BORDER,
            weight=1.5,
            fill=True,
            fill_color=MARKER_COLOR,
            fill_opacity=0.96,
            tooltip=folium.Tooltip(
                html.escape(str(local.get("nome", "Local"))),
                sticky=False,
            ),
            popup=popup,
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
    regions = sorted({str(item.get("regiao")) for item in locais if item.get("regiao")})
    types = sorted({str(item.get("tipo")) for item in locais if item.get("tipo")})

    st.sidebar.markdown("### Explorar")
    query = st.sidebar.text_input(
        "Pesquisar",
        placeholder="Local, país ou região",
    )
    selected_regions = st.sidebar.multiselect(
        "Regiões",
        regions,
        default=regions,
    )
    selected_types = st.sidebar.multiselect(
        "Tipos",
        types,
        default=types,
    )

    max_age = max(int(item.get("antiguidade_referencia_anos") or 0) for item in locais)
    age_limit = st.sidebar.slider(
        "Antiguidade de referência (anos)",
        min_value=0,
        max_value=max_age,
        value=(0, max_age),
        step=500,
        help="Filtro auxiliar. A datação exibida no popup é mais completa e pode ter incertezas.",
    )

    normalized_query = normalize(query)
    filtered = []

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
        if not (age_limit[0] <= age <= age_limit[1]):
            continue
        if normalized_query and normalized_query not in normalize(haystack):
            continue

        filtered.append(item)

    st.sidebar.divider()
    st.sidebar.caption(
        "Clique em um ponto no mapa para abrir a ficha do sítio. "
        "As datas são aproximadas e podem representar fases diferentes de produção."
    )
    return filtered


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

    resolved_images = sum(
        1
        for item in locais
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
            f'<div class="atlas-stat"><strong>{resolved_images}/{len(locais)}</strong><br><span>imagens vinculadas</span></div>',
            unsafe_allow_html=True,
        )

    st.write("")

    if not filtered:
        st.warning("Nenhum local corresponde aos filtros atuais.")
        return

    try:
        world_geojson = load_world_geojson()
    except Exception as exc:
        st.error(
            "Não foi possível carregar as fronteiras dos países. "
            "Verifique a conexão com a internet e tente novamente."
        )
        st.exception(exc)
        return

    map_object = build_map(filtered, world_geojson)
    st_folium(
        map_object,
        height=720,
        use_container_width=True,
        returned_objects=[],
        key="world_rock_art_map",
    )

    st.markdown(
        '<div class="atlas-footnote">Banco utilizado: '
        f'<strong>{html.escape(str(database.get("fonte", "locais.json")))}</strong>. '
        'Quando <code>data/locais_resolvidos.json</code> existir, a aplicação o utiliza '
        'automaticamente para exibir as fotografias e seus créditos.</div>',
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
