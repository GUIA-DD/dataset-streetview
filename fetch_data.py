from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import time
from collections import deque
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import geopandas as gpd
import osmnx as ox
import pandas as pd
import requests
from dotenv import load_dotenv
from shapely.geometry import LineString, MultiLineString

DEFAULT_PLACE_QUERY = "Distrito Federal, Brazil"
DEFAULT_OUTPUT_DIR = Path("datasets/df_2024_proportional")
DEFAULT_CACHE_DIR = Path("cache/df_2024_proportional")
DEFAULT_BATCH_SIZE = 1000
DEFAULT_MIN_YEAR = 2024
DEFAULT_GRID_SIZE_M = 5000.0
DEFAULT_CANDIDATE_MULTIPLIER = 2.0
DEFAULT_IMAGE_WIDTH = 640
DEFAULT_IMAGE_HEIGHT = 640
DEFAULT_IMAGE_FOV = 90
DEFAULT_IMAGE_PITCH = 0
DEFAULT_REQUEST_DELAY_S = 0.05
DEFAULT_REQUEST_TIMEOUT_S = 10.0
DEFAULT_MAX_RETRIES = 5
DEFAULT_RANDOM_SEED = 42
DEFAULT_METADATA_RADIUS_M = 25
DEFAULT_REPORT_EVERY = 250

# URL do repositório para o CSV
DEFAULT_GITHUB_REPO_URL = "https://github.com/GUIA-DD/dataset-streetview"
DEFAULT_GITHUB_BRANCH = "refs/heads/main"

STREETVIEW_METADATA_URL = "https://maps.googleapis.com/maps/api/streetview/metadata"
STREETVIEW_IMAGE_URL = "https://maps.googleapis.com/maps/api/streetview"


def sha1_id(*parts: Any) -> str:
    text = "|".join(map(str, parts))
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def get_github_raw_url(rel_path: str | Path, repo_url: str, branch: str) -> str:
    # Converte https://github.com/user/repo para https://raw.githubusercontent.com/user/repo
    base = repo_url.replace("github.com", "raw.githubusercontent.com")
    return f"{base}/{branch}/{str(rel_path).replace(os.sep, '/')}"


def parse_year(date_value: Any) -> int | None:
    if pd.isna(date_value) or date_value in (None, ""):
        return None
    try:
        return int(str(date_value).split("-")[0])
    except (TypeError, ValueError):
        return None


def as_lines(geometry: Any) -> list[LineString]:
    if geometry is None:
        return []
    if isinstance(geometry, LineString):
        return [geometry]
    if isinstance(geometry, MultiLineString):
        return [line for line in geometry.geoms if isinstance(line, LineString)]
    return []


def compute_heading_at_distance(line: LineString, distance_m: float, delta_m: float = 1.0) -> float:
    if line.length == 0:
        return 0.0

    start = max(0.0, min(distance_m, line.length))
    end = min(line.length, start + delta_m)

    if math.isclose(start, end):
        start = max(0.0, start - delta_m)

    p1 = line.interpolate(start)
    p2 = line.interpolate(end)
    angle_deg = math.degrees(math.atan2(p2.x - p1.x, p2.y - p1.y))
    return angle_deg % 360


def round_robin_by_cell(df: pd.DataFrame, cell_col: str, seed: int) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    rng = random.Random(seed)
    buckets: dict[str, deque[int]] = {}

    for cell_id, group in df.groupby(cell_col, sort=False):
        indices = list(group.index)
        rng.shuffle(indices)
        buckets[str(cell_id)] = deque(indices)

    active_cells = list(buckets.keys())
    rng.shuffle(active_cells)
    queue = deque(active_cells)
    ordered_indices: list[int] = []

    while queue:
        cell_id = queue.popleft()
        bucket = buckets[cell_id]
        ordered_indices.append(bucket.popleft())
        if bucket:
            queue.append(cell_id)

    return df.loc[ordered_indices].reset_index(drop=True)


def add_projected_xy(df: pd.DataFrame, lng_col: str, lat_col: str) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    gdf = gpd.GeoDataFrame(
        df.copy(),
        geometry=gpd.points_from_xy(df[lng_col], df[lat_col]),
        crs="EPSG:4326",
    )
    projected = ox.projection.project_gdf(gdf)
    result = pd.DataFrame(projected.drop(columns="geometry"))
    result["x_m"] = projected.geometry.x
    result["y_m"] = projected.geometry.y
    return result


def add_spatial_cells(df: pd.DataFrame, grid_size_m: float) -> pd.DataFrame:
    if df.empty:
        out = df.copy()
        out["cell_id"] = []
        return out

    out = df.copy()
    min_x = out["x_m"].min()
    min_y = out["y_m"].min()

    cell_x = ((out["x_m"] - min_x) / grid_size_m).astype(int)
    cell_y = ((out["y_m"] - min_y) / grid_size_m).astype(int)

    out["cell_x"] = cell_x
    out["cell_y"] = cell_y
    out["cell_id"] = cell_x.astype(str) + "_" + cell_y.astype(str)
    return out


def get_headings_for_mode(road_heading: float, mode: str, seed_text: str) -> list[tuple[int, str, str]]:
    """Retorna lista de (heading_angle, label, strategy)"""
    if mode == "cardinal":
        return [
            (0, "0_deg_north", "cardinal"),
            (90, "90_deg_east", "cardinal"),
            (180, "180_deg_south", "cardinal"),
            (270, "270_deg_west", "cardinal"),
        ]
    
    # Modos legados (uma unica imagem)
    if mode == "forward":
        angle = road_heading
    elif mode == "backward":
        angle = road_heading + 180
    elif mode == "left":
        angle = road_heading - 90
    elif mode == "right":
        angle = road_heading + 90
    else:
        seed_value = int(sha1_id(seed_text, "heading"), 16) % (2**32)
        angle = random.Random(seed_value).randrange(360)
    
    return [(int(round(angle)) % 360, f"single_{mode}", mode)]


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_place_polygon(place_query: str):
    print(f"Baixando limite administrativo de: {place_query}")
    gdf = ox.geocode_to_gdf(place_query)
    if gdf.empty:
        raise RuntimeError(f"Nenhum poligono encontrado para '{place_query}'.")
    geometry = gdf.unary_union
    return geometry


def build_drive_graph(place_query: str, network_type: str):
    polygon = load_place_polygon(place_query)
    print(f"Baixando malha viaria ({network_type})...")
    return ox.graph_from_polygon(
        polygon,
        network_type=network_type,
        simplify=True,
        retain_all=False,
        truncate_by_edge=True,
    )


def generate_candidate_points(
    graph: Any,
    spacing_m: float | None,
    target_candidate_count: int | None,
    grid_size_m: float,
    seed: int,
    sampling_strategy: str = "uniform",
) -> tuple[pd.DataFrame, float, float]:
    edges = ox.graph_to_gdfs(ox.project_graph(graph), nodes=False, edges=True)
    total_length_m = edges.geometry.length.sum()

    if spacing_m is None:
        if target_candidate_count is None:
            raise ValueError("Informe spacing_m ou target_candidate_count.")
        spacing_m = total_length_m / target_candidate_count

    print(f"Gerando candidatos com espacamento de {spacing_m:.1f} m...")

    records = []
    line_counter = 0
    for row in edges.itertuples():
        lines = as_lines(row.geometry)
        for line in lines:
            current_distance = spacing_m / 2.0
            distances = []
            while current_distance <= line.length:
                distances.append(current_distance)
                current_distance += spacing_m

            if not distances:
                distances = [line.length / 2.0]

            for distance_m in distances:
                point = line.interpolate(distance_m)
                road_heading = compute_heading_at_distance(line, distance_m)
                records.append(
                    {
                        "candidate_id": sha1_id(line_counter, round(point.x, 3), round(point.y, 3)),
                        "x_m": point.x,
                        "y_m": point.y,
                        "road_heading": road_heading,
                        "edge_length_m": float(line.length),
                    }
                )
            line_counter += 1

    if not records:
        raise RuntimeError("Nenhum ponto candidato foi gerado sobre a malha viaria.")

    gdf = gpd.GeoDataFrame(
        records,
        geometry=gpd.points_from_xy([item["x_m"] for item in records], [item["y_m"] for item in records]),
        crs=edges.crs,
    ).to_crs("EPSG:4326")

    candidates = pd.DataFrame(gdf.drop(columns="geometry"))
    candidates["requested_lng"] = gdf.geometry.x
    candidates["requested_lat"] = gdf.geometry.y
    candidates = add_spatial_cells(candidates, grid_size_m)
    
    if sampling_strategy == "uniform":
        print("Aplicando amostragem uniforme (round-robin por celula espacial)...")
        candidates = round_robin_by_cell(candidates, "cell_id", seed)
    elif sampling_strategy == "proportional":
        print("Aplicando amostragem proporcional (embaralhamento global simples)...")
        candidates = candidates.sample(frac=1, random_state=seed).reset_index(drop=True)
    else:
        raise ValueError(f"Estrategia de amostragem invalida: {sampling_strategy}")
        
    return candidates, total_length_m, spacing_m


def fetch_json_with_retry(
    session: requests.Session,
    url: str,
    params: dict[str, Any],
    timeout_s: float,
    max_retries: int,
) -> dict[str, Any]:
    last_error = ""
    for attempt in range(max_retries):
        try:
            response = session.get(url, params=params, timeout=timeout_s)
            if response.status_code == 200:
                return response.json()
            if response.status_code == 429: # Quota
                return {"status": "OVER_QUERY_LIMIT", "message": "Quota exceeded"}
            if response.status_code in {500, 502, 503, 504}:
                time.sleep(min(2**attempt, 10))
                continue
            return {"status": f"HTTP_{response.status_code}", "body": response.text[:200]}
        except requests.RequestException as exc:
            last_error = str(exc)
            time.sleep(min(2**attempt, 10))
    return {"status": "REQUEST_FAILED", "error": last_error}


def load_or_fetch_metadata(
    session: requests.Session,
    cache_dir: Path,
    lat: float,
    lng: float,
    api_key: str,
    timeout_s: float,
    max_retries: int,
    radius_m: int,
) -> tuple[dict[str, Any], bool]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{sha1_id(f'{lat:.7f}', f'{lng:.7f}')}.json"

    if cache_path.exists():
        with cache_path.open("r", encoding="utf-8") as handle:
            return json.load(handle), True

    payload = fetch_json_with_retry(
        session,
        STREETVIEW_METADATA_URL,
        {
            "location": f"{lat:.7f},{lng:.7f}",
            "radius": radius_m,
            "source": "outdoor",
            "key": api_key,
        },
        timeout_s=timeout_s,
        max_retries=max_retries,
    )

    if payload.get("status") in ("OK", "ZERO_RESULTS"):
        with cache_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    return payload, False


def collect_eligible_points(
    candidates: pd.DataFrame,
    api_key: str,
    cache_dir: Path,
    min_year: int,
    max_locations: int | None,
    max_metadata_requests: int | None,
    timeout_s: float,
    request_delay_s: float,
    max_retries: int,
    metadata_radius_m: int,
    report_every: int,
) -> tuple[pd.DataFrame, dict[str, int]]:
    session = requests.Session()
    seen_panos: set[str] = set()
    eligible_records: list[dict[str, Any]] = []
    
    stats = {
        "metadata_processed": 0,
        "metadata_network_calls": 0,
        "metadata_cache_hits": 0,
        "eligible_locations": 0,
        "duplicate_panos_skipped": 0,
        "pre_2024_skipped": 0,
        "metadata_failures": 0,
        "quota_events": 0,
        "quota_stop": 0,
    }

    metadata_cache_dir = cache_dir / "metadata"

    for row in candidates.itertuples(index=False):
        if max_metadata_requests is not None and stats["metadata_processed"] >= max_metadata_requests:
            print(f"Limite de metadata atingido em {max_metadata_requests} consultas.")
            break
        if max_locations is not None and len(eligible_records) >= max_locations:
            break

        metadata, from_cache = load_or_fetch_metadata(
            session=session,
            cache_dir=metadata_cache_dir,
            lat=float(row.requested_lat),
            lng=float(row.requested_lng),
            api_key=api_key,
            timeout_s=timeout_s,
            max_retries=max_retries,
            radius_m=metadata_radius_m,
        )

        stats["metadata_processed"] += 1
        if from_cache:
            stats["metadata_cache_hits"] += 1
        else:
            stats["metadata_network_calls"] += 1
            if metadata.get("status") == "OVER_QUERY_LIMIT":
                print("Quota de metadata excedida. Parando coleta de pontos.")
                stats["quota_events"] += 1
                stats["quota_stop"] = 1
                break
            time.sleep(request_delay_s)

        if metadata.get("status") != "OK":
            stats["metadata_failures"] += 1
            continue

        capture_date = metadata.get("date")
        capture_year = parse_year(capture_date)
        if capture_year is None or capture_year < min_year:
            stats["pre_2024_skipped"] += 1
            continue

        pano_id = metadata.get("pano_id")
        if not pano_id:
            stats["metadata_failures"] += 1
            continue
        if pano_id in seen_panos:
            stats["duplicate_panos_skipped"] += 1
            continue

        location = metadata.get("location") or {}
        pano_lat = safe_float(location.get("lat"), float(row.requested_lat))
        pano_lng = safe_float(location.get("lng"), float(row.requested_lng))

        record = {
            "location_id": sha1_id(pano_id, pano_lat, pano_lng),
            "candidate_id": getattr(row, "candidate_id", ""),
            "requested_lat": float(row.requested_lat),
            "requested_lng": float(row.requested_lng),
            "pano_lat": pano_lat,
            "pano_lng": pano_lng,
            "capture_date": capture_date,
            "capture_year": capture_year,
            "pano_id": pano_id,
            "road_heading": safe_float(getattr(row, "road_heading", 0.0)),
            "copyright": metadata.get("copyright"),
            "metadata_status": metadata.get("status"),
            "cell_id": getattr(row, "cell_id", None),
            "x_m": safe_float(getattr(row, "x_m", 0.0)),
            "y_m": safe_float(getattr(row, "y_m", 0.0)),
        }
        eligible_records.append(record)
        seen_panos.add(pano_id)
        stats["eligible_locations"] = len(eligible_records)

        if len(eligible_records) % report_every == 0:
            print(
                f"Elegiveis: {len(eligible_records)} (Processados: {stats['metadata_processed']})"
            )

    eligible_df = pd.DataFrame(eligible_records)
    return eligible_df, stats


def assign_batches(
    eligible_df: pd.DataFrame,
    heading_set: str,
    batch_size: int,
    seed: int,
    max_images: int | None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if eligible_df.empty:
        raise RuntimeError("Nao ha pontos elegiveis para formar lotes.")

    # Expandir localizacoes para imagens
    image_records = []
    for row in eligible_df.itertuples(index=False):
        headings = get_headings_for_mode(row.road_heading, heading_set, row.location_id)
        for idx, (h_angle, h_label, h_strategy) in enumerate(headings):
            img_id = sha1_id(row.location_id, h_angle, h_label)
            rec = row._asdict()
            rec.update({
                "image_id": img_id,
                "heading_set": heading_set,
                "images_per_location": len(headings),
                "direction_index": idx,
                "direction_label": h_label,
                "heading_strategy": h_strategy,
                "image_heading": h_angle,
                "image_pitch": 0,
                "image_fov": 90,
                "image_width": 640,
                "image_height": 640,
            })
            image_records.append(rec)
    
    img_df = pd.DataFrame(image_records)
    
    # Ordenar localizacoes para manter balanceamento espacial, mas garantir que 
    # todas as imagens de uma mesma localizacao fiquem no mesmo lote se possivel.
    # Para cardinal (4 vistas), o ideal eh batch_size ser multiplo de 4.
    
    full_batches = len(img_df) // batch_size
    used_images = full_batches * batch_size
    
    if full_batches == 0:
        raise RuntimeError(f"Total de {len(img_df)} imagens nao fecha um lote de {batch_size}.")

    # Nova lógica de distribuição estratificada por célula para garantir uniformidade em cada lote
    # 1. Coletar o pool de localizações únicas
    unique_loc_ids = list(img_df["location_id"].unique())
    locs_per_batch = batch_size // len(headings)
    num_batches = used_images // batch_size
    
    # 2. EMBARALHAMENTO GLOBAL: Dissolve a ordem sequencial da coleta.
    # Como o pool inicial ja eh geograficamente uniforme (pelo round-robin do grid),
    # embaralhar garante que cada lote seja uma fatia aleatoria dessa populacao uniforme.
    rng = random.Random(seed)
    rng.shuffle(unique_loc_ids)
    
    # 3. Atribuir cada localizacao a um numero de lote
    batch_assignments = {}
    for i, lid in enumerate(unique_loc_ids):
        b_num = (i // locs_per_batch) + 1
        if b_num <= num_batches:
            batch_assignments[lid] = b_num

    # 4. Aplicar os IDs de lote de volta ao dataframe de imagens
    img_df["batch_number"] = img_df["location_id"].map(batch_assignments)
    
    # Remover o que não coube nos lotes e finalizar
    batched = img_df.dropna(subset=["batch_number"]).copy()
    batched["batch_number"] = batched["batch_number"].astype(int)
    batched["batch_id"] = batched["batch_number"].map(lambda v: f"batch_{v:04d}")
    
    # Ordenar para manter imagens do mesmo lote e local juntas
    batched = batched.sort_values(["batch_number", "location_id", "direction_index"])
    batched["batch_location_index"] = batched.groupby("batch_id").cumcount() + 1

    return batched, {
        "heading_set": heading_set,
        "images_per_location": len(headings),
        "locations_per_batch": locs_per_batch,
        "full_batches": num_batches,
        "used_locations": len(batched.location_id.unique()),
        "used_images": len(batched),
        "leftover_eligible_locations": len(eligible_df) - len(batched.location_id.unique()),
        "leftover_eligible_images": len(img_df) - len(batched)
    }


def download_image_with_retry(
    session: requests.Session,
    api_key: str,
    row: pd.Series,
    destination: Path,
    timeout_s: float,
    request_delay_s: float,
    max_retries: int,
) -> bool:
    params = {
        "size": f"{int(row.image_width)}x{int(row.image_height)}",
        "heading": int(row.image_heading),
        "fov": int(row.image_fov),
        "pitch": int(row.image_pitch),
        "source": "outdoor",
        "return_error_code": "true",
        "key": api_key,
    }

    if row.pano_id:
        params["pano"] = row.pano_id
    else:
        params["location"] = f"{float(row.pano_lat):.7f},{float(row.pano_lng):.7f}"

    for attempt in range(max_retries):
        try:
            response = session.get(STREETVIEW_IMAGE_URL, params=params, timeout=timeout_s, stream=True)
            if response.status_code == 200:
                with destination.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk: handle.write(chunk)
                time.sleep(request_delay_s)
                return True
            if response.status_code == 429:
                return False # Quota handled outside
            if response.status_code in {500, 502, 503, 504}:
                time.sleep(min(2**attempt, 10))
                continue
            return False
        except requests.RequestException:
            time.sleep(min(2**attempt, 10))
    return False


def write_dataset(
    batched_df: pd.DataFrame,
    output_dir: Path,
    skip_download: bool,
    api_key: str | None,
    github_repo_url: str,
    github_branch: str,
    timeout_s: float,
    request_delay_s: float,
    max_retries: int,
    quota_backoff_initial_s: float,
    quota_backoff_max_s: float,
    quota_max_retries: int,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    
    downloaded = 0
    failed = 0
    skipped = 0
    quota_events = 0
    stop_execution = False
    stop_reason = None

    all_rows = []
    
    for batch_id, group in batched_df.groupby("batch_id", sort=True):
        if stop_execution: break
        
        batch_dir = output_dir / batch_id
        images_dir = batch_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        
        print(f"Processando {batch_id} ({len(group)} imagens)...")
        rows_for_csv = []
        
        for row in group.itertuples(index=False):
            if stop_execution: break
            
            file_name = f"{row.image_id}.jpg"
            image_path = images_dir / file_name
            # Precisamos do caminho relativo a raiz do repositorio para a URL do GitHub
            image_rel = output_dir / batch_id / "images" / file_name
            
            is_ok = image_path.exists()
            if not is_ok and not skip_download:
                # Lógica de download com backoff para Quota
                backoff = quota_backoff_initial_s
                for q_attempt in range(quota_max_retries + 1):
                    is_ok = download_image_with_retry(
                        session, api_key, row, image_path, timeout_s, request_delay_s, max_retries
                    )
                    if is_ok: break
                    
                    # Se falhou, pode ser quota ou erro real
                    print(f"Falha no download de {row.image_id}. Aguardando {backoff}s...")
                    quota_events += 1
                    time.sleep(backoff)
                    backoff = min(backoff * 2, quota_backoff_max_s)
                
                if not is_ok:
                    stop_execution = True
                    stop_reason = "QUOTA_EXCEEDED_OR_NETWORK_ERROR"
                    print("Limite de tentativas de quota atingido. Parando download.")

            if is_ok: downloaded += 1
            elif skip_download: skipped += 1
            else: failed += 1

            row_dict = row._asdict()
            row_dict.update({
                "image_file": str(image_rel),
                "image_url": get_github_raw_url(image_rel, github_repo_url, github_branch),
                "image_downloaded": is_ok,
                "download_status": "downloaded" if is_ok else "failed",
                "download_error": stop_reason if not is_ok and stop_execution else ""
            })
            rows_for_csv.append(row_dict)

        pd.DataFrame(rows_for_csv).to_csv(batch_dir / "metadata.csv", index=False)
        # Salvar tambem locations no nivel do batch
        loc_cols = ["location_id", "pano_id", "pano_lat", "pano_lng", "capture_date", "road_heading"]
        group.drop_duplicates("location_id")[loc_cols].to_csv(batch_dir / "locations.csv", index=False)
        all_rows.extend(rows_for_csv)

    # Consolidados
    full_df = pd.DataFrame(all_rows)
    if not full_df.empty:
        full_df.to_csv(output_dir / "metadata_all.csv", index=False)
        loc_df = full_df.drop_duplicates("location_id")
        loc_df.to_csv(output_dir / "locations_all.csv", index=False)
        
        # Gerar batches.csv
        batch_summary = full_df.groupby("batch_id").size().reset_index(name="images")
        batch_summary.to_csv(output_dir / "batches.csv", index=False)

    return {
        "downloaded_images": downloaded,
        "failed_downloads": failed,
        "skipped_downloads": skipped,
        "quota_events": quota_events,
        "quota_stop": 1 if stop_execution else 0,
        "quota_stop_reason": stop_reason
    }


def parse_args():
    parser = argparse.ArgumentParser(description="Pipeline Street View DF")
    parser.add_argument("--place-query", default=DEFAULT_PLACE_QUERY)
    parser.add_argument("--min-year", type=int, default=DEFAULT_MIN_YEAR)
    parser.add_argument("--heading-set", choices=["cardinal", "forward", "left", "right", "backward", "random"], default="cardinal")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--max-images", type=int, default=None)
    parser.add_argument("--candidate-spacing-m", type=float, default=None)
    parser.add_argument("--candidate-multiplier", type=float, default=DEFAULT_CANDIDATE_MULTIPLIER)
    parser.add_argument("--grid-size-m", type=float, default=DEFAULT_GRID_SIZE_M)
    parser.add_argument("--sampling-strategy", choices=["uniform", "proportional"], default="uniform", help="Estrategia de amostragem: 'uniform' (cobertura espacial usando grid) ou 'proportional' (proporcional a densidade viaria usando shuffle global).")
    parser.add_argument("--max-metadata-requests", type=int, default=None)
    parser.add_argument("--anchor-size", type=int, default=1000, help="Tamanho da amostra anchor set.")
    parser.add_argument("--github-repo-url", default=DEFAULT_GITHUB_REPO_URL)
    parser.add_argument("--github-branch", default=DEFAULT_GITHUB_BRANCH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--seed", type=int, default=DEFAULT_RANDOM_SEED)
    parser.add_argument("--api-key-env", default="API_KEY")
    
    # Timeouts e Quota
    parser.add_argument("--timeout-s", type=float, default=10.0)
    parser.add_argument("--request-delay-s", type=float, default=0.05)
    parser.add_argument("--max-retries", type=int, default=5)
    parser.add_argument("--quota-backoff-initial-s", type=float, default=30.0)
    parser.add_argument("--quota-backoff-max-s", type=float, default=900.0)
    parser.add_argument("--quota-max-retries", type=int, default=6)
    parser.add_argument("--report-every", type=int, default=250)
    parser.add_argument("--metadata-radius-m", type=int, default=25)
    parser.add_argument("--network-type", default="drive")
    parser.add_argument("--input-csv", type=Path, default=None)

    return parser.parse_args()


def main():
    load_dotenv()
    args = parse_args()
    api_key = os.getenv(args.api_key_env)
    
    if not api_key and args.input_csv is None:
        raise RuntimeError(f"API_KEY nao encontrada.")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.cache_dir.mkdir(parents=True, exist_ok=True)

    if args.input_csv:
        # Modo simplificado para teste/re-processamento
        eligible_df = pd.read_csv(args.input_csv)
        metadata_stats = {"metadata_processed": len(eligible_df), "eligible_locations": len(eligible_df)}
    else:
        # Fluxo normal
        graph = build_drive_graph(args.place_query, args.network_type)
        
        target_count = None
        if args.candidate_spacing_m is None and args.max_images:
            # Se for cardinal, cada localizacao gera 4 imagens
            mult = 4 if args.heading_set == "cardinal" else 1
            target_count = int(math.ceil((args.max_images / mult) * args.candidate_multiplier))
            
        candidates_df, _, spacing = generate_candidate_points(
            graph, args.candidate_spacing_m, target_count, args.grid_size_m, args.seed, args.sampling_strategy
        )
        candidates_df.to_csv(args.cache_dir / "candidate_points.csv", index=False)
        
        # Se cardinal, max_locations eh max_images / 4
        max_locs = (args.max_images // 4) if (args.max_images and args.heading_set == "cardinal") else args.max_images
        
        eligible_df, metadata_stats = collect_eligible_points(
            candidates_df, api_key, args.cache_dir, args.min_year, max_locs, 
            args.max_metadata_requests, args.timeout_s, args.request_delay_s, 
            args.max_retries, args.metadata_radius_m, args.report_every
        )
        eligible_df.to_csv(args.cache_dir / "eligible_points.csv", index=False)

    # Batching
    batched_df, batch_stats = assign_batches(
        eligible_df, args.heading_set, args.batch_size, args.seed, args.max_images
    )
    
    # Download e Escrita
    write_stats = write_dataset(
        batched_df, args.output_dir, args.skip_download, api_key, 
        args.github_repo_url, args.github_branch, args.timeout_s, args.request_delay_s, 
        args.max_retries, args.quota_backoff_initial_s, args.quota_backoff_max_s, 
        args.quota_max_retries
    )

    # Anchor Set (Amostra unica para o paper)
    anchor_sample_path = args.output_dir / "anchor_set_sample.csv"
    anchor_written = False
    if args.anchor_size > 0:
        # Pega uma imagem por localizacao da amostra inicial
        anchor_df = batched_df.drop_duplicates("location_id").head(args.anchor_size).copy()
        anchor_df["anchor_rank"] = range(1, len(anchor_df) + 1)
        anchor_df.to_csv(anchor_sample_path, index=False)
        anchor_written = True

    summary = {
        "place_query": args.place_query,
        "min_year": args.min_year,
        "heading_set": args.heading_set,
        "sampling_strategy": getattr(args, 'sampling_strategy', 'uniform'),
        "images_per_location": batch_stats["images_per_location"],
        "batch_size": args.batch_size,
        "max_images": args.max_images,
        "skip_download": args.skip_download,
        "output_dir": str(args.output_dir),
        "cache_dir": str(args.cache_dir),
        "github_repo_url": args.github_repo_url,
        "github_branch": args.github_branch,
        "quota_backoff_initial_s": args.quota_backoff_initial_s,
        "quota_backoff_max_s": args.quota_backoff_max_s,
        "quota_max_retries": args.quota_max_retries,
        "metadata_stats": metadata_stats,
        "batch_stats": batch_stats,
        "write_stats": write_stats,
        "anchor_stats": {
            "anchor_sample_written": anchor_written,
            "anchor_sample_size": len(anchor_df) if anchor_written else 0,
            "anchor_sample_path": str(anchor_sample_path)
        },
        "paper_alignment": {
            "four_cardinal_headings": args.heading_set == "cardinal",
            "location_level_outputs": True,
            "anchor_sample_written": anchor_written,
            "pairwise_mllm_ranking_implemented": False,
            "clip_knn_city_scoring_implemented": False
        }
    }

    with (args.output_dir / "summary.json").open("w", encoding="utf-8") as h:
        json.dump(summary, h, indent=2)

    print("\nProcesso concluido!")
    print(f"Lotes gerados: {batch_stats['full_batches']}")
    print(f"Imagens processadas: {write_stats['downloaded_images']}")
    if write_stats['quota_stop']:
        print("AVISO: O processo parou prematuramente devido a limite de quota. Rode novamente para continuar.")

if __name__ == "__main__":
    main()

