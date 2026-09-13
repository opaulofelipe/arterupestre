from __future__ import annotations

import copy
import html
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import streamlit as st
from ddgs import DDGS

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
SOURCE_DB = DATA_DIR / "locais.json"
RESOLVED_DB = DATA_DIR / "locais_resolvidos.json"

RIGHTS_OPTIONS = [
    "nao_verificado",
    "livre",
    "permissao_verificada",
    "uso_autorizado",
]
PUBLISHABLE_RIGHTS = {"livre", "permissao_verificada", "uso_autorizado"}

st.set_page_config(
    page_title="Curadoria de imagens — Arte pré-histórica",
    page_icon="◉",
    layout="wide",
)


def load_base_database() -> dict[str, Any]:
    path = RESOLVED_DB if RESOLVED_DB.exists() else SOURCE_DB
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    if isinstance(raw, list):
        return {"metadata": {}, "locais": raw}
    return raw


def get_database() -> dict[str, Any]:
    if "database" not in st.session_state:
        st.session_state.database = load_base_database()
    return st.session_state.database


def site_index(database: dict[str, Any], site_id: str) -> int:
    for i, item in enumerate(database["locais"]):
        if item.get("id") == site_id:
            return i
    raise KeyError(site_id)


def default_query(local: dict[str, Any]) -> str:
    image = local.get("imagem") or {}
    existing = (image.get("commons_search") or "").strip()
    if existing:
        return existing
    bits = [
        str(local.get("nome") or ""),
        str(local.get("pais_atual") or ""),
        str(local.get("tipo") or ""),
        "prehistoric art",
    ]
    return " ".join(bit for bit in bits if bit).strip()


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def search_images(query: str, max_results: int = 18) -> list[dict[str, Any]]:
    if not query.strip():
        return []

    with DDGS() as ddgs:
        results = ddgs.images(
            query,
            region="wt-wt",
            safesearch="moderate",
            max_results=max_results,
        )

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()

    for result in results or []:
        image_url = (
            result.get("image")
            or result.get("url")
            or result.get("media")
            or result.get("src")
        )
        thumbnail = (
            result.get("thumbnail")
            or result.get("thumbnail_url")
            or image_url
        )
        page_url = (
            result.get("url")
            if result.get("image") and result.get("url") != result.get("image")
            else result.get("source")
            or result.get("page")
            or result.get("href")
        )
        title = result.get("title") or result.get("name") or "Imagem candidata"
        source = result.get("source") or result.get("provider") or ""

        if not image_url or image_url in seen:
            continue
        seen.add(image_url)

        normalized.append({
            "image_url": str(image_url),
            "thumbnail_url": str(thumbnail) if thumbnail else str(image_url),
            "page_url": str(page_url) if page_url else "",
            "title": str(title),
            "source": str(source),
        })

    return normalized


def safe_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def save_selection(
    local: dict[str, Any],
    candidate: dict[str, Any],
    rights: str,
    author: str,
    license_text: str,
    license_url: str,
    notes: str,
    query: str,
) -> None:
    image = copy.deepcopy(local.get("imagem") or {})
    image.update({
        "thumbnail_url": candidate.get("thumbnail_url") or candidate.get("image_url"),
        "original_url": candidate.get("image_url"),
        "page_url": candidate.get("page_url"),
        "titulo_fonte": candidate.get("title"),
        "fonte_dominio": safe_domain(candidate.get("page_url") or candidate.get("image_url") or ""),
        "autor": author.strip() or None,
        "licenca": license_text.strip() or None,
        "licenca_url": license_url.strip() or None,
        "direitos": rights,
        "notas_curadoria": notes.strip() or None,
        "consulta_web": query,
        "status": "resolvido_curado" if rights in PUBLISHABLE_RIGHTS else "pendente_direitos",
    })
    local["imagem"] = image


def mark_no_image(local: dict[str, Any], query: str) -> None:
    image = copy.deepcopy(local.get("imagem") or {})
    image.update({
        "thumbnail_url": None,
        "original_url": None,
        "page_url": None,
        "direitos": "nao_verificado",
        "status": "sem_imagem_confiavel",
        "consulta_web": query,
    })
    local["imagem"] = image


def json_bytes(database: dict[str, Any]) -> bytes:
    return json.dumps(database, ensure_ascii=False, indent=2).encode("utf-8")


database = get_database()
locais = database.get("locais", [])

st.title("Curadoria de imagens")
st.caption(
    "A busca usa a web inteira apenas para encontrar candidatas. "
    "Nenhuma imagem é considerada publicável automaticamente."
)

uploaded = st.file_uploader(
    "Continuar a partir de um locais_resolvidos.json",
    type=["json"],
)
if uploaded is not None:
    try:
        uploaded_db = json.loads(uploaded.getvalue().decode("utf-8"))
        if isinstance(uploaded_db, list):
            uploaded_db = {"metadata": {}, "locais": uploaded_db}
        st.session_state.database = uploaded_db
        database = uploaded_db
        locais = database.get("locais", [])
        st.success("Banco carregado.")
    except Exception as exc:
        st.error(f"Não foi possível ler o JSON: {exc}")

status_filter = st.sidebar.selectbox(
    "Mostrar",
    [
        "Todos",
        "Pendentes",
        "Resolvidos e publicáveis",
        "Pendentes de direitos",
        "Sem imagem confiável",
    ],
)


def matches_status(local: dict[str, Any]) -> bool:
    image = local.get("imagem") or {}
    status = str(image.get("status") or "pendente")
    rights = str(image.get("direitos") or "nao_verificado")

    if status_filter == "Todos":
        return True
    if status_filter == "Pendentes":
        return status in {"", "pendente"} or not image.get("original_url")
    if status_filter == "Resolvidos e publicáveis":
        return status == "resolvido_curado" and rights in PUBLISHABLE_RIGHTS
    if status_filter == "Pendentes de direitos":
        return status == "pendente_direitos"
    if status_filter == "Sem imagem confiável":
        return status == "sem_imagem_confiavel"
    return True


eligible = [item for item in locais if matches_status(item)]
if not eligible:
    st.info("Nenhum local corresponde ao filtro atual.")
    st.stop()

labels = {
    item["id"]: f"{item.get('nome')} — {item.get('pais_atual')}"
    for item in eligible
}
site_id = st.sidebar.selectbox(
    "Local",
    list(labels.keys()),
    format_func=lambda key: labels[key],
)
idx = site_index(database, site_id)
local = database["locais"][idx]
current_image = local.get("imagem") or {}

st.subheader(local.get("nome", "Local"))
st.write(
    f"**País atual:** {local.get('pais_atual', '—')}  \n"
    f"**Tipo:** {local.get('tipo', '—')}  \n"
    f"**Datação:** {local.get('idade_aproximada', '—')}"
)

if current_image.get("original_url"):
    st.info(
        f"Estado atual: **{current_image.get('status', '—')}** · "
        f"direitos: **{current_image.get('direitos', 'não informado')}**"
    )
    try:
        st.image(
            current_image.get("thumbnail_url") or current_image.get("original_url"),
            width=420,
            caption="Imagem atualmente salva",
        )
    except Exception:
        st.warning("A imagem atualmente salva não pôde ser exibida.")

query = st.text_input(
    "Consulta de busca",
    value=default_query(local),
    key=f"query_{site_id}",
)

c1, c2 = st.columns([1, 1])
with c1:
    search_now = st.button("Pesquisar imagens", type="primary", use_container_width=True)
with c2:
    if st.button("Marcar como sem imagem confiável", use_container_width=True):
        mark_no_image(local, query)
        st.success("Marcado. Baixe o JSON atualizado ao terminar.")

if search_now:
    with st.spinner("Pesquisando imagens na web…"):
        try:
            st.session_state[f"results_{site_id}"] = search_images(query)
        except Exception as exc:
            st.error(f"A busca falhou: {exc}")
            st.session_state[f"results_{site_id}"] = []

results = st.session_state.get(f"results_{site_id}", [])

if results:
    st.markdown("### Candidatas")
    st.caption(
        "Confira a página-fonte antes de salvar. A imagem ser correta visualmente "
        "não significa que sua reutilização esteja autorizada."
    )

    cols = st.columns(3)
    for i, candidate in enumerate(results):
        with cols[i % 3]:
            try:
                st.image(candidate["thumbnail_url"], use_container_width=True)
            except Exception:
                st.warning("Miniatura indisponível")

            title = html.escape(candidate.get("title") or "Imagem candidata")
            st.markdown(f"**{title[:120]}**")
            domain = safe_domain(candidate.get("page_url") or candidate.get("image_url") or "")
            if domain:
                st.caption(domain)
            if candidate.get("page_url"):
                st.link_button("Abrir fonte", candidate["page_url"], use_container_width=True)

            with st.expander("Selecionar esta imagem"):
                rights = st.selectbox(
                    "Situação dos direitos",
                    RIGHTS_OPTIONS,
                    index=0,
                    key=f"rights_{site_id}_{i}",
                    help=(
                        "'nao_verificado' mantém a imagem fora do mapa público. "
                        "Use outra opção somente após confirmar a situação."
                    ),
                )
                author = st.text_input("Autor / crédito", key=f"author_{site_id}_{i}")
                license_text = st.text_input("Licença / permissão", key=f"license_{site_id}_{i}")
                license_url = st.text_input("Link da licença ou autorização", key=f"license_url_{site_id}_{i}")
                notes = st.text_area(
                    "Notas da curadoria",
                    key=f"notes_{site_id}_{i}",
                    placeholder="Ex.: painel correto; imagem mostra o motivo; conferir licença.",
                )

                if st.button("Salvar candidata", key=f"save_{site_id}_{i}", use_container_width=True):
                    save_selection(
                        local,
                        candidate,
                        rights,
                        author,
                        license_text,
                        license_url,
                        notes,
                        query,
                    )
                    st.success("Imagem salva na sessão. Exporte o JSON abaixo.")

st.divider()

published = 0
rights_pending = 0
no_image = 0
for item in database.get("locais", []):
    image = item.get("imagem") or {}
    if image.get("status") == "resolvido_curado" and image.get("direitos") in PUBLISHABLE_RIGHTS:
        published += 1
    elif image.get("status") == "pendente_direitos":
        rights_pending += 1
    elif image.get("status") == "sem_imagem_confiavel":
        no_image += 1

m1, m2, m3 = st.columns(3)
m1.metric("Publicáveis", published)
m2.metric("Direitos pendentes", rights_pending)
m3.metric("Sem imagem confiável", no_image)

st.download_button(
    "Baixar locais_resolvidos.json",
    data=json_bytes(database),
    file_name="locais_resolvidos.json",
    mime="application/json",
    type="primary",
    use_container_width=True,
)

st.caption(
    "No Streamlit Cloud, use o download para persistir a curadoria. "
    "Depois envie data/locais_resolvidos.json ao repositório."
)
