from __future__ import annotations

import argparse
import html
import io
import json
import math
import os
import re
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

import requests
from PIL import Image
from ddgs import DDGS

try:
    import torch
    import open_clip
except ImportError:
    torch = None
    open_clip = None

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
OPENVERSE_API = "https://api.openverse.org/v1/images/"
DEFAULT_THUMB_WIDTH = 900
DEFAULT_DELAY = 0.2
MAX_RETRIES = 4
DEFAULT_CANDIDATES = 20
DEFAULT_DOWNLOAD_CANDIDATES = 10
DEFAULT_QUERY_VARIANTS = 5
DEFAULT_CLIP_MODEL = "ViT-B-32"
DEFAULT_CLIP_PRETRAINED = "laion2b_s34b_b79k"

NEGATIVE_TERMS = {
    "map": 16,
    "locator": 22,
    "location": 10,
    "flag": 20,
    "logo": 20,
    "icon": 16,
    "diagram": 12,
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
    "aerial": 8,
    "satellite": 18,
    "reconstruction": 10,
    "replica": 12,
    "facsimile": 10,
    "building": 10,
    "exterior": 8,
    "unesco": 4,
    "tourist": 12,
    "tourism": 12,
    "scenery": 10,
    "mountain": 6,
    "desert": 5,
    "cave exterior": 12,
    "visitor center": 14,
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
    "ceramic": 8,
    "pottery": 8,
}

STOPWORDS = {
    "a", "an", "and", "art", "cave", "da", "das", "de", "del", "do", "dos",
    "e", "el", "en", "et", "la", "las", "le", "les", "of", "rock", "the",
    "prehistoric", "painting", "paintings", "petroglyph", "petroglyphs", "arte",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Resolve imagens de arte pré-histórica usando Openverse + Wikimedia Commons + OpenCLIP. "
            "O script escolhe automaticamente a candidata mais adequada e grava locais_resolvidos.json."
        )
    )
    parser.add_argument("--input", "-i", type=Path, help="Arquivo JSON de entrada.")
    parser.add_argument("--output", "-o", type=Path, help="Arquivo JSON de saída.")
    parser.add_argument("--in-place", action="store_true", help="Atualiza o próprio arquivo de entrada.")
    parser.add_argument(
        "--contact",
        default=os.getenv("WIKIMEDIA_CONTACT") or "https://github.com/opaulofelipe/arterupestre",
        help="Contato/URL para o User-Agent usado nas APIs.",
    )
    parser.add_argument("--overwrite", action="store_true", help="Refaz locais já resolvidos.")
    parser.add_argument("--limit", type=int, default=None, help="Processa no máximo N locais.")
    parser.add_argument("--cache", type=Path, default=Path("cache") / "resolver_cache.json")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--thumb-width", type=int, default=DEFAULT_THUMB_WIDTH)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    parser.add_argument("--candidates", type=int, default=DEFAULT_CANDIDATES)
    parser.add_argument("--download-candidates", type=int, default=DEFAULT_DOWNLOAD_CANDIDATES)
    parser.add_argument("--query-variants", type=int, default=DEFAULT_QUERY_VARIANTS)
    parser.add_argument(
        "--disable-clip",
        action="store_true",
        help="Desliga a análise visual com OpenCLIP e usa apenas pontuação textual.",
    )
    parser.add_argument(
        "--device",
        default=None,
        help="Dispositivo do OpenCLIP (ex.: cpu, cuda). O padrão escolhe automaticamente.",
    )
    return parser.parse_args()


def locate_input(explicit: Path | None) -> Path:
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
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate.exists():
            return candidate
    raise FileNotFoundError("Não encontrei locais.json. Use --input CAMINHO/locais.json")


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_cache(path: Path, disabled: bool) -> dict[str, Any]:
    if disabled or not path.exists():
        return {}
    try:
        data = load_json(path)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_cache(path: Path, cache: dict[str, Any], disabled: bool) -> None:
    if not disabled:
        atomic_write_json(path, cache)


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


def ext_value(extmetadata: dict[str, Any], key: str) -> str:
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


def commons_license_url(short_name: str | None) -> str | None:
    if not short_name:
        return None
    key = short_name.lower().strip()
    mapping = {
        "cc0": "https://creativecommons.org/publicdomain/zero/1.0/",
        "cc by": "https://creativecommons.org/licenses/by/4.0/",
        "cc-by": "https://creativecommons.org/licenses/by/4.0/",
        "cc by-sa": "https://creativecommons.org/licenses/by-sa/4.0/",
        "cc-by-sa": "https://creativecommons.org/licenses/by-sa/4.0/",
        "by": "https://creativecommons.org/licenses/by/4.0/",
        "by-sa": "https://creativecommons.org/licenses/by-sa/4.0/",
        "pdm": "https://creativecommons.org/publicdomain/mark/1.0/",
    }
    return mapping.get(key)


def score_text_presence(text: str, terms: dict[str, int], factor_on_combined: float = 0.45) -> float:
    score = 0.0
    normalized_text = normalize_text(text)
    for term, weight in terms.items():
        normalized_term = normalize_text(term)
        if normalized_term in normalized_text:
            score += weight * factor_on_combined
    return score


def query_tokens(query: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", normalize_text(query))
    return [token for token in tokens if len(token) >= 3 and token not in STOPWORDS]


def base_text_score(candidate: dict[str, Any], query: str, rank_index: int) -> float:
    title = normalize_text(candidate.get("title") or candidate.get("commons_file") or "")
    desc = normalize_text(candidate.get("description") or candidate.get("descricao_imagem") or "")
    source = normalize_text(candidate.get("source") or candidate.get("provider") or "")
    combined = " ".join([title, desc, source])

    score = max(0.0, 14.0 - float(rank_index))
    for token in query_tokens(query):
        if token in title:
            score += 6.5
        elif token in combined:
            score += 2.2

    for term, weight in POSITIVE_TERMS.items():
        term_n = normalize_text(term)
        if term_n in title:
            score += weight
        elif term_n in combined:
            score += weight * 0.45

    for term, weight in NEGATIVE_TERMS.items():
        term_n = normalize_text(term)
        if term_n in title:
            score -= weight
        elif term_n in combined:
            score -= weight * 0.35

    width = candidate.get("width") or 0
    height = candidate.get("height") or 0
    try:
        width = int(width)
        height = int(height)
    except Exception:
        width = height = 0

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
    return round(score, 3)


class HttpClient:
    def __init__(self, user_agent: str, delay: float = DEFAULT_DELAY):
        self.delay = max(0.0, float(delay))
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept": "application/json, text/plain;q=0.9, */*;q=0.8",
        })

    def get_json(self, url: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.session.get(url, params=params, timeout=(10, 40), headers=headers)
                if resp.status_code == 429 or resp.status_code >= 500:
                    if attempt < MAX_RETRIES:
                        time.sleep(min(2**attempt, 8))
                        continue
                resp.raise_for_status()
                data = resp.json()
                if self.delay:
                    time.sleep(self.delay)
                return data
            except Exception:
                if attempt >= MAX_RETRIES:
                    raise
                time.sleep(min(2**attempt, 8))
        raise RuntimeError("Falha HTTP inesperada")

    def get_bytes(self, url: str, *, headers: dict[str, str] | None = None, max_bytes: int = 12_000_000) -> bytes | None:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.session.get(url, timeout=(10, 45), headers=headers, stream=True)
                if resp.status_code == 429 or resp.status_code >= 500:
                    if attempt < MAX_RETRIES:
                        time.sleep(min(2**attempt, 8))
                        continue
                resp.raise_for_status()
                total = 0
                chunks = []
                for chunk in resp.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        return None
                    chunks.append(chunk)
                if self.delay:
                    time.sleep(self.delay)
                return b"".join(chunks)
            except Exception:
                if attempt >= MAX_RETRIES:
                    return None
                time.sleep(min(2**attempt, 8))
        return None


class CommonsSource:
    def __init__(self, client: HttpClient, thumb_width: int, candidates: int) -> None:
        self.client = client
        self.thumb_width = thumb_width
        self.candidates = max(1, min(int(candidates), 50))

    def search(self, query: str) -> list[dict[str, Any]]:
        payload = self.client.get_json(
            COMMONS_API,
            params={
                "action": "query",
                "generator": "search",
                "gsrsearch": query,
                "gsrnamespace": 6,
                "gsrlimit": self.candidates,
                "prop": "imageinfo",
                "iiprop": "url|size|mime|mediatype|extmetadata",
                "iiurlwidth": self.thumb_width,
                "iiextmetadatalanguage": "en",
                "iiextmetadatafilter": (
                    "Artist|Credit|LicenseShortName|LicenseUrl|UsageTerms|"
                    "ImageDescription|ObjectName|Categories"
                ),
                "format": "json",
                "formatversion": 2,
                "maxlag": 5,
            },
        )
        pages = payload.get("query", {}).get("pages", [])
        results = []
        for page in pages:
            info_list = page.get("imageinfo") or []
            if not info_list:
                continue
            info = info_list[0]
            mime = (info.get("mime") or "").lower()
            mediatype = (info.get("mediatype") or "").upper()
            if mime and not mime.startswith("image/"):
                continue
            if mediatype and mediatype not in {"BITMAP", "DRAWING"}:
                continue
            ext = info.get("extmetadata") or {}
            results.append({
                "source_kind": "commons",
                "title": page.get("title") or "",
                "commons_file": canonical_file_title(page.get("title") or ""),
                "thumbnail_url": info.get("thumburl") or info.get("url"),
                "original_url": info.get("url"),
                "page_url": info.get("descriptionurl") or commons_page_url(page.get("title") or ""),
                "width": info.get("width"),
                "height": info.get("height"),
                "autor": ext_value(ext, "Artist") or ext_value(ext, "Credit") or None,
                "credito": ext_value(ext, "Credit") or None,
                "licenca": ext_value(ext, "LicenseShortName") or ext_value(ext, "UsageTerms") or None,
                "licenca_url": ext_value(ext, "LicenseUrl") or None,
                "description": ext_value(ext, "ImageDescription") or ext_value(ext, "ObjectName") or None,
                "source": "Wikimedia Commons",
                "provider": "commons",
            })
        return results


class OpenverseSource:
    def __init__(self, client: HttpClient, candidates: int) -> None:
        self.client = client
        self.candidates = max(1, min(int(candidates), 50))

    def search(self, query: str) -> list[dict[str, Any]]:
        payload = self.client.get_json(
            OPENVERSE_API,
            params={
                "q": query,
                "page_size": self.candidates,
                "mature": "false",
            },
            headers={"Accept": "application/json"},
        )
        results = []
        for item in payload.get("results", []) or []:
            thumb = item.get("thumbnail") or item.get("url")
            if not thumb:
                continue
            creator = item.get("creator") or item.get("foreign_landing_url") or None
            lic = item.get("license")
            lic_ver = item.get("license_version")
            lic_display = None
            if lic:
                lic_display = lic.upper()
                if lic_ver:
                    lic_display = f"{lic_display} {lic_ver}"
            results.append({
                "source_kind": "openverse",
                "title": item.get("title") or item.get("id") or "Imagem Openverse",
                "thumbnail_url": thumb,
                "original_url": item.get("url") or thumb,
                "page_url": item.get("foreign_landing_url") or item.get("detail_url") or item.get("url"),
                "width": item.get("width"),
                "height": item.get("height"),
                "autor": creator,
                "credito": creator,
                "licenca": lic_display,
                "licenca_url": item.get("license_url") or commons_license_url(item.get("license")),
                "description": item.get("title") or item.get("creator") or None,
                "source": item.get("source") or "Openverse",
                "provider": item.get("source") or "openverse",
            })
        return results


class WebImageSource:
    """Fallback amplo de imagens da web via DDGS.

    Estes resultados podem não ter licença aberta. O resolvedor registra a
    página-fonte e marca os direitos como não verificados automaticamente.
    """

    def __init__(self, candidates: int) -> None:
        self.candidates = max(1, min(int(candidates), 40))

    def search(self, query: str) -> list[dict[str, Any]]:
        try:
            results = DDGS().images(
                query,
                region="wt-wt",
                safesearch="moderate",
                max_results=self.candidates,
            ) or []
        except Exception:
            return []

        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in results:
            image_url = item.get("image") or item.get("media") or item.get("src")
            thumb = item.get("thumbnail") or image_url
            page_url = item.get("url") or item.get("source") or item.get("page")
            if not image_url or image_url in seen:
                continue
            seen.add(str(image_url))
            normalized.append({
                "source_kind": "web",
                "title": item.get("title") or "Imagem da web",
                "thumbnail_url": thumb,
                "original_url": image_url,
                "page_url": page_url,
                "width": item.get("width"),
                "height": item.get("height"),
                "autor": None,
                "credito": None,
                "licenca": None,
                "licenca_url": None,
                "description": item.get("title") or None,
                "source": item.get("source") or "Web",
                "provider": "ddgs",
            })
        return normalized


def build_queries(local: dict[str, Any], max_variants: int) -> list[str]:
    image = local.get("imagem") or {}
    primary = (image.get("commons_search") or image.get("openverse_search") or "").strip()
    nome = str(local.get("nome") or "").strip()
    pais = str(local.get("pais_atual") or "").strip()
    tipo = normalize_text(str(local.get("tipo") or ""))

    target_prompts = image.get("alvo_visual") or []
    if isinstance(target_prompts, str):
        target_prompts = [target_prompts]

    queries: list[str] = []
    if primary:
        queries.append(primary)

    if "petro" in tipo or "gravur" in tipo:
        queries.extend([
            f"{nome} petroglyph",
            f"{nome} rock engraving",
            f"{nome} {pais} rock art",
            f"{nome} prehistoric petroglyph",
        ])
    elif "pint" in tipo or "pict" in tipo or "stencil" in tipo:
        queries.extend([
            f"{nome} rock painting",
            f"{nome} cave art",
            f"{nome} {pais} prehistoric painting",
            f"{nome} prehistoric art",
        ])
    elif "ocre" in tipo or "ochre" in tipo:
        queries.extend([
            f"{nome} engraved ochre",
            f"{nome} prehistoric artifact",
            f"{nome} {pais} archaeology",
        ])
    else:
        queries.extend([
            f"{nome} rock art",
            f"{nome} prehistoric art",
            f"{nome} {pais} archaeology",
        ])

    for prompt in target_prompts:
        prompt = str(prompt).strip()
        if prompt:
            queries.append(f"{nome} {prompt}")
    queries.extend([f"{nome} archaeology", nome])

    result: list[str] = []
    seen: set[str] = set()
    for q in queries:
        q = re.sub(r"\s+", " ", q).strip()
        key = normalize_text(q)
        if q and key not in seen:
            seen.add(key)
            result.append(q)
        if len(result) >= max_variants:
            break
    return result


def visual_prompts_for_local(local: dict[str, Any]) -> tuple[list[str], list[str]]:
    image = local.get("imagem") or {}
    target = image.get("alvo_visual") or []
    if isinstance(target, str):
        target = [target]
    nome = str(local.get("nome") or "")
    tipo = normalize_text(str(local.get("tipo") or ""))

    positives = [
        "prehistoric art object",
        "archaeological artifact",
        "rock art panel",
    ]
    negatives = [
        "landscape",
        "tourist photo",
        "museum building exterior",
        "cave entrance",
        "map",
        "sign",
        "road",
        "empty landscape",
        "person standing in front of cave",
        "mountain landscape",
        "desert landscape",
        "scenic valley",
        "visitor centre",
    ]

    if "petro" in tipo or "gravur" in tipo:
        positives.extend([
            "prehistoric petroglyph",
            "rock engraving",
            "engraved rock art panel",
        ])
    if "pint" in tipo or "pict" in tipo:
        positives.extend([
            "prehistoric cave painting",
            "ancient rock painting",
            "painted rock art panel",
        ])
    if "stencil" in tipo:
        positives.append("hand stencil rock art")
    if "ocre" in tipo or "ochre" in tipo:
        positives.extend([
            "engraved ochre stone",
            "prehistoric engraved ochre artifact",
        ])
    if "ceram" in tipo or "potter" in tipo:
        positives.extend([
            "prehistoric ceramic artifact",
            "archaeological pottery",
        ])

    for item in target:
        item = str(item).strip()
        if item:
            positives.append(item)

    if nome:
        positives.append(f"{nome} prehistoric art")
    return positives, negatives


class ClipRanker:
    def __init__(self, client: HttpClient, device: str | None = None) -> None:
        if torch is None or open_clip is None:
            raise RuntimeError(
                "OpenCLIP não está instalado. Instale open_clip_torch e torch ou use --disable-clip."
            )
        if device:
            self.device = device
        else:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.client = client
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(
            DEFAULT_CLIP_MODEL,
            pretrained=DEFAULT_CLIP_PRETRAINED,
            device=self.device,
        )
        self.tokenizer = open_clip.get_tokenizer(DEFAULT_CLIP_MODEL)
        self.model.eval()

    def image_from_url(self, url: str) -> Image.Image | None:
        raw = self.client.get_bytes(url)
        if not raw:
            return None
        try:
            image = Image.open(io.BytesIO(raw)).convert("RGB")
            return image
        except Exception:
            return None

    def text_features(self, texts: list[str]):
        with torch.no_grad():
            tokens = self.tokenizer(texts).to(self.device)
            features = self.model.encode_text(tokens)
            features = features / features.norm(dim=-1, keepdim=True)
        return features

    def rank(self, candidates: list[dict[str, Any]], local: dict[str, Any], max_downloads: int) -> list[dict[str, Any]]:
        if not candidates:
            return candidates
        positives, negatives = visual_prompts_for_local(local)
        positive_features = self.text_features(positives)
        negative_features = self.text_features(negatives)
        limited = candidates[:max_downloads]
        for item in limited:
            image_url = item.get("thumbnail_url") or item.get("original_url")
            image = self.image_from_url(str(image_url)) if image_url else None
            if image is None:
                item["clip_score"] = None
                continue
            with torch.no_grad():
                tensor = self.preprocess(image).unsqueeze(0).to(self.device)
                image_features = self.model.encode_image(tensor)
                image_features = image_features / image_features.norm(dim=-1, keepdim=True)
                pos_scores = (100.0 * image_features @ positive_features.T).squeeze(0)
                neg_scores = (100.0 * image_features @ negative_features.T).squeeze(0)
                pos = float(pos_scores.max().item())
                neg = float(neg_scores.max().item())
                mean_pos = float(pos_scores.mean().item())
                mean_neg = float(neg_scores.mean().item())
                item["clip_score"] = round((0.65 * pos + 0.35 * mean_pos) - (0.60 * neg + 0.40 * mean_neg), 3)
                item["clip_positive_max"] = round(pos, 3)
                item["clip_negative_max"] = round(neg, 3)
        for item in candidates[max_downloads:]:
            item["clip_score"] = None
        candidates.sort(
            key=lambda x: (
                -9999 if x.get("clip_score") is None else float(x.get("clip_score")),
                float(x.get("text_score") or -9999),
            ),
            reverse=True,
        )
        return candidates


def merged_candidates_for_local(
    local: dict[str, Any],
    queries: list[str],
    openverse: OpenverseSource,
    commons: CommonsSource,
    web_source: WebImageSource,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    def add_items(items: list[dict[str, Any]], query: str) -> None:
        for idx, raw in enumerate(items):
            item = dict(raw)
            item["query_used"] = query
            item["text_score"] = base_text_score(item, query, idx)
            key = item.get("original_url") or item.get("thumbnail_url") or item.get("page_url")
            if not key or str(key) in seen_urls:
                continue
            seen_urls.add(str(key))
            merged.append(item)

    for query in queries:
        try:
            add_items(openverse.search(query), query)
        except Exception:
            pass
        try:
            add_items(commons.search(query), query)
        except Exception:
            pass

    if len(merged) < 8:
        for query in queries:
            try:
                add_items(web_source.search(query), query)
            except Exception:
                pass
            if len(merged) >= 24:
                break

    merged.sort(key=lambda x: float(x.get("text_score") or -9999), reverse=True)
    return merged


def image_is_resolved(local: dict[str, Any]) -> bool:
    image = local.get("imagem") or {}
    status = normalize_text(str(image.get("status") or ""))
    rights = normalize_text(str(image.get("direitos") or ""))
    return bool(
        (image.get("thumbnail_url") or image.get("original_url"))
        and status.startswith("resolvido")
        and rights in {"livre", "permissao_verificada", "uso_autorizado", "aberta_auto", "web_auto"}
    )


def merge_image_data(local: dict[str, Any], selected: dict[str, Any], rights: str = "aberta_auto") -> None:
    image = local.setdefault("imagem", {})
    preserved = {
        "commons_search": image.get("commons_search"),
        "openverse_search": image.get("openverse_search"),
        "preferencia": image.get("preferencia"),
        "alvo_visual": image.get("alvo_visual"),
    }
    image.update({
        "thumbnail_url": selected.get("thumbnail_url"),
        "original_url": selected.get("original_url"),
        "page_url": selected.get("page_url"),
        "autor": selected.get("autor"),
        "credito": selected.get("credito"),
        "licenca": selected.get("licenca"),
        "licenca_url": selected.get("licenca_url"),
        "descricao_imagem": selected.get("description"),
        "source_kind": selected.get("source_kind"),
        "fonte_dominio": selected.get("source"),
        "query_used": selected.get("query_used"),
        "text_score": selected.get("text_score"),
        "clip_score": selected.get("clip_score"),
        "status": "resolvido_automaticamente",
        "direitos": rights,
    })
    if selected.get("source_kind") == "commons":
        image["commons_file"] = selected.get("commons_file")
    for key, value in preserved.items():
        if value is not None:
            image[key] = value


def resolve_one(
    local: dict[str, Any],
    cache: dict[str, Any],
    use_cache: bool,
    openverse: OpenverseSource,
    commons: CommonsSource,
    web_source: WebImageSource,
    clip_ranker: ClipRanker | None,
    query_variants: int,
    max_download_candidates: int,
    overwrite: bool,
) -> str:
    if image_is_resolved(local) and not overwrite:
        return "ja_resolvido"

    queries = build_queries(local, query_variants)
    if not queries:
        local.setdefault("imagem", {})["status"] = "sem_consulta"
        return "sem_resultado"

    cache_key = f"{local.get('id','')}|{'||'.join(queries)}"
    candidates = []
    if use_cache and cache_key in cache:
        cached = cache.get(cache_key)
        if isinstance(cached, list):
            candidates = [dict(item) for item in cached]

    if not candidates:
        candidates = merged_candidates_for_local(local, queries, openverse, commons, web_source)
        if use_cache:
            cache[cache_key] = candidates

    if not candidates:
        image = local.setdefault("imagem", {})
        image["status"] = "sem_resultado"
        image["direitos"] = "nao_verificado"
        return "sem_resultado"

    if clip_ranker is not None:
        try:
            candidates = clip_ranker.rank(candidates, local, max_download_candidates)
        except Exception as exc:
            print(f"  Aviso: falha no OpenCLIP para {local.get('nome')}: {exc}")

    selected = candidates[0]
    rights = "aberta_auto" if selected.get("licenca") or selected.get("licenca_url") else ("web_auto" if selected.get("source_kind") == "web" else "nao_verificado")
    merge_image_data(local, selected, rights=rights)
    return "resolvido_automaticamente"


def main() -> int:
    args = parse_args()
    try:
        input_path = locate_input(args.input)
    except FileNotFoundError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 2

    if args.in_place and args.output:
        print("Erro: use --in-place OU --output, não ambos.", file=sys.stderr)
        return 2
    if args.in_place:
        output_path = input_path
    elif args.output:
        output_path = args.output.expanduser().resolve()
    else:
        output_path = input_path.with_name("locais_resolvidos.json")

    try:
        database = load_json(input_path)
    except Exception as exc:
        print(f"Erro ao ler {input_path}: {exc}", file=sys.stderr)
        return 2

    if isinstance(database, dict):
        locais = database.get("locais")
    elif isinstance(database, list):
        locais = database
        database = {"metadata": {}, "locais": locais}
    else:
        locais = None
    if not isinstance(locais, list):
        print("Erro: o JSON precisa conter uma lista em 'locais' ou ser uma lista na raiz.", file=sys.stderr)
        return 2

    cache_path = args.cache.expanduser().resolve()
    cache = load_cache(cache_path, args.no_cache)

    ua = f"ArteRupestreResolver/2.0 ({args.contact})"
    http_client = HttpClient(ua, delay=args.delay)
    openverse = OpenverseSource(http_client, args.candidates)
    commons = CommonsSource(http_client, args.thumb_width, args.candidates)
    web_source = WebImageSource(max(args.candidates, 20))
    clip_ranker = None
    if not args.disable_clip:
        try:
            clip_ranker = ClipRanker(http_client, device=args.device)
            print(f"OpenCLIP carregado em {clip_ranker.device}.")
        except Exception as exc:
            print(f"Aviso: OpenCLIP indisponível, seguindo apenas com ranking textual. {exc}")
            clip_ranker = None

    total = len(locais)
    max_items = total if args.limit is None else min(max(args.limit, 0), total)
    counters: dict[str, int] = {}

    print(f"Entrada : {input_path}")
    print(f"Saída   : {output_path}")
    print(f"Locais  : {total}")
    print(f"Processar nesta execução: {max_items}")

    processed = 0
    try:
        for index, local in enumerate(locais, start=1):
            if processed >= max_items:
                break
            name = local.get("nome") or local.get("id") or f"Local {index}"
            print(f"[{index}/{total}] {name}")
            try:
                result = resolve_one(
                    local=local,
                    cache=cache,
                    use_cache=not args.no_cache,
                    openverse=openverse,
                    commons=commons,
                    web_source=web_source,
                    clip_ranker=clip_ranker,
                    query_variants=args.query_variants,
                    max_download_candidates=args.download_candidates,
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
                print(
                    f"  ✓ {result}: {image.get('original_url') or image.get('thumbnail_url')} "
                    f"(fonte={image.get('fonte_dominio')}, clip={image.get('clip_score')}, texto={image.get('text_score')})"
                )
            elif result == "ja_resolvido":
                print("  ↷ já resolvido; mantido.")
            else:
                print(f"  ! status: {result}")

            atomic_write_json(output_path, database)
            save_cache(cache_path, cache, args.no_cache)
    except KeyboardInterrupt:
        print("\nInterrompido pelo usuário. Salvando progresso...")
        atomic_write_json(output_path, database)
        save_cache(cache_path, cache, args.no_cache)
        return 130

    metadata = database.setdefault("metadata", {})
    metadata["arquivo_imagens_resolvido"] = True
    metadata["resolucao_imagens"] = {
        "servicos": ["Openverse", "Wikimedia Commons", "Busca web via DDGS"],
        "modo": "automatico_multimodal",
        "clip": "OpenCLIP" if clip_ranker is not None else "desativado",
        "miniatura_largura_px": args.thumb_width,
        "observacao": (
            "A seleção automática privilegia primeiro fontes abertas e usa busca web geral como fallback. "
            "Resultados da web sem licença conhecida são marcados como web_auto e mantêm o link da fonte."
        ),
    }

    atomic_write_json(output_path, database)
    save_cache(cache_path, cache, args.no_cache)

    print("\nConcluído.")
    print(f"Arquivo gerado: {output_path}")
    for status, count in sorted(counters.items()):
        print(f"  {status}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
