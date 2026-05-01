# Dataset Street View DF

Pipeline para montar um dataset do Google Street View no Distrito Federal com:

- imagens somente de `2024` em diante
- pontos distribuídos ao longo da malha viária do DF
- suporte a `4` headings cardinais por ponto: `0, 90, 180, 270`
- lotes independentes de `1000` imagens
- `metadata.csv` por lote e `metadata_all.csv` consolidado no nível de imagem
- `locations.csv` por lote e `locations_all.csv` consolidado no nível de ponto
- `image_url` no CSV apontando para o repositório `GUIA-DD/dataset-streetview`
- `anchor_set_sample.csv` para amostrar um conjunto inicial de imagens como no paper

## Estrutura de saída

Cada lote fica assim:

```text
datasets/df_2024_proportional/
  anchor_set_sample.csv
  batches.csv
  locations_all.csv
  metadata_all.csv
  summary.json
  batch_0001/
    locations.csv
    metadata.csv
    images/
      <image_id>.jpg
  batch_0002/
    locations.csv
    metadata.csv
    images/
      <image_id>.jpg
```

Cada `batch_XXXX` é balanceado espacialmente para poder ser usado sozinho. No modo cardinal, cada lote de `1000` imagens corresponde a `250` localizações com `4` vistas por localização. Se quiser um dataset maior, basta concatenar os lotes.

## Variável de ambiente

O script lê a chave em `API_KEY` via `.env`.

Por padrão, a URL pública das imagens é montada usando:

```text
https://github.com/GUIA-DD/dataset-streetview
```

O campo `image_url` usa o formato `raw.githubusercontent.com` para acesso direto ao arquivo. Se precisar trocar o repo ou branch, use `--github-repo-url` e `--github-branch`.

## Validação local sem API

Usa o CSV já existente no repositório só para validar a lógica de particionamento:

```bash
python fetch_data.py \
  --input-csv streetview_df_locations.csv \
  --min-year 2024 \
  --heading-set cardinal \
  --batch-size 1000 \
  --max-images 2000 \
  --skip-download \
  --output-dir datasets/df_preview_cardinal
```

## Coleta real no DF

Exemplo para montar até `8000` imagens em `8` lotes de `1000` (`4` vistas por ponto):

```bash
python fetch_data.py \
  --place-query "Distrito Federal, Brazil" \
  --min-year 2024 \
  --heading-set cardinal \
  --sampling-strategy proportional \
  --batch-size 1000 \
  --max-images 8000 \
  --candidate-multiplier 2.0 \
  --output-dir datasets/df_2024_proportional \
  --cache-dir cache/df_2024_proportional
```

Notas:

- **Estratégia de Amostragem (`--sampling-strategy`):** Você pode escolher entre duas estratégias de amostragem de pontos:
  - `uniform` (Padrão): Força uma distribuição espacialmente uniforme sobrepondo uma grade de 5x5km. Garante que áreas rurais e periféricas tenham representatividade igual às áreas densamente urbanizadas. Ideal para máxima diversidade visual.
  - `proportional`: Faz uma amostragem puramente aleatória sobre a malha viária, resultando em uma distribuição proporcional à densidade de ruas. Reflete melhor a "experiência média" da população e áreas de maior tráfego, alinhando-se com abordagens comuns em estudos urbanos.
- No modo `cardinal`, `batch-size` e `max-images` devem ser múltiplos de `4`.
- Se quiser mudar a densidade da amostragem, use `--candidate-spacing-m`.
- Se quiser limitar o número de consultas de metadata, use `--max-metadata-requests`.
- Para apenas gerar os CSVs sem baixar JPEGs, adicione `--skip-download`.
- Para desligar a escrita do `anchor_set_sample.csv`, use `--anchor-size 0`.
- Se a API retornar excesso de quota, o script faz backoff exponencial usando `--quota-backoff-initial-s`, `--quota-backoff-max-s` e `--quota-max-retries`.
- Se a quota continuar excedida, a coleta para de forma limpa e deixa o restante como pendente para retomada posterior.
- Reexecutar o mesmo comando reaproveita metadata em cache e pula imagens já baixadas.

## Exportando a Amostra Inicial (Anchor Set)

O script `fetch_data.py` gera automaticamente um arquivo `anchor_set_sample.csv` contendo uma amostra representativa de 1.000 imagens (ou o valor definido em `--anchor-size`). Esta amostra pode ser utilizada para validação, calibração ou testes antes do processamento total.

Para extrair e exportar de forma simples estas imagens em uma única pasta, utilize o script de amostragem:

```bash
python sample_anchor_images.py
```

Isso fará o seguinte:
1. Lera o arquivo `anchor_set_sample.csv` gerado durante a coleta.
2. Criará uma nova pasta dedicada chamada `anchor_set_export`.
3. Copiará exclusivamente as imagens sorteadas (1.000) para este novo diretório local.
4. Produzirá um arquivo `metadata.csv` enxuto na nova pasta, consolidando colunas vitais: `ID, Latitude, Longitude, Data, Arquivo Local e Link Público (URL)`.
