"""
Contorno das casas do entorno. A OSM praticamente nao tem building=* nessa
regiao (so um cluster institucional pequeno, ~153 predios, nada na malha
residencial) -- por isso recorrer a dataset pre-computado por ML. Ver
PROCESSO.md secao 4 pro racional completo, inclusive o buraco real do
Microsoft bem em cima deste site (nao e' falta de cobertura do Brasil).

Tenta Microsoft Global ML Building Footprints primeiro (rapido, ~7MB por
tile); se a contagem sair zero/muito baixa na bbox exata, cai pro Google
Open Buildings v3 (mais pesado, ~365MB pra celula S2 nivel 4 inteira, mas
cobriu bem este site). NAO baixa os dois por padrao -- so o Google se o
Microsoft realmente nao cobrir (ver `MIN_ACCEPTABLE_COUNT`).

Rodar depois de fetch_osm_and_imagery.py (usa a bbox do config.py).
"""
import csv
import gzip
import json
import math
import re

import numpy as np
import requests
import s2sphere
import mapbox_earcut as earcut
from shapely.geometry import Polygon, box

import config

HOUSE_HEIGHT = 3.0  # metros -- volume unico, sem detalhe de cobertura (pedido explicito)

MIN_ACCEPTABLE_COUNT = 20  # abaixo disso, considera que o Microsoft "furou" aqui
TERRAIN_BOX = box(config.MIN_X, config.MIN_Y, config.MAX_X, config.MAX_Y)


def check_osm_buildings():
    """So pra registrar a contagem -- nao usar como fonte principal (ver
    PROCESSO.md secao 4 item 1)."""
    south, west, north, east = config.BBOX_LATLON
    query = f'''
[out:json][timeout:30];
way["building"]({south},{west},{north},{east});
out count;
'''
    r = requests.post('https://overpass-api.de/api/interpreter', data={'data': query},
                       headers={'User-Agent': 'pyops-blender-tests/1.0'}, timeout=60)
    count = int(r.json()['elements'][0]['tags']['total'])
    print('OSM building=* na bbox:', count)
    return count


def _quadkey_for_bbox_center(zoom=9):
    lat = (config.BBOX_LATLON[0] + config.BBOX_LATLON[2]) / 2
    lon = (config.BBOX_LATLON[1] + config.BBOX_LATLON[3]) / 2
    n = 2 ** zoom
    xtile = int((lon + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    quadkey = ''
    for i in range(zoom, 0, -1):
        digit, mask = 0, 1 << (i - 1)
        if xtile & mask:
            digit += 1
        if ytile & mask:
            digit += 2
        quadkey += str(digit)
    return quadkey


def try_microsoft_buildings():
    quadkey = _quadkey_for_bbox_center()
    print('quadkey Microsoft (zoom 9):', quadkey)
    csv_index = requests.get(
        'https://minedbuildings.z5.web.core.windows.net/global-buildings/dataset-links.csv', timeout=60
    ).text
    row = next((l for l in csv_index.splitlines() if f',{quadkey},' in l), None)
    if row is None:
        print('quadkey nao encontrado no indice do Microsoft')
        return []
    url = row.split(',')[2]
    r = requests.get(url, timeout=180)
    south, west, north, east = config.BBOX_LATLON
    found = []
    for line in gzip.decompress(r.content).decode('utf-8', errors='replace').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        coords = obj.get('geometry', {}).get('coordinates', [[]])[0]
        if not coords:
            continue
        clon = sum(c[0] for c in coords) / len(coords)
        clat = sum(c[1] for c in coords) / len(coords)
        if west <= clon <= east and south <= clat <= north:
            found.append([config.to_local(lon, lat) for lon, lat in coords])
    print('Microsoft: predios encontrados na bbox exata:', len(found))
    return found


def try_google_open_buildings():
    lat = (config.BBOX_LATLON[0] + config.BBOX_LATLON[2]) / 2
    lon = (config.BBOX_LATLON[1] + config.BBOX_LATLON[3]) / 2
    cell = s2sphere.CellId.from_lat_lng(s2sphere.LatLng.from_degrees(lat, lon)).parent(4)
    token = cell.to_token()
    print('S2 token (nivel 4) Google Open Buildings:', token, '-- arquivo pode ter ate ~350MB+')

    url = f'https://storage.googleapis.com/open-buildings-data/v3/polygons_s2_level_4_gzip/{token}_buildings.csv.gz'
    south, west, north, east = config.BBOX_LATLON
    found = []

    with requests.get(url, timeout=600, stream=True) as r:
        r.raise_for_status()
        gz = gzip.GzipFile(fileobj=r.raw)
        # decodifica em stream pra nao carregar o arquivo inteiro na memoria
        import io
        text_stream = io.TextIOWrapper(gz, encoding='utf-8', errors='replace')
        reader = csv.reader(text_stream)
        header = next(reader)
        lat_i, lon_i, geom_i = header.index('latitude'), header.index('longitude'), header.index('geometry')
        for row in reader:
            try:
                rlat, rlon = float(row[lat_i]), float(row[lon_i])
            except (ValueError, IndexError):
                continue
            if not (west <= rlon <= east and south <= rlat <= north):
                continue
            m = re.search(r'POLYGON\s*\(\((.*?)\)\)', row[geom_i])
            if not m:
                continue
            pts = [tuple(map(float, pair.strip().split(' '))) for pair in m.group(1).split(',')]
            found.append([config.to_local(plon, plat) for plon, plat in pts])

    print('Google Open Buildings: predios encontrados na bbox exata:', len(found))
    return found


def clip_and_extrude_prep(footprints, min_area=4.0):
    """So filtra/recorta no terreno -- a extrusao de verdade (altura,
    cota Z pelo raycast do terreno) acontece em blender_snippets.py, porque
    precisa do bpy."""
    kept = []
    for raw in footprints:
        poly = Polygon(raw)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty:
            continue
        poly = poly.intersection(TERRAIN_BOX)
        if poly.is_empty or poly.geom_type != 'Polygon' or poly.area < min_area:
            continue
        kept.append(list(poly.exterior.coords)[:-1])
    return kept


def build_mesh_prep(footprints):
    """Gera tampa de baixo + tampa de cima + paredes por footprint, prontos
    pra virar mesh no Blender. A cota Z (base = raycast no terreno, topo =
    base + HOUSE_HEIGHT) so pode ser calculada dentro do Blender -- aqui
    cada vertice so carrega (x, y, building_id, is_top); blender_snippets.py
    resolve o Z."""
    verts_xy, is_top, building_id, faces = [], [], [], []

    def add_ring(ring, bid, top):
        idxs = []
        for x, y in ring:
            verts_xy.append((x, y))
            is_top.append(top)
            building_id.append(bid)
            idxs.append(len(verts_xy) - 1)
        return idxs

    n_buildings = 0
    for ring in footprints:
        if len(ring) < 3:
            continue
        coords_arr = np.array(ring, dtype=np.float64)
        tris = np.array(earcut.triangulate_float64(coords_arr, np.array([len(ring)], dtype=np.uint32))).reshape(-1, 3)
        if len(tris) == 0:
            continue
        bid = n_buildings
        bottom = add_ring(ring, bid, False)
        top = add_ring(ring, bid, True)
        for t in tris:
            faces.append([bottom[t[0]], bottom[t[2]], bottom[t[1]]])  # tampa de baixo, sentido invertido
        for t in tris:
            faces.append([top[t[0]], top[t[1]], top[t[2]]])  # tampa de cima
        for k in range(len(ring)):
            k2 = (k + 1) % len(ring)
            faces.append([bottom[k], bottom[k2], top[k2], top[k]])  # parede
        n_buildings += 1

    return {'verts_xy': verts_xy, 'is_top': is_top, 'building_id': building_id,
            'faces': faces, 'n_buildings': n_buildings, 'height': HOUSE_HEIGHT}


def main():
    check_osm_buildings()
    footprints = try_microsoft_buildings()
    if len(footprints) < MIN_ACCEPTABLE_COUNT:
        print(f'Microsoft cobriu so {len(footprints)} predios (< {MIN_ACCEPTABLE_COUNT}) -- tentando Google Open Buildings')
        footprints = try_google_open_buildings()

    kept = clip_and_extrude_prep(footprints)
    print('footprints finais (pos-recorte/filtro):', len(kept))

    mesh_prep = build_mesh_prep(kept)
    print('predios preparados:', mesh_prep['n_buildings'])
    json.dump(mesh_prep, open(config.out_path('houses_mesh.json'), 'w'))
    print('salvo em', config.out_path('houses_mesh.json'))


if __name__ == '__main__':
    main()
