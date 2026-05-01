import os
import pandas as pd
import shutil
import argparse
from pathlib import Path

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-dir", type=str, default="datasets/df_2024_proportional", help="Caminho para o diretório do dataset")
    parser.add_argument("--output-dir", type=str, default="anchor_set_export_proportional", help="Caminho da pasta de exportação")
    return parser.parse_args()

def main():
    args = parse_args()
    DATASET_DIR = Path(args.dataset_dir)
    ANCHOR_CSV = DATASET_DIR / "anchor_set_sample.csv"
    OUTPUT_DIR = Path(args.output_dir)

    if not ANCHOR_CSV.exists():
        print(f"Erro: {ANCHOR_CSV} não encontrado.")
        return

    print(f"Lendo amostra de: {ANCHOR_CSV}")
    df = pd.read_csv(ANCHOR_CSV)
    
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    new_rows = []
    
    print(f"Copiando {len(df)} imagens para {OUTPUT_DIR}...")
    
    for i, row in df.iterrows():
        # Construir o caminho da imagem usando batch_id e image_id
        batch_id = row['batch_id']
        image_id = row['image_id']
        src_path = DATASET_DIR / batch_id / "images" / f"{image_id}.jpg"
        
        if not src_path.exists():
            print(f"Aviso: Imagem não encontrada: {src_path}")
            continue
            
        # Nome simplificado ou mantendo o original
        dest_filename = f"{image_id}.jpg"
        dest_path = OUTPUT_DIR / dest_filename
        
        shutil.copy2(src_path, dest_path)
        
        # Guardar metadados simplificados
        github_repo = "https://raw.githubusercontent.com/GUIA-DD/dataset-streetview/main"
        new_rows.append({
            "anchor_rank": row['anchor_rank'],
            "image_id": image_id,
            "lat": row['pano_lat'],
            "lng": row['pano_lng'],
            "date": row['capture_date'],
            "local_path": dest_filename,
            "url": f"{github_repo}/{batch_id}/images/{image_id}.jpg"
        })
        
        if (i + 1) % 100 == 0:
            print(f"Processadas {i + 1}/{len(df)} imagens...")

    # Salvar metadados da exportação
    pd.DataFrame(new_rows).to_csv(OUTPUT_DIR / "metadata.csv", index=False)
    
    print("\nExportação concluída!")
    print(f"Pasta: {OUTPUT_DIR}")
    print(f"Total de imagens: {len(new_rows)}")
    print("Dica: Você pode zipar esta pasta e enviar para seus colegas.")

if __name__ == "__main__":
    main()
