# Modelagem do entorno da Ponte Estaiada (Teresina) — processo completo

Não é relacionado ao addon PyOps. Isso é o registro de uma sessão de teste das
capacidades de MCP do Blender: gerar contorno de ruas (com meio-fio real),
rio e casas do entorno para um lote de projeto de arquitetura, a partir só de
uma captura de satélite do BlenderGIS + dados públicos.

Site: -5.068860, -42.801518 — ao lado da Ponte Estaiada Mestre João Isidoro
França, Teresina/PI. Terreno já importado no Blender via BlenderGIS
(`EXPORT_GOOGLE_SAT_WM`, `GOOGLE_SAT_WM.tif`, zoom 18).

**Não modelar a ponte** — ela existe só para dar contexto/localização.

## Índice
1. Georreferência (como sair do Blender local pra lat/lon e voltar)
2. Ruas e rio — pipeline completo
3. O que não deu certo (pra não repetir)
4. Casas do entorno — footprint
5. Estacionamento
6. Como rodar tudo de novo num site novo
7. Parâmetros ajustáveis (o que mexer se o resultado não ficar bom)

---

## 1. Georreferência

O BlenderGIS guarda a georreferência da cena em propriedades customizadas da
`Scene`, lidas assim (rodar dentro do Blender via MCP, não em Python externo):

```python
import bpy
scene = bpy.context.scene
print(scene['SRID'])       # 'EPSG:3857'
print(scene['crs x'])      # origem X em metros Web Mercator
print(scene['crs y'])      # origem Y em metros Web Mercator
print(scene['longitude'])  # ponto clicado originalmente
print(scene['latitude'])
```

Conversão **local Blender → EPSG:3857**: `X = local_x + crs_x`, `Y = local_y + crs_y`
(confirmado batendo com `scene['longitude']/['latitude']` — erro de ponto flutuante
zero). Não precisa de `pyproj`; a fórmula de Mercator esférica com R=6378137 já
fecha exatamente com o que o BlenderGIS usa:

```python
import math
R = 6378137.0
def to_local(lon, lat, crs_x0, crs_y0):
    x = R * math.radians(lon) - crs_x0
    y = R * math.log(math.tan(math.pi/4 + math.radians(lat)/2)) - crs_y0
    return x, y

def to_lonlat(x_local, y_local, crs_x0, crs_y0):
    X, Y = x_local + crs_x0, y_local + crs_y0
    lon = math.degrees(X / R)
    lat = math.degrees(2*math.atan(math.exp(Y/R)) - math.pi/2)
    return lon, lat
```

O retângulo do terreno em coordenadas locais (bounding box do mesh
`EXPORT_GOOGLE_SAT_WM`, que tem `matrix_world` identidade — confirmar isso
sempre antes de assumir) dá o recorte de tudo: `minX, maxX, minY, maxY`. Nesta
sessão: `-610.416, 612.577, -473.895, 443.350`. Isso vira bbox lat/lon pra
consultar Overpass: `sul, oeste, norte, leste = -5.073162, -42.806854,
-5.064954, -42.795868`.

`scripts/config.py` já tem esses valores fixos pra este site; pra outro site,
gerar de novo com o snippet acima.

---

## 2. Ruas e rio — pipeline completo

Ordem real (e por quê), do começo ao fim:

1. **Overpass**: baixar `highway=*`, `waterway=river`, `natural=water`,
   `amenity=parking` dentro da bbox (`scripts/fetch_osm_and_imagery.py`).
   Rodar com `User-Agent` custom — sem isso o overpass-api.de responde 406.
2. **Rio**: polígono da relação `natural=water`, recortado no terreno.
   Precisa achatar numa cota Z única (ver §3.7) — sem isso o relevo (SRTM
   ~30m/célula) faz o rio "ondular".
3. **Imagem de satélite maior resolução**: a textura que o BlenderGIS já
   baixou (`GOOGLE_SAT_WM.tif`, ~0,6 m/px) é grossa demais pra detectar
   meio-fio. Trocada pelo **Esri World Imagery** (REST público, sem API key,
   `scripts/fetch_osm_and_imagery.py::fetch_esri_imagery`), ~0,3 m/px —
   qualidade bem maior nessa região específica (variável por lugar; testar
   antes de assumir).
4. **Detecção de meio-fio por transecto** (`scripts/detect_curbs.py`): pra
   cada rua, anda de 3 em 3m no eixo, e em cada ponto lança um corte
   perpendicular nos pixels em espaço de cor Lab, procurando a transição
   asfalto → não-asfalto. Ver §3 pros dois problemas grandes disso.
5. **Encadeamento por nome** (`scripts/build_streets.py::build_chains`): a
   OSM corta a mesma rua real em vários `way`s a cada esquina. Agrupar por
   `tags['name']` (grafo de conectividade via `networkx`, um nó por ponta de
   via) antes de calcular largura — senão a largura pula a cada esquina.
6. **Confiabilidade + piso de largura** (`build_streets.py::classify_and_floor`):
   classificar cada rua como confiável ou não pela oscilação ponto-a-ponto
   (não pela variância total — rua comprida pode variar de verdade). Onde não
   confiável, ou onde o valor fica abaixo do piso por classe viária, usar
   fallback.
7. **Continuidade de largura** (`build_streets.py::continuous_width_ribbon`):
   simplifica o eixo (Douglas-Peucker) pra achar os trechos retos, mas a
   largura usada é uma função **linear por partes** entre os pontos de
   quebra — não uma mediana constante por trecho (isso último cria um
   degrau bem na emenda).
8. **Remalhagem em quads** (`build_streets.py::build_quad_mesh`): cada rua
   vira uma faixa 1×N quads (~6m de espaçamento), cruzamentos detectados por
   interseção de linha entre TODOS os pares de rua (não só ponta-com-ponta —
   ver §3.11), virando uma única face consolidada por cruzamento (leque
   triangulado, ver §3.10 pra saber por que não é sempre um quad de verdade).
9. **Recorte no terreno**: em todo passo acima que gera geometria, recortar
   pela bbox do terreno (`shapely.box(...).intersection(...)`) — as vias e
   nomes da OSM se estendem bem além do recorte do site.
10. **Importar no Blender**: `scripts/blender_snippets.py` tem o código pra
    colar no `execute_blender_code`. Usa raycast contra o terreno pra achar
    Z real, com **nudge pro centro + repetir** quando o raio erra bem na
    borda do recorte (§3.7).

---

## 3. O que não deu certo (documentado pra não repetir)

1. **CV clássico na imagem do Google (0,6 m/px, comprimida)**: sinal ruidoso
   demais, sem separação clara de meio-fio. Resolvido trocando de fonte de
   imagem (Esri), não o algoritmo.
2. **Referência de cor = 1 pixel no centro da via**: se cair numa faixa de
   pintura/mancha, a referência fica errada e tudo ao redor parece "fora da
   via" a menos de 1m. Melhorado com mediana de várias amostras ao longo do
   eixo (±2,5m) — ajudou, mas não resolveu tudo (ver item 3).
3. **Sombra de prédio / telhado com placa solar escura perto da via**: causa
   real da maioria das "vias finas". O algoritmo às vezes acha que saiu do
   asfalto a poucos metros do centro porque um objeto escuro do lado tem
   distância de cor grande. **Sem solução fácil** com detecção por cor
   simples — mais da metade das estações precisou do piso de largura mínima
   por classe pra compensar. Se for atacar isso de verdade, precisa de
   detecção de borda estrutural (linha do meio-fio em si) em vez de região
   de cor, ou um modelo treinado.
4. **Largura por `way` da OSM em vez de por rua real**: mesma rua cortada em
   N `way`s pela OSM = N larguras independentes = degrau em toda esquina.
   Resolvido agrupando por `name` (só ajuda pra ruas nomeadas — vias sem
   nome continuam por `way` isolado).
5. **Largura constante por trecho simplificado**: resolve o serrilhado mas
   cria um novo degrau na emenda entre trechos (a largura pula quando dois
   trechos vizinhos têm mediana diferente). Resolvido interpolando
   linearmente a largura entre os pontos de quebra em vez de usar valor
   constante por trecho.
6. **Bug de parametrização**: interpolar posição na linha simplificada
   usando a distância acumulada da linha ORIGINAL (`shapely` — a linha
   simplificada tem comprimento total menor, principalmente em curva).
   Corrigido interpolando manualmente dentro de cada sub-trecho usando as
   coordenadas conhecidas dos pontos de quebra, não `line.interpolate(s)`
   direto.
7. **Raycast que erra exatamente na borda do recorte**: ponto de flutuação
   faz o raio passar de raspão na borda do mesh e não bater em nada; o
   fallback (`54.0`, coincidentemente perto da cota mínima real do terreno)
   mascarava isso — dava geometria "grudada no chão" sem avisar. Corrigido
   com "aproxima 1-20% em direção ao centro do terreno e tenta de novo" antes
   de usar o fallback de verdade. Precisou disso em três lugares
   independentes (rio, ruas, casas) — **sempre usar esse padrão** ao
   drapear qualquer coisa nova.
8. **Área grande e plana (lote de estacionamento) seguindo o relevo
   vértice-a-vértice**: o DEM do BlenderGIS aqui é muito grosseiro (SRTM,
   ~41×30 px pro terreno inteiro, ~30m por célula) — uma área plana de
   verdade sai com fachetas visíveis. Resolvido achatando numa cota única
   (percentil alto de uma grade de amostras internas, não só o centro).
9. **`bpy` `Curve.fill_mode` pra preencher polígono com buraco**: funciona
   às vezes, falha silenciosamente (preenchimento incompleto) em
   configurações com muitos buracos próximos. Abandonado por malha direta.
10. **`mathutils.geometry.tessellate_polygon`**: mais robusto que o fill de
    curva, mas ainda falhou (triangulação quebrada/sobreposta) numa
    configuração específica (o laço do estacionamento) mesmo com a geometria
    de entrada validada como correta via `shapely`. Trocado por
    **`mapbox_earcut`** (`pip install mapbox_earcut`), que resolveu todos os
    casos testados. Preferir `earcut` desde o início da próxima vez.
11. **Detectar cruzamento só por ponta de rua coincidente**: sub-contou
    demais (achou 5 cruzamentos reais de ~90). Uma rua contínua (já
    encadeada por nome) **atravessa** uma transversal sem ter uma "ponta"
    ali — só teria ponta se as ruas realmente terminassem no mesmo nó.
    Corrigido calculando `LineString.intersection()` entre TODOS os pares de
    rua (O(n²), mas n~80 é rápido).
12. **Face de junção como n-gono convexo único**: só funcionou em 2 de 88
    cruzamentos. Unir as "pontas" (stubs) de cada rua que chega no
    cruzamento tende a formar uma cruz/estrela **côncava**, não convexa —
    natural quando as ruas são bem mais compridas que largas. Aceito o
    leque triangulado como resultado final (ainda é uma única área
    consolidada, só não é uma face sem diagonal).
13. **`pip install X`** nesse ambiente instala no Python do **addon do
    Blender**, não no Python que o Bash roda por padrão — sempre usar
    `python -m pip install X` (não `pip install X` sozinho) pra garantir que
    vai pro interpretador certo.
14. **Heredoc do Bash (`<< 'EOF'`)** falha esporadicamente com "unexpected
    EOF" nesse ambiente (Git Bash) pra scripts Python grandes. Usar a
    ferramenta de escrita de arquivo direto (`Write`) em vez de heredoc
    sempre que o script passar de ~30 linhas.

---

## 4. Casas do entorno — footprint

Testado nesta ordem, cada um documentado porque o próximo só fez sentido
depois do anterior falhar:

1. **OSM `building=*`** direto na bbox: só 153 edificações, concentradas
   num cluster institucional pequeno (não cobre a malha residencial). Ver
   `scripts/fetch_buildings.py::check_osm_buildings`.
2. **Microsoft Global ML Building Footprints**: cobre o Brasil (confirmado —
   1154 quadkeys), mas tem um **buraco real e local** bem em cima deste
   site específico (0 edificações a 600m do centro do site, crescendo pra
   fora — não é falta de cobertura do Brasil, é falha pontual da fonte de
   imagem usada no pipeline deles ali). Quadkey desse site: `211000233`
   (zoom 9). **Não assumir que "não achou aqui" = "não cobre o Brasil"** —
   testar os dois datasets sempre, porque as fontes de imagem/época são
   diferentes por pipeline.
3. **Google Open Buildings v3**: cobriu bem (1580 edificações, confiança
   0,65–0,96, mediana 0,84 — conferido visualmente contra a imagem Esri,
   contornos batem até com garagem/anexo). Célula S2 nível 4 usada: `079`
   (~365MB gzip, formato CSV com WKT na coluna `geometry` — **não é JSON
   apesar do nome do arquivo sugerir**, ver `scripts/fetch_buildings.py`).
   Extrudado em bloco simples (sem telhado), altura única 3m, base pela
   mediana do raycast no terreno sob cada footprint.

---

## 5. Estacionamento

A OSM tem o lote como `amenity=parking` já pronto (way `683259197`,
capacidade 200, operador "Ponte Estaiada" — bate exatamente com o
estacionamento da ponte). Usar esse polígono direto em vez de tentar
reconstruir a partir das vias de serviço internas (que a OSM mapeia como
`highway=service` fragmentadas e com leitura de cor ruim — confundem faixa
pintada com canteiro de terra). As vias `service` que caem dentro do lote
(>70% das estações dentro do polígono, com 8m de margem) são excluídas da
malha de ruas. O polígono do lote é achatado (mesmo motivo do §3.8) e
triangulado plano (`earcut`), sem o esquema de quads — é área, não via.

---

## 6. Como rodar tudo de novo (site novo ou mesmo site)

1. No Blender, já ter o BlenderGIS com o terreno (satélite + `dem_query`)
   baixado e assado (o `EXPORT_GOOGLE_SAT_WM` ou equivalente).
2. Rodar o snippet de georreferência (§1) dentro do Blender via
   `execute_blender_code`, copiar `crs_x`, `crs_y`, e a bbox local do
   terreno pra `scripts/config.py`.
3. `python scripts/fetch_osm_and_imagery.py` — baixa Overpass (ruas, rio,
   estacionamento) e a imagem Esri.
4. `python scripts/detect_curbs.py` — detecção de meio-fio na imagem Esri
   (demora alguns minutos, é o passo mais pesado).
5. `python scripts/build_streets.py` — encadeamento, confiabilidade, piso de
   largura, continuidade, malha em quads. Gera `data/ruas_quadmesh.json`.
6. `python scripts/build_river_and_parking.py` — gera `data/rio_mesh.json` e
   os dados do estacionamento.
7. `python scripts/fetch_buildings.py` — tenta Microsoft, cai pro Google Open
   Buildings se o Microsoft não cobrir; gera `data/houses_mesh.json`.
8. Colar os snippets de `scripts/blender_snippets.py` no
   `execute_blender_code`, um de cada vez (`Ruas`, `Rio`, `Casas`), pra
   importar de fato no Blender.

Todos os scripts em `scripts/` rodam com Python comum (fora do Blender) —
só o passo 8 precisa do `bpy` de dentro do Blender.

Dependências Python: `requests`, `numpy`, `opencv-python` (`cv2`), `shapely`,
`scipy`, `networkx`, `mapbox_earcut`, `s2sphere`. Instalar sempre com
`python -m pip install <pacote>` (ver item 13 do §3).

---

## 7. Parâmetros ajustáveis

Tudo isso está no topo de `scripts/detect_curbs.py` e `scripts/build_streets.py`,
com comentário de pra que serve cada um:

| Parâmetro | Valor usado | Efeito se mudar |
|---|---|---|
| `STEP_LEN` (espaçamento de estação) | 3 m | menor = mais preciso e mais lento |
| `TSTEP` (passo do transecto) | 0.5 m | resolução da busca de meio-fio |
| `MAX_R` (raio máx. de busca) | 15 m | até que largura de via detecta |
| `THRESH` (limiar de distância Lab) | 22 | mais alto = menos sensível a sombra, mas também menos sensível a meio-fio real |
| `MIN_RUN` (amostras sustentadas) | 4 (=2m) | mais alto = menos falso positivo, mais lento pra confirmar borda real |
| `JUMP_THRESH` (confiabilidade) | 4 m | acima disso a rua vira "não confiável" e usa piso/mediana da classe |
| `WIDTH_FLOOR` (piso por classe) | 4–7 m | ver §3.3 — é rede de segurança, não detecção real |
| `SIMPLIFY_TOL` (Douglas-Peucker) | 2 m | maior = mais reto, risco de achatar curva real |
| `BREAKPOINT_WINDOW` | 3 m | janela pra achar largura robusta em cada ponto de quebra |
| `TARGET_SPACING` (quads) | 6 m | espaçamento pedido pelo Arthur |
| `MIN_INSET`/`MAX_INSET` (junção) | 2–8 m | tamanho do "buraco" reservado pra face de cruzamento |

Se o resultado sair ruim de novo, o primeiro lugar pra olhar é `THRESH` (sombra
demais na imagem = baixar; via não detectada = pode estar alto demais) e
`WIDTH_FLOOR` (se muitas vias baterem no piso, é sinal de que a detecção não
está funcionando bem naquela região, não que o piso está errado).
