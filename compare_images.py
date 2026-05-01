import os
import json
import random
import argparse
import pandas as pd
import osmnx as ox
from pathlib import Path

# Lista oficial/comum das RAs do DF para filtragem no OSM
RA_NAMES = [
    "Plano Piloto", "Gama", "Taguatinga", "Brazlândia", "Sobradinho", "Planaltina", "Paranoá", 
    "Núcleo Bandeirante", "Ceilândia", "Guará", "Cruzeiro", "Samambaia", "Santa Maria", 
    "São Sebastião", "Recanto das Emas", "Lago Sul", "Lago Norte", "Candangolândia", 
    "Águas Claras", "Riacho Fundo", "Riacho Fundo II", "Sudoeste/Octogonal", "Varjão", 
    "Park Way", "SCIA", "Sobradinho II", "Jardim Botânico", "Itapoã", "SIA", 
    "Vicente Pires", "Fercal", "Sol Nascente/Pôr do Sol", "Arniqueira", "Estrutural"
]

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-dir", type=str, default="datasets/df_2024_plus", help="Caminho para o diretório do dataset")
    parser.add_argument("--output-html", type=str, default="compare_images_proportional.html", help="Caminho do arquivo HTML de saída")
    return parser.parse_args()

def main():
    args = parse_args()
    DATASET_DIR = Path(args.dataset_dir)
    OUTPUT_HTML = args.output_html

    if not DATASET_DIR.exists():
        print(f"Erro: Diretório {DATASET_DIR} não encontrado.")
        return

    print(f"Carregando metadados das imagens de {DATASET_DIR}...")
    metadata_path = DATASET_DIR / "metadata_all.csv"
    if not metadata_path.exists():
        print(f"Erro: {metadata_path} não encontrado.")
        return
    
    meta_df = pd.read_csv(metadata_path)
    
    image_data = []
    dataset_name = DATASET_DIR.name
    for _, row in meta_df.iterrows():
        batch_id = row['batch_id']
        img_id = row['image_id']
        img_rel_path = f"datasets/{dataset_name}/{batch_id}/images/{img_id}.jpg"
        
        image_data.append({
            "path": img_rel_path,
            "lat": float(row['pano_lat']),
            "lng": float(row['pano_lng']),
            "batch": batch_id,
            "date": str(row['capture_date'])
        })

    if not image_data:
        print("Nenhum dado de imagem encontrado no CSV.")
        return

    # 1. Obter contorno do DF
    print("Obtendo contorno do Distrito Federal...")
    df_boundary_geojson = "null"
    df_polygon = None
    try:
        df_boundary = ox.geocode_to_gdf("Distrito Federal, Brazil")
        df_polygon = df_boundary.geometry.iloc[0]
        # Converter para dict e depois para string JSON
        df_boundary_geojson = json.dumps(json.loads(df_boundary.geometry.simplify(0.001).to_json()))
    except Exception as e:
        print(f"Aviso: Não foi possível obter o contorno do DF: {e}")

    # 2. Obter contornos das RAs (Regiões Administrativas)
    print("Obtendo contornos das RAs...")
    ras_geojson = "null"
    try:
        if df_polygon is not None:
            # Buscar limites administrativos no DF
            all_admin = ox.features_from_polygon(df_polygon, tags={"boundary": "administrative", "admin_level": "8"})
            
            if not all_admin.empty:
                def is_ra(row):
                    name = str(row.get("name", ""))
                    if name in RA_NAMES:
                        return row.geometry.centroid.within(df_polygon)
                    return False
                
                ras = all_admin[all_admin.apply(is_ra, axis=1)].copy()
                ras = ras.drop_duplicates(subset=["name"])
                
                print(f"Encontradas {len(ras)} RAs válidas.")
                
                # Construir o GeoJSON manualmente com as propriedades
                features = []
                for _, row in ras.iterrows():
                    feat = {
                        "type": "Feature",
                        "geometry": row.geometry.simplify(0.001).__geo_interface__,
                        "properties": {"name": row["name"]}
                    }
                    features.append(feat)
                ras_geojson = json.dumps({"type": "FeatureCollection", "features": features})
    except Exception as e:
        print(f"Aviso: Erro ao processar RAs: {e}")

    print(f"Total de imagens carregadas: {len(image_data)}")

    # Gerar o HTML
    html_content = f"""
    <!DOCTYPE html>
    <html lang="pt-br">
    <head>
        <meta charset="UTF-8">
        <title>Dashboard Street View - {dataset_name}</title>
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
        <style>
            :root {{
                --bg-color: #121212;
                --card-bg: #1e1e1e;
                --accent-color: #2ecc71;
                --secondary-color: #3498db;
                --text-color: #e0e0e0;
                --text-muted: #888;
            }}
            
            body {{
                font-family: 'Inter', 'Segoe UI', system-ui, sans-serif;
                background-color: var(--bg-color);
                color: var(--text-color);
                margin: 0;
                padding: 15px;
                display: flex;
                flex-direction: column;
                height: 100vh;
                box-sizing: border-box;
                overflow: hidden;
            }}
            
            .header {{ text-align: center; margin-bottom: 10px; flex-shrink: 0; }}
            .header h2 {{ margin: 0; color: var(--accent-color); font-size: 1.5rem; }}
            
            .main-layout {{
                display: grid;
                grid-template-columns: 1fr 1fr; 
                gap: 15px;
                flex-grow: 1;
                width: 100%;
                max-width: 1800px;
                margin: 0 auto;
                align-items: center;
                transition: grid-template-columns 0.4s ease;
            }}
            
            .main-layout.with-map {{ grid-template-columns: 1fr 1fr 1fr; }}
            
            .image-card, #map-box {{
                background: var(--card-bg);
                border-radius: 12px;
                overflow: hidden;
                box-shadow: 0 10px 30px rgba(0,0,0,0.5);
                border: 1px solid #333;
                aspect-ratio: 1 / 1;
                display: flex;
                flex-direction: column;
                position: relative;
            }}
            
            .image-container {{ flex-grow: 1; display: flex; align-items: center; justify-content: center; background: #000; overflow: hidden; }}
            img {{ width: 100%; height: 100%; object-fit: cover; }}
            
            .image-info {{
                padding: 10px;
                background: rgba(20,20,20,0.8);
                font-size: 0.8rem;
                display: flex;
                justify-content: space-between;
                border-top: 1px solid #333;
            }}
            
            .label-tag {{
                position: absolute;
                top: 10px;
                left: 10px;
                padding: 5px 12px;
                border-radius: 5px;
                font-weight: 800;
                font-size: 0.7rem;
                text-transform: uppercase;
                z-index: 10;
            }}
            .label-1 {{ background: #e6194b; color: white; }}
            .label-2 {{ background: #4363d8; color: white; }}
            
            #map-box {{ display: none; }}
            #map {{ width: 100%; height: 100%; background: #1a1a1a; }}
            
            .bottom-bar {{
                flex-shrink: 0;
                display: flex;
                justify-content: center;
                align-items: center;
                gap: 15px;
                padding: 20px 0;
            }}
            
            button {{
                padding: 12px 24px;
                font-size: 0.95rem;
                font-weight: 700;
                color: white;
                border: none;
                border-radius: 10px;
                cursor: pointer;
                transition: all 0.2s;
                display: flex;
                align-items: center;
                gap: 8px;
            }}
            .btn-next {{ background-color: var(--accent-color); color: #052c16; }}
            .btn-next:hover {{ background-color: #34e77f; transform: translateY(-3px); }}
            
            .btn-toggle {{ background-color: #333; border: 1px solid #444; }}
            .btn-toggle:hover {{ background-color: #444; }}
            .btn-toggle.active {{ background-color: var(--secondary-color); border-color: var(--secondary-color); }}
            
            button:active {{ transform: scale(0.95); }}
            .ra-tooltip {{ background: rgba(0,0,0,0.8); border: 1px solid var(--accent-color); color: white; font-weight: bold; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h2>Comparação para percepção de segurança</h2>
        </div>

        <div id="layout" class="main-layout">
            <div class="image-card">
                <div class="image-container">
                    <span class="label-tag label-1">Amostra A</span>
                    <img id="img1" src="" alt="Imagem 1">
                </div>
                <div class="image-info" id="info1"></div>
            </div>

            <div class="image-card">
                <div class="image-container">
                    <span class="label-tag label-2">Amostra B</span>
                    <img id="img2" src="" alt="Imagem 2">
                </div>
                <div class="image-info" id="info2"></div>
            </div>

            <div id="map-box">
                <div id="map"></div>
            </div>
        </div>

        <div class="bottom-bar">
            <button class="btn-next" onclick="refreshImages()">
                <span>🔄</span> Próximo Par
            </button>
            <button class="btn-toggle" id="map-toggle-btn" onclick="toggleMap()">
                <span>🗺️</span> Mapa
            </button>
            <button class="btn-toggle" id="ra-toggle-btn" onclick="toggleRAs()" style="display:none;">
                <span>🏢</span> Regiões Administrativas
            </button>
        </div>

        <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
        <script>
            const imageData = {json.dumps(image_data)};
            const dfBoundary = {df_boundary_geojson};
            const rasData = {ras_geojson};
            
            let map, marker1, marker2, raLayer;
            let currentIdx1, currentIdx2;
            let isMapVisible = false;
            let isRAVisible = false;

            function initMap() {{
                map = L.map('map', {{ zoomControl: false, attributionControl: false }}).setView([-15.78, -47.93], 10);
                L.control.zoom({{ position: 'bottomright' }}).addTo(map);
                L.tileLayer('https://{{s}}.basemaps.cartocdn.com/rastertiles/voyager/{{z}}/{{x}}/{{y}}{{r}}.png', {{
                    subdomains: 'abcd', maxZoom: 20
                }}).addTo(map);
                
                if (dfBoundary) {{
                    L.geoJSON(dfBoundary, {{
                        style: {{ color: "#2ecc71", weight: 2, fillColor: "#2ecc71", fillOpacity: 0.01, dashArray: "5, 5" }}
                    }}).addTo(map);
                }}

                if (rasData) {{
                    raLayer = L.geoJSON(rasData, {{
                        style: {{ color: "#f39c12", weight: 1.5, fillColor: "#f39c12", fillOpacity: 0.1 }},
                        onEachFeature: function(feature, layer) {{
                            if (feature.properties && feature.properties.name) {{
                                layer.bindTooltip(feature.properties.name, {{ sticky: true, className: 'ra-tooltip' }});
                            }}
                        }}
                    }});
                }}
                
                marker1 = L.circleMarker([0, 0], {{ radius: 10, color: 'white', weight: 3, fillColor: '#e6194b', fillOpacity: 1 }}).addTo(map);
                marker2 = L.circleMarker([0, 0], {{ radius: 10, color: 'white', weight: 3, fillColor: '#4363d8', fillOpacity: 1 }}).addTo(map);
            }}

            function toggleMap() {{
                const layout = document.getElementById('layout');
                const mapBox = document.getElementById('map-box');
                const btn = document.getElementById('map-toggle-btn');
                const raBtn = document.getElementById('ra-toggle-btn');
                isMapVisible = !isMapVisible;
                if (isMapVisible) {{
                    layout.classList.add('with-map');
                    mapBox.style.display = 'flex';
                    btn.classList.add('active');
                    raBtn.style.display = 'flex';
                    if (!map) initMap();
                    setTimeout(() => {{ map.invalidateSize(); updateMapMarkers(); }}, 400);
                }} else {{
                    layout.classList.remove('with-map');
                    mapBox.style.display = 'none';
                    btn.classList.remove('active');
                    raBtn.style.display = 'none';
                }}
            }}

            function toggleRAs() {{
                const btn = document.getElementById('ra-toggle-btn');
                if (!raLayer) return;
                isRAVisible = !isRAVisible;
                if (isRAVisible) {{
                    raLayer.addTo(map);
                    btn.classList.add('active');
                }} else {{
                    map.removeLayer(raLayer);
                    btn.classList.remove('active');
                }}
            }}

            function refreshImages() {{
                if (imageData.length < 2) return;
                currentIdx1 = Math.floor(Math.random() * imageData.length);
                currentIdx2 = Math.floor(Math.random() * imageData.length);
                while (currentIdx1 === currentIdx2) {{ currentIdx2 = Math.floor(Math.random() * imageData.length); }}
                const item1 = imageData[currentIdx1];
                const item2 = imageData[currentIdx2];
                document.getElementById('img1').src = item1.path;
                document.getElementById('info1').innerHTML = `<span><b>Lote:</b> ${{item1.batch}}</span> <span><b>Data:</b> ${{item1.date}}</span>`;
                document.getElementById('img2').src = item2.path;
                document.getElementById('info2').innerHTML = `<span><b>Lote:</b> ${{item2.batch}}</span> <span><b>Data:</b> ${{item2.date}}</span>`;
                if (isMapVisible) updateMapMarkers();
            }}

            function updateMapMarkers() {{
                if (!map) return;
                marker1.setLatLng([imageData[currentIdx1].lat, imageData[currentIdx1].lng]);
                marker2.setLatLng([imageData[currentIdx2].lat, imageData[currentIdx2].lng]);
            }}

            refreshImages();
        </script>
    </body>
    </html>
    """

    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"Sucesso! Dashboard corrigido com RAs gerado em: {OUTPUT_HTML}")

if __name__ == "__main__":
    main()
