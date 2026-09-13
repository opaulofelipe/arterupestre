from __future__ import annotations

import base64
import html
import io
import json
import unicodedata
from pathlib import Path
from typing import Any

import folium
import requests
import streamlit as st
from PIL import Image, ImageOps
from folium.plugins import Fullscreen
from streamlit_folium import st_folium

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
DATABASE_PATH = DATA_DIR / "locais.json"

# Quase todos os JPGs têm exatamente o mesmo nome do campo "id" do JSON.
# Só estes três usam nomes diferentes na pasta data/.
IMAGE_FILE_OVERRIDES = {
    "blombos": "blombos_cave.jpg",
    "apollo_11": "apollo_11_cave.jpg",
    "tsodilo": "tsodilo_hills.jpg",
}

WORLD_GEOJSON_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
    "master/geojson/ne_110m_admin_0_countries.geojson"
)

PAGE_BG = "#F4F1EA"
PANEL_BG = "#FCFAF6"
LAND_COLOR = "#AAB8A6"
COUNTRY_BORDER = "#F7F4ED"
MARKER_COLOR = "#8C5741"
MARKER_BORDER = "#FFF8EF"
TEXT_COLOR = "#262824"
MUTED_TEXT = "#666A62"
OCEAN_COLOR = "#E4EBE7"

# Mantém o HTML do mapa leve mesmo quando a foto original tem vários MB.
POPUP_IMAGE_MAX_SIZE = (900, 650)
POPUP_IMAGE_QUALITY = 80

st.set_page_config(
    page_title="Atlas da Arte Pré-Histórica",
    page_icon="◉",
    layout="wide",
    initial_sidebar_state="expanded",
)

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
        .atlas-subtitle {{
            color: {MUTED_TEXT}; max-width: 850px; line-height: 1.55; margin-top: -.35rem;
        }}
        .atlas-stat {{
            background: {PANEL_BG}; border: 1px solid rgba(38,40,36,.10);
            padding: .75rem .9rem; border-radius: 0;
        }}
        .atlas-stat strong {{ font-size: 1.15rem; }}
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
    """Carrega apenas o banco principal; não usa mais locais_resolvidos.json."""
    with DATABASE_PATH.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if isinstance(data, list):
        data = {"metadata": {}, "locais": data}

    data["fonte"] = DATABASE_PATH.name
    return data


@st.cache_data(ttl=60 * 60 * 24 * 7, show_spinner=False)
def load_world_geojson() -> dict[str, Any]:
    response = requests.get(
        WORLD_GEOJSON_URL,
        timeout=30,
        headers={"User-Agent": "AtlasArtePreHistorica/3.0"},
    )
    response.raise_for_status()
    return response.json()


def local_image_path(local: dict[str, Any]) -> Path:
    """Retorna o JPG correspondente ao id do local dentro de data/."""
    local_id = str(local.get("id") or "").strip()
    filename = IMAGE_FILE_OVERRIDES.get(local_id, f"{local_id}.jpg")
    return DATA_DIR / filename


def has_local_image(local: dict[str, Any]) -> bool:
    path = local_image_path(local)
    return bool(path.is_file() and path.stat().st_size > 0)


@st.cache_data(show_spinner=False)
def image_as_data_uri(path_string: str, modified_ns: int) -> str:
    """
    Converte uma foto local em miniatura JPEG incorporada ao HTML.

    modified_ns participa da chave do cache para que uma imagem substituída
    no GitHub seja recarregada automaticamente após o redeploy.
    """
    del modified_ns
    path = Path(path_string)

    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail(POPUP_IMAGE_MAX_SIZE, Image.Resampling.LANCZOS)

        buffer = io.BytesIO()
        image.save(
            buffer,
            format="JPEG",
            quality=POPUP_IMAGE_QUALITY,
            optimize=True,
            progressive=True,
        )

    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def image_block(local: dict[str, Any]) -> str:
    path = local_image_path(local)

    if not has_local_image(local):
        expected = html.escape(path.name)
        return (
            '<div style="height:190px;background:#E9E5DC;display:flex;align-items:center;'
            'justify-content:center;color:#777268;font-size:12px;margin-bottom:12px;'
            'padding:12px;text-align:center;box-sizing:border-box;">'
            f"Imagem não encontrada: data/{expected}"
            "</div>"
        )

    try:
        data_uri = image_as_data_uri(str(path), path.stat().st_mtime_ns)
    except Exception:
        expected = html.escape(path.name)
        return (
            '<div style="height:190px;background:#E9E5DC;display:flex;align-items:center;'
            'justify-content:center;color:#777268;font-size:12px;margin-bottom:12px;'
            'padding:12px;text-align:center;box-sizing:border-box;">'
            f"Não foi possível abrir data/{expected}"
            "</div>"
        )

    alt = html.escape(str(local.get("nome") or "Arte pré-histórica"), quote=True)
    return (
        f'<img src="{data_uri}" alt="{alt}" '
        'style="display:block;width:100%;height:210px;object-fit:contain;'
        'margin:0 0 12px 0;background:#E9E5DC;">'
    )


def popup_html(local: dict[str, Any]) -> str:
    def esc(key: str, default: str = "—") -> str:
        return html.escape(str(local.get(key) or default))

    observation = local.get("observacao_datacao")
    observation_html = ""
    if observation:
        observation_html = (
            '<div style="margin-top:9px;padding-top:9px;border-top:1px solid #E3DDD2;'
            'font-size:11px;color:#706B62;line-height:1.35;">'
            f"<strong>Nota de datação:</strong> {html.escape(str(observation))}"
            "</div>"
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
    </div>
    """


def build_map(
    locais: list[dict[str, Any]],
    world_geojson: dict[str, Any],
) -> folium.Map:
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
    regions = sorted({str(item.get("regiao")) for item in locais if item.get("regiao")})
    types = sorted({str(item.get("tipo")) for item in locais if item.get("tipo")})

    st.sidebar.markdown("### Explorar")
    query = st.sidebar.text_input("Pesquisar", placeholder="Local, país ou região")
    selected_regions = st.sidebar.multiselect("Regiões", regions, default=regions)
    selected_types = st.sidebar.multiselect("Tipos", types, default=types)

    max_age = max(int(item.get("antiguidade_referencia_anos") or 0) for item in locais)
    age_limit = st.sidebar.slider(
        "Antiguidade de referência (anos)",
        0,
        max_age,
        (0, max_age),
        step=500,
        help="Filtro auxiliar. A datação exibida no popup é mais completa e pode ter incertezas.",
    )

    normalized_query = normalize(query)
    filtered: list[dict[str, Any]] = []

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

    missing = [item for item in locais if not has_local_image(item)]

    st.sidebar.divider()
    if missing:
        st.sidebar.warning(
            f"{len(missing)} imagem(ns) local(is) não encontrada(s) em data/."
        )
    else:
        st.sidebar.success("42/42 imagens locais encontradas.")

    st.sidebar.caption(
        "As imagens são carregadas diretamente da pasta data/ do projeto. "
        "Nenhuma busca externa de imagens é feita durante o uso do mapa."
    )

    return filtered


def main() -> None:
    database = load_database()
    locais = database.get("locais", [])

    st.markdown(
        '<div class="atlas-kicker">Atlas interativo</div>',
        unsafe_allow_html=True,
    )
    st.title("Arte pré-histórica pelo mundo")
    st.markdown(
        '<div class="atlas-subtitle">Mapa de sítios selecionados de arte rupestre e outras '
        'formas de expressão visual pré-histórica. O mapa-base exibe somente os territórios '
        'e suas divisas, sem nomes geográficos.</div>',
        unsafe_allow_html=True,
    )

    filtered = apply_filters(locais)
    local_image_count = sum(1 for item in locais if has_local_image(item))

    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown(
            f'<div class="atlas-stat"><strong>{len(filtered)}</strong><br>'
            '<span>locais exibidos</span></div>',
            unsafe_allow_html=True,
        )

    with col2:
        st.markdown(
            f'<div class="atlas-stat"><strong>{len(locais)}</strong><br>'
            '<span>locais no banco</span></div>',
            unsafe_allow_html=True,
        )

    with col3:
        st.markdown(
            f'<div class="atlas-stat"><strong>{local_image_count}/{len(locais)}</strong><br>'
            '<span>imagens locais</span></div>',
            unsafe_allow_html=True,
        )

    if not filtered:
        st.warning("Nenhum local corresponde aos filtros atuais.")
        return

    try:
        world_geojson = load_world_geojson()
    except Exception as exc:
        st.error(
            "Não foi possível carregar as fronteiras dos países. "
            "Verifique a conexão e tente novamente."
        )
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
        'As fotografias são lidas diretamente dos arquivos JPG presentes em '
        '<strong>data/</strong> e incorporadas aos popups do mapa.</div>',
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
