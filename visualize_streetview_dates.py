import os
import pandas as pd
import folium
import branca.colormap as cm
import json
from folium import Element

# Configurações
CSV_FILE = "streetview_df_locations.csv"
OUTPUT_MAP = "df_streetview_dates.html"

def main():
    if not os.path.exists(CSV_FILE):
        print(f"Erro: Arquivo {CSV_FILE} não encontrado.")
        return

    print(f"Lendo dados de {CSV_FILE}...")
    df = pd.read_csv(CSV_FILE)
    
    min_year = int(df.year.min())
    max_year = int(df.year.max())

    # Criar o mapa base
    m = folium.Map(
        location=[-15.78, -47.93], 
        zoom_start=10, 
        tiles="cartodbpositron",
        prefer_canvas=True
    )

    colormap = cm.LinearColormap(
        colors=['blue', 'cyan', 'green', 'yellow', 'orange', 'red'],
        vmin=min_year, vmax=max_year,
        caption='Ano de Captura'
    )
    colormap.add_to(m)

    # Converter DF para GeoJSON com índice para controle de densidade
    features = []
    for i, row in df.iterrows():
        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [float(row.lng), float(row.lat)]
            },
            "properties": {
                "index": i,
                "year": int(row.year),
                "date": str(row.date),
                "color": colormap(row.year)
            }
        }
        features.append(feature)
    
    geojson_str = json.dumps({"type": "FeatureCollection", "features": features})
    
    custom_script = f"""
    <div id="control-panel" style="
        position: fixed; 
        bottom: 30px; left: 30px; width: 320px; 
        z-index: 9999; background: white; padding: 15px; 
        border: 2px solid grey; border-radius: 10px; opacity: 0.95;
        font-family: Arial, sans-serif; box-shadow: 0 4px 15px rgba(0,0,0,0.2);">
        
        <h4 style="margin-top:0; color: #2c3e50; margin-bottom: 12px; border-bottom: 1px solid #eee; padding-bottom: 5px;">Explorador Street View DF</h4>
        
        <!-- Densidade -->
        <div style="margin-bottom: 15px;">
            <strong style="font-size: 13px; color: #34495e;">Densidade da Amostra:</strong>
            <div style="margin-top: 5px; display: flex; justify-content: space-between;">
                <label style="font-size: 11px; cursor:pointer; color: black;"><input type="radio" name="density" value="1000"> 1k</label>
                <label style="font-size: 11px; cursor:pointer; color: black;"><input type="radio" name="density" value="2000"> 2k</label>
                <label style="font-size: 11px; cursor:pointer; color: black;"><input type="radio" name="density" value="5000"> 5k</label>
                <label style="font-size: 11px; cursor:pointer; color: black;"><input type="radio" name="density" value="10000" checked> 10k</label>
            </div>
        </div>

        <!-- Filtro de Data -->
        <div style="margin-bottom: 10px; color: black; font-size: 13px;">
            <strong>Ano Selecionado:</strong> <span id="year-label" style="font-weight: bold; color: #d35400;">Todos</span>
        </div>
        <input id="year-slider" type="range" min="{min_year-1}" max="{max_year}" value="{min_year-1}" step="1" style="width:100%; cursor: pointer;">
        
        <div style="margin-top: 10px; background: #f9f9f9; padding: 8px; border-radius: 5px;">
            <label style="display: block; font-size: 12px; margin-bottom: 4px; cursor: pointer; color: black;">
                <input type="radio" name="filter-mode" value="exact" checked> Apenas este ano
            </label>
            <label style="display: block; font-size: 12px; cursor: pointer; color: black;">
                <input type="radio" name="filter-mode" value="onwards"> Deste ano em diante (>=)
            </label>
        </div>
        
        <!-- Contador -->
        <div style="margin-top: 15px; padding: 10px; background: #2c3e50; color: white; border-radius: 5px; text-align: center;">
            <span style="font-size: 12px; opacity: 0.8;">Pontos visíveis:</span><br>
            <strong id="visible-count" style="font-size: 18px;">0</strong>
        </div>
    </div>

    <script>
    document.addEventListener("DOMContentLoaded", function() {{
        var map = {m.get_name()};
        var fullData = {geojson_str};
        var minYear = {min_year};
        var geojsonLayer;

        function updateMap() {{
            var density = parseInt(document.querySelector('input[name="density"]:checked').value);
            var selectedYear = parseInt(document.getElementById('year-slider').value);
            var mode = document.querySelector('input[name="filter-mode"]:checked').value;
            var label = document.getElementById('year-label');
            var countDisplay = document.getElementById('visible-count');

            label.innerHTML = (selectedYear < minYear) ? "Todos" : selectedYear;

            if (geojsonLayer) map.removeLayer(geojsonLayer);

            var visibleCount = 0;

            geojsonLayer = L.geoJson(fullData, {{
                pointToLayer: function (feature, latlng) {{
                    return L.circleMarker(latlng, {{
                        radius: 3.5,
                        fillColor: feature.properties.color,
                        color: "#000",
                        weight: 0.3,
                        opacity: 1,
                        fillOpacity: 0.7
                    }});
                }},
                filter: function(feature) {{
                    // Filtro 1: Densidade (Baseado no índice original do CSV)
                    if (feature.properties.index >= density) return false;

                    // Filtro 2: Data
                    var matchDate = true;
                    if (selectedYear >= minYear) {{
                        if (mode === 'exact') {{
                            matchDate = (feature.properties.year === selectedYear);
                        }} else {{
                            matchDate = (feature.properties.year >= selectedYear);
                        }}
                    }}
                    
                    if (matchDate) visibleCount++;
                    return matchDate;
                }},
                onEachFeature: function (feature, layer) {{
                    layer.bindPopup("<b>Data:</b> " + feature.properties.date);
                }}
            }}).addTo(map);

            countDisplay.innerHTML = visibleCount.toLocaleString();
        }}

        // Listeners
        document.getElementById('year-slider').oninput = updateMap;
        document.querySelectorAll('input[name="density"]').forEach(r => r.onchange = updateMap);
        document.querySelectorAll('input[name="filter-mode"]').forEach(r => r.onchange = updateMap);

        updateMap();
    }});
    </script>
    """
    
    m.get_root().html.add_child(Element(custom_script))
    m.save(OUTPUT_MAP)
    print(f"Sucesso! Mapa final com densidade, slider e contador salvo em: {OUTPUT_MAP}")

if __name__ == "__main__":
    main()
