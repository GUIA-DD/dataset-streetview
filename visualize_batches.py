import os
import pandas as pd
import folium
import json
import argparse
from pathlib import Path
from folium import Element
import geopandas as gpd
import osmnx as ox
from shapely.geometry import Polygon

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-dir", type=str, default="datasets/df_2024_proportional", help="Caminho para o diretório do dataset")
    parser.add_argument("--output-html", type=str, default="df_proportional_sampling_coverage.html", help="Caminho do arquivo HTML de saída")
    return parser.parse_args()

def main():
    args = parse_args()
    DATASET_DIR = Path(args.dataset_dir)
    CSV_FILE = DATASET_DIR / "locations_all.csv"
    OUTPUT_MAP = args.output_html

    if not CSV_FILE.exists():
        print(f"Erro: Arquivo {CSV_FILE} não encontrado. Certifique-se de que os dados foram baixados.")
        return

    print(f"Lendo dados de {CSV_FILE}...")
    df = pd.read_csv(CSV_FILE)
    
    # Garantir que temos as colunas necessárias
    required_cols = ['pano_lat', 'pano_lng', 'batch_id', 'capture_date']
    for col in required_cols:
        if col not in df.columns:
            print(f"Erro: Coluna '{col}' não encontrada no CSV.")
            return

    # Criar o mapa base
    m = folium.Map(
        location=[df['pano_lat'].mean(), df['pano_lng'].mean()], 
        zoom_start=10, 
        tiles="cartodbpositron",
        prefer_canvas=True
    )

    # ----------------------------------------------------
    # GERAR O GRID DE 5KMx5KM
    # ----------------------------------------------------
    print("Gerando grid espacial (5x5 km)...")
    try:
        # Criar GDF apenas com os pontos únicos
        df_locs = df.drop_duplicates(subset=['location_id']).copy() if 'location_id' in df.columns else df.copy()
        
        gdf_points = gpd.GeoDataFrame(
            df_locs, geometry=gpd.points_from_xy(df_locs.pano_lng, df_locs.pano_lat), crs="EPSG:4326"
        )
        gdf_proj = ox.projection.project_gdf(gdf_points)
        
        # A malha viária inteira poderia ter um bounding box maior.
        # Vamos garantir que cobrimos toda a área usando min/max com uma margem.
        min_x = gdf_proj.geometry.x.min() - 5000
        min_y = gdf_proj.geometry.y.min() - 5000
        max_x = gdf_proj.geometry.x.max() + 5000
        max_y = gdf_proj.geometry.y.max() + 5000

        grid_size_m = 5000.0
        polygons = []
        
        curr_x = min_x
        while curr_x < max_x:
            curr_y = min_y
            while curr_y < max_y:
                poly = Polygon([
                    (curr_x, curr_y),
                    (curr_x + grid_size_m, curr_y),
                    (curr_x + grid_size_m, curr_y + grid_size_m),
                    (curr_x, curr_y + grid_size_m)
                ])
                polygons.append(poly)
                curr_y += grid_size_m
            curr_x += grid_size_m
            
        grid_gdf = gpd.GeoDataFrame(geometry=polygons, crs=gdf_proj.crs).to_crs("EPSG:4326")
        
        grid_features = []
        for _, row in grid_gdf.iterrows():
            grid_features.append({
                "type": "Feature",
                "geometry": row.geometry.__geo_interface__,
                "properties": {}
            })
        grid_geojson_str = json.dumps({"type": "FeatureCollection", "features": grid_features})
    except Exception as e:
        print(f"Aviso: Não foi possível gerar a grade: {e}")
        grid_geojson_str = "null"

    # ----------------------------------------------------
    # GERAR O CONTORNO DO DF
    # ----------------------------------------------------
    print("Obtendo contorno do Distrito Federal...")
    try:
        df_boundary = ox.geocode_to_gdf("Distrito Federal, Brazil")
        df_boundary_geom = df_boundary.geometry.simplify(0.001)
        df_boundary_geojson_str = df_boundary_geom.to_json()
    except Exception as e:
        print(f"Aviso: Não foi possível obter o contorno do DF: {e}")
        df_boundary_geojson_str = "null"

    # Preparar dados para o JavaScript
    batches = sorted(df['batch_id'].unique())
    colors = [
        "#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231", 
        "#911eb4", "#46f0f0", "#f032e6", "#bcf60c", "#fabebe", 
        "#008080", "#e6beff", "#9a6324", "#fffac8", "#800000", 
        "#aaffc3", "#808000", "#ffd8b1", "#000075", "#808080"
    ]
    batch_color_map = {batch: colors[i % len(colors)] for i, batch in enumerate(batches)}

    features = []
    for _, row in df.iterrows():
        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [float(row['pano_lng']), float(row['pano_lat'])]
            },
            "properties": {
                "batch_id": row['batch_id'],
                "date": str(row['capture_date']),
                "color": batch_color_map[row['batch_id']]
            }
        }
        features.append(feature)
    
    geojson_str = json.dumps({"type": "FeatureCollection", "features": features})
    
    # Gerar HTML para os checkboxes
    checkboxes_html = ""
    for batch in batches:
        color = batch_color_map[batch]
        checkboxes_html += f"""
        <label style="display: block; font-size: 12px; margin-bottom: 4px; cursor: pointer; color: black;">
            <input type="checkbox" class="batch-checkbox" value="{batch}" checked onchange="updateMap()"> 
            <span style="display: inline-block; width: 10px; height: 10px; background: {color}; border-radius: 50%; margin-right: 5px;"></span>
            {batch}
        </label>
        """

    custom_script = f"""
    <div id="control-panel" style="
        position: fixed; 
        bottom: 30px; left: 30px; width: 250px; 
        z-index: 9999; background: white; padding: 15px; 
        border: 2px solid grey; border-radius: 10px; opacity: 0.95;
        font-family: Arial, sans-serif; box-shadow: 0 4px 15px rgba(0,0,0,0.2);
        max-height: 80vh; overflow-y: auto;">
        
        <h4 style="margin-top:0; color: #2c3e50; margin-bottom: 12px; border-bottom: 1px solid #eee; padding-bottom: 5px;">Opções de Mapa</h4>
        
        <label style="display: block; font-size: 13px; font-weight: bold; margin-bottom: 15px; cursor: pointer; color: #2980b9;">
            <input type="checkbox" id="grid-checkbox" onchange="toggleGrid()"> 
            Mostrar Grade (5x5 km)
        </label>
        
        <h4 style="margin-top:0; color: #2c3e50; margin-bottom: 12px; border-bottom: 1px solid #eee; padding-bottom: 5px;">Cobertura por Lote</h4>
        
        <div style="margin-bottom: 10px;">
            <button onclick="toggleAll(true)" style="font-size: 11px; cursor: pointer;">Selecionar Todos</button>
            <button onclick="toggleAll(false)" style="font-size: 11px; cursor: pointer;">Limpar</button>
        </div>

        <div id="batch-list">
            {checkboxes_html}
        </div>
        
        <!-- Contador -->
        <div style="margin-top: 15px; padding: 10px; background: #2c3e50; color: white; border-radius: 5px; text-align: center;">
            <span style="font-size: 12px; opacity: 0.8;">Pontos visíveis:</span><br>
            <strong id="visible-count" style="font-size: 18px;">0</strong>
        </div>
    </div>

    <script>
    var map;
    var fullData = {geojson_str};
    var gridData = {grid_geojson_str};
    var dfBoundaryData = {df_boundary_geojson_str};
    var geojsonLayer;
    var gridLayer;
    var boundaryLayer;

    function initBoundary() {{
        if (!dfBoundaryData) return;
        boundaryLayer = L.geoJson(dfBoundaryData, {{
            style: {{
                color: "#2c3e50",
                weight: 3,
                fillOpacity: 0.0,
                dashArray: "10, 5"
            }},
            interactive: false
        }}).addTo(map);
    }}

    function toggleAll(visible) {{
        document.querySelectorAll('.batch-checkbox').forEach(cb => cb.checked = visible);
        updateMap();
    }}

    function toggleGrid() {{
        if (!gridData) return;
        var showGrid = document.getElementById('grid-checkbox').checked;
        if (showGrid) {{
            if (!gridLayer) {{
                gridLayer = L.geoJson(gridData, {{
                    style: {{
                        color: "#8e44ad",
                        weight: 1.5,
                        fillOpacity: 0.05,
                        dashArray: "5, 5"
                    }},
                    interactive: false
                }});
            }}
            gridLayer.addTo(map);
            // Garante que o grid fique atrás dos pontos
            gridLayer.bringToBack();
            // Mas o contorno do DF fica na frente de tudo (atrás dos pontos)
            if (boundaryLayer) boundaryLayer.bringToBack(); 
        }} else {{
            if (gridLayer) map.removeLayer(gridLayer);
        }}
    }}

    function updateMap() {{
        var selectedBatches = Array.from(document.querySelectorAll('.batch-checkbox:checked')).map(cb => cb.value);
        var countDisplay = document.getElementById('visible-count');

        if (geojsonLayer) map.removeLayer(geojsonLayer);

        var visibleCount = 0;

        geojsonLayer = L.geoJson(fullData, {{
            pointToLayer: function (feature, latlng) {{
                return L.circleMarker(latlng, {{
                    radius: 4,
                    fillColor: feature.properties.color,
                    color: "#000",
                    weight: 0.5,
                    opacity: 1,
                    fillOpacity: 0.8
                }});
            }},
            filter: function(feature) {{
                var isVisible = selectedBatches.includes(feature.properties.batch_id);
                if (isVisible) visibleCount++;
                return isVisible;
            }},
            onEachFeature: function (feature, layer) {{
                layer.bindPopup("<b>Lote:</b> " + feature.properties.batch_id + "<br><b>Data:</b> " + feature.properties.date);
            }}
        }}).addTo(map);

        countDisplay.innerHTML = visibleCount.toLocaleString();
    }}

    document.addEventListener("DOMContentLoaded", function() {{
        map = {m.get_name()};
        initBoundary();
        updateMap();
    }});
    </script>
    """
    
    m.get_root().html.add_child(Element(custom_script))
    m.save(OUTPUT_MAP)
    print(f"Sucesso! Mapa de cobertura com Toggle de Grade salvo em: {OUTPUT_MAP}")

if __name__ == "__main__":
    main()