#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
image_resolver.py
=================

Resolve imagens para os locais de ``locais.json`` usando o Wikimedia Commons.

O script:
- lê o JSON de locais;
- respeita ``commons_file`` quando uma imagem já foi escolhida manualmente;
- pesquisa ``commons_search`` quando ainda não existe arquivo escolhido;
- obtém miniatura, URL original, página do Commons, autor e licença;
- pontua candidatos para reduzir mapas, logos, fachadas e paisagens;
- usa cache local para evitar chamadas repetidas;
- preserva o arquivo original por padrão, gerando ``locais_resolvidos.json``;
- permite curadoria interativa pelo terminal;
- salva progresso após cada local para não perder trabalho.

Dependência:
    pip install requests

Uso recomendado:
    python image_resolver.py --contact "https://github.com/SEU_USUARIO/SEU_REPOSITORIO"

Exemplo com pastas:
    python image_resolver.py --input data/locais.json --output data/locais_resolvidos.json \
        --contact "https://github.com/SEU_USUARIO/SEU_REPOSITORIO"

Modo de curadoria:
    python image_resolver.py --interactive \
        --contact "https://github.com/SEU_USUARIO/SEU_REPOSITORIO"

Importante:
A seleção automática ajuda, mas não substitui uma revisão humana. Em projetos
históricos, confira se a imagem realmente corresponde ao sítio e ao vestígio.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote

try:
    import requests
except ImportError:
    print(
        "Erro: a biblioteca 'requests' não está instalada.\n"
        "Instale com:\n\n"
        "    pip install requests\n",
        file=sys.stderr,
    )
    raise SystemExit(1)


COMMONS_API = "https://commons.wikimedia.org/w/api.php"
DEFAULT_THUMB_WIDTH = 900
DEFAULT_CANDIDATES = 8
DEFAULT_DELAY = 0.25
MAX_RETRIES = 4

NEGATIVE_TERMS = {
    "map": 18,
    "locator": 22,
    "location": 10,
    "flag": 20,
    "logo": 20,
    "icon": 16,
    "diagram": 10,
    "scheme": 10,
    "plan": 8,
    "museum": 7,
    "entrance": 9,
    "visitor": 8,
    "sign": 10,
    "signage": 10,
    "road": 8,
    "parking": 10,
    "panorama": 8,
    "landscape": 6,
    "aerial": 7,
    "satellite": 18,
    "reconstruction": 10,
    "replica": 12,
    "facsimile": 8,
    "stamp": 12,
    "coin": 12,
}

POSITIVE_TERMS = {
    "rock art": 15,
    "cave art": 15,
    "cave painting": 16,
    "painting": 10,
    "paintings": 10,
    "pictograph": 13,
    "pictographs": 13,
    "petroglyph": 15,
    "petroglyphs": 15,
    "engraving": 12,
    "engravings": 12,
    "engraved": 10,
    "stencil": 12,
    "stencils": 12,
    "mural": 10,
    "murals": 10,
    "ochre": 12,
    "ocre": 12,
    "prehistoric": 8,
    "prehistory": 7,
    "paleolithic": 8,
    "palaeolithic": 8,
    "neolithic": 6,
    "artifact": 7,
    "artefact": 7,
}

STOPWORDS = {
    "a", "an", "and", "art", "cave", "da", "das", "de", "del", "do", "dos",
    "e", "el", "en", "et", "la", "las", "le", "les", "of", "rock", "the",
    "prehistoric", "painting", "paintings", "petroglyph", "petroglyphs",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve imagens do Wikimedia Commons para os locais do JSON."
    )
    parser.add_argument(
        "--input", "-i", type=Path,
        help="Arquivo JSON de entrada. Se omitido, procura data/locais.json e locais.json.",
    )
    parser.add_argument(
        "--output", "-o", type=Path,
        help="Arquivo de saída. Padrão: locais_resolvidos.json ao lado do arquivo de entrada.",
    )
    parser.add_argument(
        "--in-place", action="store_true",
        help="Atualiza o próprio arquivo de entrada.",
    )
    parser.add_argument(
        "--contact", default=os.getenv("WIKIMEDIA_CONTACT"),
        help=(
            "Contato para o User-Agent exigido pela Wikimedia. "
            "Ex.: URL do repositório, página pessoal ou mailto:email."
        ),
    )
    parser.add_argument(
        "--interactive", action="store_true",
        help="Mostra os candidatos e pede uma escolha manual para cada local.",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="Refaz locais que já estejam com status resolvido.",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Processa no máximo N locais; útil para testes.",
    )
    parser.add_argument(
        "--candidates", type=int, default=DEFAULT_CANDIDATES,
        help=f"Quantidade de candidatos por busca (padrão: {DEFAULT_CANDIDATES}).",
    )
    parser.add_argument(
        "--thumb-width", type=int, default=DEFAULT_THUMB_WIDTH,
        help=f"Largura da miniatura em pixels (padrão: {DEFAULT_THUMB_WIDTH}).",
    )
    parser.add_argument(
        "--delay", type=float, default=DEFAULT_DELAY,
        help=f"Pausa entre chamadas à API em segundos (padrão: {DEFAULT_DELAY}).",
    )
    parser.add_argument(
        "--cache", type=Path, default=Path("cache") / "imagens.json",
        help="Arquivo de cache (padrão: cache/imagens.json).",
    )
    parser.add_argument(
        "--no-cache", action="store_true",
        help="Não lê nem grava cache.",
    )
    return parser.parse_args()


def locate_input(explicit: Optional[Path]) -> Path:
    if explicit:
        path = explicit.expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"Arquivo de entrada não encontrado: {path}")
        return path

    script_dir = Path(__file__).resolve().parent
    candidates = [
        Path.cwd() / "data" / "locais.json",
        Path.cwd() / "locais.json",
        script_dir / "data" / "locais.json",
        script_dir / "locais.json",
    ]

    seen = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        "Não encontrei 'locais.json'. Use --input CAMINHO/locais.json."
    )


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = re.sub(r"[_\-–—]+", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def strip_html(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def ext_value(extmetadata: Dict[str, Any], key: str) -> str:
    item = extmetadata.get(key, {})
    if isinstance(item, dict):
        return strip_html(item.get("value", ""))
    return strip_html(item)


def canonical_file_title(title: str) -> str:
    title = (title or "").strip()
    if not title:
        return ""
    if not title.lower().startswith("file:"):
        title = "File:" + title
    return title


def commons_page_url(file_title: str) -> str:
    title = canonical_file_title(file_title).replace(" ", "_")
    return "https://commons.wikimedia.org/wiki/" + quote(title, safe=":()'_,.-")


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_cache(path: Path, disabled: bool) -> Dict[str, Any]:
    if disabled or not path.exists():
        return {}
    try:
        data = load_json(path)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_cache(path: Path, cache: Dict[str, Any], disabled: bool) -> None:
    if not disabled:
        atomic_write_json(path, cache)


class CommonsClient:
    def __init__(self, contact: str, thumb_width: int, delay: float, candidates: int) -> None:
        self.thumb_width = max(300, min(int(thumb_width), 2400))
        self.delay = max(0.0, float(delay))
        self.candidates = max(1, min(int(candidates), 20))
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                f"ArteRupestreMap/1.0 ({contact}) "
                f"Python-requests/{requests.__version__}"
            ),
            "Accept": "application/json",
        })

    def request(self, params: Dict[str, Any]) -> Dict[str, Any]:
        base = {
            "format": "json",
            "formatversion": 2,
            "maxlag": 5,
        }
        base.update(params)

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self.session.get(COMMONS_API, params=base, timeout=(8, 35))

                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < MAX_RETRIES:
                        wait = min(2 ** attempt, 10)
                        print(
                            f"  API temporariamente indisponível "
                            f"({response.status_code}); nova tentativa em {wait}s."
                        )
                        time.sleep(wait)
                        continue

                response.raise_for_status()
                payload = response.json()

                if "error" in payload:
                    code = payload["error"].get("code", "erro_desconhecido")
                    info = payload["error"].get("info", "")
                    if code == "maxlag" and attempt < MAX_RETRIES:
                        time.sleep(min(2 ** attempt, 10))
                        continue
                    raise RuntimeError(f"API Wikimedia: {code}: {info}")

                if self.delay:
                    time.sleep(self.delay)
                return payload

            except (requests.RequestException, ValueError) as exc:
                if attempt >= MAX_RETRIES:
                    raise RuntimeError(f"Falha na API Wikimedia: {exc}") from exc
                time.sleep(min(2 ** attempt, 10))

        raise RuntimeError("Falha inesperada na API Wikimedia.")

    def search_file_titles(self, query: str) -> List[str]:
        payload = self.request({
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srnamespace": 6,
            "srlimit": self.candidates,
            "srprop": "",
        })
        items = payload.get("query", {}).get("search", [])
        return [
            canonical_file_title(item.get("title", ""))
            for item in items
            if item.get("title")
        ]

    def fetch_files(self, titles: Iterable[str]) -> List[Dict[str, Any]]:
        titles = [canonical_file_title(x) for x in titles if x]
        if not titles:
            return []

        payload = self.request({
            "action": "query",
            "prop": "imageinfo",
            "titles": "|".join(titles),
            "iiprop": "url|size|mime|mediatype|extmetadata",
            "iiurlwidth": self.thumb_width,
            "iiextmetadatalanguage": "en",
            "iiextmetadatafilter": (
                "Artist|Credit|LicenseShortName|LicenseUrl|UsageTerms|"
                "ImageDescription|ObjectName|Categories"
            ),
        })

        pages = payload.get("query", {}).get("pages", [])
        by_title: Dict[str, Dict[str, Any]] = {}

        for page in pages:
            title = page.get("title", "")
            info_list = page.get("imageinfo") or []
            if not title or not info_list:
                continue

            info = info_list[0]
            media_type = (info.get("mediatype") or "").upper()
            mime = (info.get("mime") or "").lower()

            if media_type and media_type not in {"BITMAP", "DRAWING"}:
                continue
            if mime and not mime.startswith("image/"):
                continue

            ext = info.get("extmetadata") or {}
            record = {
                "commons_file": canonical_file_title(title),
                "original_url": info.get("url"),
                "thumbnail_url": info.get("thumburl") or info.get("url"),
                "thumbnail_width": info.get("thumbwidth"),
                "thumbnail_height": info.get("thumbheight"),
                "width": info.get("width"),
                "height": info.get("height"),
                "mime": mime or None,
                "page_url": info.get("descriptionurl") or commons_page_url(title),
                "autor": ext_value(ext, "Artist") or ext_value(ext, "Credit") or None,
                "credito": ext_value(ext, "Credit") or None,
                "licenca": ext_value(ext, "LicenseShortName") or ext_value(ext, "UsageTerms") or None,
                "licenca_url": ext_value(ext, "LicenseUrl") or None,
                "descricao_imagem": ext_value(ext, "ImageDescription") or ext_value(ext, "ObjectName") or None,
                "categorias_commons": ext_value(ext, "Categories") or None,
            }
            by_title[normalize_text(title)] = record

        result = []
        for title in titles:
            record = by_title.get(normalize_text(title))
            if record:
                result.append(record)
        return result

    def fetch_exact_file(self, file_title: str) -> Optional[Dict[str, Any]]:
        records = self.fetch_files([file_title])
        return records[0] if records else None


def query_tokens(query: str) -> List[str]:
    tokens = re.findall(r"[a-z0-9]+", normalize_text(query))
    return [token for token in tokens if len(token) >= 3 and token not in STOPWORDS]


def score_candidate(candidate: Dict[str, Any], query: str, index: int) -> float:
    title = normalize_text(candidate.get("commons_file", ""))
    description = normalize_text(candidate.get("descricao_imagem", ""))
    categories = normalize_text(candidate.get("categorias_commons", ""))
    combined = f"{title} {description} {categories}"

    score = max(0, 12 - index)

    for token in query_tokens(query):
        if token in title:
            score += 7
        elif token in combined:
            score += 2

    for term, weight in POSITIVE_TERMS.items():
        normalized_term = normalize_text(term)
        if normalized_term in title:
            score += weight
        elif normalized_term in combined:
            score += weight * 0.45

    for term, weight in NEGATIVE_TERMS.items():
        normalized_term = normalize_text(term)
        if normalized_term in title:
            score -= weight
        elif normalized_term in combined:
            score -= weight * 0.35

    width = candidate.get("width") or 0
    height = candidate.get("height") or 0
    if width >= 800 and height >= 500:
        score += 3
    if width >= 1600 and height >= 900:
        score += 2
    if width and height and min(width, height) < 250:
        score -= 8

    if candidate.get("licenca"):
        score += 2
    if candidate.get("licenca_url"):
        score += 1

    return round(score, 2)


def ranked_candidates(client: CommonsClient, search_query: str) -> List[Dict[str, Any]]:
    titles = client.search_file_titles(search_query)
    records = client.fetch_files(titles)

    for index, record in enumerate(records):
        record["auto_score"] = score_candidate(record, search_query, index)

    records.sort(key=lambda item: item.get("auto_score", -9999), reverse=True)
    return records


def fallback_queries(local: Dict[str, Any]) -> List[str]:
    imagem = local.get("imagem") or {}
    primary = (imagem.get("commons_search") or "").strip()
    nome = (local.get("nome") or "").strip()
    pais = (local.get("pais_atual") or "").strip()
    tipo = normalize_text(local.get("tipo") or "")

    queries: List[str] = []
    if primary:
        queries.append(primary)

    if nome:
        if "petro" in tipo or "gravur" in tipo:
            queries.append(f'"{nome}" petroglyph')
        elif "pint" in tipo or "pict" in tipo:
            queries.append(f'"{nome}" rock painting')
        else:
            queries.append(f'"{nome}" rock art')

        if pais:
            queries.append(f'"{nome}" {pais}')

    result: List[str] = []
    seen = set()
    for query in queries:
        key = normalize_text(query)
        if query and key not in seen:
            seen.add(key)
            result.append(query)
    return result


def choose_interactively(local: Dict[str, Any], candidates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    print()
    print("=" * 78)
    print(f"{local.get('nome')} — {local.get('pais_atual')}")
    print("=" * 78)

    for idx, item in enumerate(candidates, start=1):
        print(f"\n[{idx}] {item.get('commons_file')}")
        print(f"    score: {item.get('auto_score')}")
        print(f"    licença: {item.get('licenca') or 'não identificada'}")
        print(f"    autor: {item.get('autor') or 'não identificado'}")
        print(f"    página: {item.get('page_url')}")
        if item.get("descricao_imagem"):
            desc = item["descricao_imagem"]
            if len(desc) > 220:
                desc = desc[:217] + "..."
            print(f"    descrição: {desc}")

    print("\n[0] Não escolher imagem para este local.")
    while True:
        raw = input(f"Escolha 0–{len(candidates)}: ").strip()
        if raw.isdigit():
            choice = int(raw)
            if choice == 0:
                return None
            if 1 <= choice <= len(candidates):
                return candidates[choice - 1]
        print("Escolha inválida.")


def cache_key(local: Dict[str, Any], queries: List[str]) -> str:
    return f"{local.get('id', '')}|{' || '.join(queries)}"


def image_is_resolved(local: Dict[str, Any]) -> bool:
    image = local.get("imagem") or {}
    status = normalize_text(image.get("status") or "")
    return bool(
        image.get("commons_file")
        and image.get("thumbnail_url")
        and status.startswith("resolvido")
    )


def merge_image_data(
    local: Dict[str, Any],
    selected: Dict[str, Any],
    status: str,
    source_query: Optional[str],
) -> None:
    image = local.setdefault("imagem", {})
    preserved = {
        "commons_search": image.get("commons_search"),
        "preferencia": image.get("preferencia"),
    }

    image.update({
        "commons_file": selected.get("commons_file"),
        "original_url": selected.get("original_url"),
        "thumbnail_url": selected.get("thumbnail_url"),
        "thumbnail_width": selected.get("thumbnail_width"),
        "thumbnail_height": selected.get("thumbnail_height"),
        "page_url": selected.get("page_url"),
        "autor": selected.get("autor"),
        "credito": selected.get("credito"),
        "licenca": selected.get("licenca"),
        "licenca_url": selected.get("licenca_url"),
        "descricao_imagem": selected.get("descricao_imagem"),
        "auto_score": selected.get("auto_score"),
        "consulta_usada": source_query,
        "status": status,
    })

    for key, value in preserved.items():
        if value is not None:
            image[key] = value


def resolve_one(
    client: CommonsClient,
    local: Dict[str, Any],
    cache: Dict[str, Any],
    use_cache: bool,
    interactive: bool,
    overwrite: bool,
) -> str:
    image = local.setdefault("imagem", {})

    if image_is_resolved(local) and not overwrite:
        return "ja_resolvido"

    # Um commons_file preenchido manualmente tem prioridade sobre qualquer busca.
    explicit_file = image.get("commons_file")
    if explicit_file and not image_is_resolved(local):
        selected = client.fetch_exact_file(explicit_file)
        if selected:
            selected["auto_score"] = None
            merge_image_data(local, selected, status="resolvido_manual", source_query=None)
            return "resolvido_manual"

        image["status"] = "erro_arquivo_manual"
        image["erro"] = f"Arquivo não encontrado no Commons: {explicit_file}"
        return "erro"

    queries = fallback_queries(local)
    if not queries:
        image["status"] = "sem_consulta"
        return "sem_resultado"

    key = cache_key(local, queries)
    candidates: List[Dict[str, Any]] = []

    if use_cache and key in cache and isinstance(cache[key], list):
        candidates = cache[key]

    source_query = queries[0]

    if not candidates:
        for query in queries:
            try:
                candidates = ranked_candidates(client, query)
            except RuntimeError as exc:
                print(f"  Aviso em '{query}': {exc}")
                candidates = []

            if candidates:
                source_query = query
                break

        if use_cache and candidates:
            cache[key] = candidates

    if not candidates:
        image["status"] = "sem_resultado"
        image["consulta_usada"] = source_query
        return "sem_resultado"

    if interactive:
        selected = choose_interactively(local, candidates)
        if selected is None:
            image["status"] = "pendente_revisao"
            image["consulta_usada"] = source_query
            return "pendente_revisao"
        status = "resolvido_curado"
    else:
        selected = candidates[0]
        status = "resolvido_automaticamente"

    merge_image_data(local, selected, status=status, source_query=source_query)
    return status


def main() -> int:
    args = parse_args()

    if not args.contact:
        print(
            "É necessário informar um contato para identificar o script perante "
            "a Wikimedia.\n\n"
            "Exemplo:\n"
            '  python image_resolver.py --contact '
            '"https://github.com/SEU_USUARIO/SEU_REPOSITORIO"\n\n'
            "Você também pode definir a variável WIKIMEDIA_CONTACT.",
            file=sys.stderr,
        )
        return 2

    try:
        input_path = locate_input(args.input)
    except FileNotFoundError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 2

    if args.in_place and args.output:
        print("Erro: use --in-place OU --output, não os dois.", file=sys.stderr)
        return 2

    if args.in_place:
        output_path = input_path
    elif args.output:
        output_path = args.output.expanduser().resolve()
    else:
        output_path = input_path.with_name("locais_resolvidos.json")

    try:
        database = load_json(input_path)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Erro ao ler {input_path}: {exc}", file=sys.stderr)
        return 2

    if isinstance(database, dict):
        locais = database.get("locais")
    elif isinstance(database, list):
        locais = database
    else:
        locais = None

    if not isinstance(locais, list):
        print(
            "Erro: o JSON precisa conter uma lista em 'locais' ou ser uma lista na raiz.",
            file=sys.stderr,
        )
        return 2

    cache_path = args.cache.expanduser().resolve()
    cache = load_cache(cache_path, args.no_cache)

    client = CommonsClient(
        contact=args.contact,
        thumb_width=args.thumb_width,
        delay=args.delay,
        candidates=args.candidates,
    )

    total = len(locais)
    max_items = total if args.limit is None else min(max(args.limit, 0), total)
    counters: Dict[str, int] = {}

    print(f"Entrada : {input_path}")
    print(f"Saída   : {output_path}")
    print(f"Locais  : {total}")
    print(f"Processar nesta execução: {max_items}\n")

    processed = 0

    try:
        for index, local in enumerate(locais, start=1):
            if processed >= max_items:
                break

            name = local.get("nome") or local.get("id") or f"Local {index}"
            print(f"[{index}/{total}] {name}")

            try:
                result = resolve_one(
                    client=client,
                    local=local,
                    cache=cache,
                    use_cache=not args.no_cache,
                    interactive=args.interactive,
                    overwrite=args.overwrite,
                )
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                result = "erro"
                image = local.setdefault("imagem", {})
                image["status"] = "erro"
                image["erro"] = str(exc)
                print(f"  Erro: {exc}")

            counters[result] = counters.get(result, 0) + 1
            processed += 1

            image = local.get("imagem") or {}
            if result.startswith("resolvido"):
                print(f"  ✓ {result}: {image.get('commons_file')}")
            elif result == "ja_resolvido":
                print("  ↷ já resolvido; mantido.")
            elif result == "sem_resultado":
                print("  ! nenhum arquivo adequado encontrado.")
            elif result == "pendente_revisao":
                print("  → deixado para revisão.")
            else:
                print(f"  ! status: {result}")

            atomic_write_json(output_path, database)
            save_cache(cache_path, cache, args.no_cache)

    except KeyboardInterrupt:
        print("\nInterrompido pelo usuário. Salvando progresso...")
        atomic_write_json(output_path, database)
        save_cache(cache_path, cache, args.no_cache)
        return 130

    if isinstance(database, dict):
        metadata = database.setdefault("metadata", {})
        metadata["arquivo_imagens_resolvido"] = True
        metadata["resolucao_imagens"] = {
            "servico": "Wikimedia Commons",
            "miniatura_largura_px": client.thumb_width,
            "modo": "interativo" if args.interactive else "automatico",
            "observacao": (
                "Imagens selecionadas automaticamente devem ser revisadas "
                "antes da publicação definitiva."
            ),
        }

    atomic_write_json(output_path, database)
    save_cache(cache_path, cache, args.no_cache)

    print("\nConcluído.")
    print(f"Arquivo gerado: {output_path}")
    print("Resumo:")
    for status, count in sorted(counters.items()):
        print(f"  {status}: {count}")

    print(
        "\nRecomendação: revise os links das imagens antes de publicar o mapa, "
        "principalmente os itens marcados como 'resolvido_automaticamente'."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
