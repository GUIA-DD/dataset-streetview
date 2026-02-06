import os
import time
import math
import json
import hashlib
import random
import shutil
from pathlib import Path
from dotenv import load_dotenv
import requests
import pandas as pd
import osmnx as ox
from shapely.geometry import Polygon, LineString, MultiLineString
from tqdm import tqdm

load_dotenv()

#config

API_KEY = os.getenv("API_KEY")

# Configurações do GitHub
GITHUB_USER = "himeakLucas"
GITHUB_REPO = "dataset-streetview"
GITHUB_BRANCH = "main"

# Parâmetros de Requisição
REQUEST_DELAY = 0.12
REQUEST_TIMEOUT = 10.0
MAX_RETRIES = 3

# Parâmetros de Amostragem
DENSITY_POINTS_PER_KM = 10
MAX_POINTS_PER_REGION = 300
RANDOM_SEED = 42

# Parâmetros de Imagem
DOWNLOAD_IMAGES = True
IMAGE_SIZE = "640x640"
IMAGE_FOV = 90
IMAGE_PITCH = 0
MIN_YEAR = None

# Saídas
SAVE_DIR = "streetview_images_db"
OUTPUT_CSV = "streetview_metadata_db.csv"
OUTPUT_JSON = "streetview_metadata_db.json"
SUMMARY_CSV = "allocation_summary.csv"

MAX_TOTAL_REQUESTS = None 

random.seed(RANDOM_SEED)

#HELPERS GEOMETRIA & UTIL

def sha1_id(*parts) -> str:
    s = "_".join(map(str, parts))
    return hashlib.sha1(s.encode("utf-8")).hexdigest()

def get_perpendicular_headings(heading: float):
    return [(heading + 90) % 360, (heading - 90) % 360]

def compute_heading_at(line: LineString, t: float, delta: float = 1e-4) -> float:
    length = line.length
    if length == 0: return 0.0
    d0 = t * length
    if d0 + delta <= length:
        p1 = line.interpolate(d0); p2 = line.interpolate(d0 + delta)
    else:
        p1 = line.interpolate(d0); p2 = line.interpolate(max(d0 - delta, 0))
    angle_deg = math.degrees(math.atan2(p2.x - p1.x, p2.y - p1.y))
    return angle_deg + 360 if angle_deg < 0 else angle_deg

def parse_year(date_field):
    if not date_field: return None
    try: return int(str(date_field).split("-")[0])
    except: return None

def get_github_raw_url(relative_path):
    clean_path = str(relative_path).replace("\\", "/")
    return f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/{GITHUB_BRANCH}/{clean_path}"

#HELPERS OSMNX

def allocate_points_by_density(polygons_dict, density):
    allocation = {}
    graphs = {}
    
    print(f"Calculando malha viária para {len(polygons_dict)} regiões...")
    
    for region, coords in polygons_dict.items():
        print(f"  - Processando: {region}")
        poly = Polygon(coords)
        try:
            G = ox.graph_from_polygon(poly, network_type="drive", simplify=True)
            Gp = ox.project_graph(G)
            edges = ox.graph_to_gdfs(Gp, nodes=False, edges=True)
            
            col = "length" if "length" in edges.columns else "geometry"
            total_km = edges[col].sum() / 1000.0 if col == "length" else edges.geometry.length.sum() / 1000.0
            
            # Calcula baseada na densidade
            pts = max(1, int(round(total_km * density)))
            
            # Aplica o limite máximo se a variável estiver definida
            if MAX_POINTS_PER_REGION is not None and pts > MAX_POINTS_PER_REGION:
                pts = MAX_POINTS_PER_REGION
            
            allocation[region] = pts
            graphs[region] = G
        except Exception as e:
            print(f"    [ERRO] Região {region}: {e}")
            allocation[region] = 0
            
    return allocation, graphs

def generate_points_along_edges(G, n_points):
    edges = ox.graph_to_gdfs(G, nodes=False, edges=True)
    geoms = []
    for geom in edges["geometry"]:
        if isinstance(geom, LineString): geoms.append(geom)
        elif isinstance(geom, MultiLineString): geoms.extend(geom.geoms)
            
    total_len = sum(g.length for g in geoms)
    if total_len == 0 or not geoms: return []
    
    spacing = total_len / n_points
    out = []
    current_dist = 0
    target_dist = spacing / 2 
    
    for line in geoms:
        line_len = line.length
        while current_dist + line_len > target_dist:
            remainder = target_dist - current_dist
            t = remainder / line_len
            out.append((line, t))
            target_dist += spacing
        current_dist += line_len
    if len(out) > n_points: out = out[:n_points]
    return out

# ---------------- API STREET VIEW ----------------

def get_metadata(lat, lng, api_key):
    url = "https://maps.googleapis.com/maps/api/streetview/metadata"
    params = {"location": f"{lat},{lng}", "key": api_key}
    for _ in range(MAX_RETRIES):
        try:
            r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200: return r.json()
            if r.status_code in [429, 500, 502, 503]:
                time.sleep(1); continue
        except: time.sleep(1)
    return {"status": "REQUEST_FAILED"}

def download_image(lat, lng, heading, path, api_key):
    url = "https://maps.googleapis.com/maps/api/streetview"
    params = {"location": f"{lat},{lng}", "heading": heading, "fov": IMAGE_FOV, "pitch": IMAGE_PITCH, "size": IMAGE_SIZE, "key": api_key}
    try:
        r = requests.get(url, params=params, stream=True, timeout=REQUEST_TIMEOUT)
        if r.status_code == 200:
            with open(path, "wb") as f: shutil.copyfileobj(r.raw, f)
            return True
    except: pass
    return False

#main logic

def process_region(region, G, n_points, api_key, stats_counter):
    records = []
    points = generate_points_along_edges(G, n_points)
    region_dir = os.path.join(SAVE_DIR, region)
    os.makedirs(region_dir, exist_ok=True)
    
    pbar = tqdm(points, desc=f"Região: {region}", leave=False)
    
    for line, t in pbar:
        if MAX_TOTAL_REQUESTS and stats_counter['reqs'] >= MAX_TOTAL_REQUESTS: break
        
        p = line.interpolate(t * line.length)
        lat, lng = p.y, p.x
        street_h = compute_heading_at(line, t)
        
        meta = get_metadata(lat, lng, api_key)
        stats_counter['reqs'] += 1
        time.sleep(REQUEST_DELAY)
        
        if meta.get("status") != "OK": continue 
        
        year = parse_year(meta.get("date"))
        if MIN_YEAR and (year is None or year < MIN_YEAR): continue
            
        id_base = sha1_id(f"{lat:.5f}", f"{lng:.5f}") 
        headings = get_perpendicular_headings(street_h)
        
        for h in headings:
            angle = int(round(h))
            img_id = sha1_id(id_base, angle)
            filename = f"{img_id}.jpg"
            local_path = os.path.join(region_dir, filename)
            rel_path = os.path.join(SAVE_DIR, region, filename)
            
            downloaded = os.path.exists(local_path)
            if not downloaded and DOWNLOAD_IMAGES:
                if download_image(lat, lng, angle, local_path, api_key):
                    downloaded = True
                    stats_counter['reqs'] += 1
                    time.sleep(REQUEST_DELAY)
            
            rec = {
                "id": img_id,
                "region": region,
                "lat": lat,
                "lng": lng,
                "heading": angle,
                "year": year,
                "date": meta.get("date"),
                "pano_id": meta.get("pano_id"),
                "image_downloaded": downloaded,
                "image_url": get_github_raw_url(rel_path) if downloaded else None
            }
            records.append(rec)
    return records

def build_db(polygons_def):
    if not API_KEY: return print("ERRO: API_KEY não definida.")
    os.makedirs(SAVE_DIR, exist_ok=True)
    
    allocation, graphs = allocate_points_by_density(polygons_def, DENSITY_POINTS_PER_KM)
    
    # Salvar resumo
    pd.DataFrame(list(allocation.items()), columns=["region", "points"]).to_csv(SUMMARY_CSV, index=False)
    
    all_records = []
    stats = {'reqs': 0}
    
    print(f"\nIniciando coleta para o repo: {GITHUB_USER}/{GITHUB_REPO}")
    print(f"Limite por região configurado: {MAX_POINTS_PER_REGION} pontos")
    
    for region, n_points in allocation.items():
        if n_points == 0: continue
        print(f"-> Coletando {n_points} pontos em {region}...")
        all_records.extend(process_region(region, graphs[region], n_points, API_KEY, stats))
        if MAX_TOTAL_REQUESTS and stats['reqs'] >= MAX_TOTAL_REQUESTS: break
            
    if all_records:
        pd.DataFrame(all_records).to_csv(OUTPUT_CSV, index=False)
        with open(OUTPUT_JSON, "w", encoding="utf-8") as f: json.dump(all_records, f, indent=2)
        print(f"\nCSV salvo: {OUTPUT_CSV}\nTotal de registros: {len(all_records)}")
    else:
        print("Nenhum registro encontrado.")

if __name__ == "__main__":
    my_polygons = {
        "aguas_claras": [(-48.0450, -15.8200), (-48.0150, -15.8200), (-48.0150, -15.8480), (-48.0450, -15.8480)],
        "taguatinga": [(-48.1000, -15.7900), (-48.0500, -15.7900), (-48.0500, -15.8500), (-48.1000, -15.8500)],
        "planaltina_df": [(-47.6800, -15.5800), (-47.6300, -15.5800), (-47.6300, -15.6500), (-47.6800, -15.6500)]
    }
    build_db(my_polygons)