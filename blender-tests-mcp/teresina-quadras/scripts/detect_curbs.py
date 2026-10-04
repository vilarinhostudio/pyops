"""
Deteccao de meio-fio por transecto perpendicular, em espaco de cor Lab, na
imagem de satelite de alta resolucao. Ver PROCESSO.md secao 2 (passo 4) e
secao 3 (itens 1-3) pros problemas conhecidos disso -- notavelmente: sombra
de predio / telhado escuro perto da via causa subestimativa de largura, sem
solucao facil so com cor. O piso de largura minima (build_streets.py) existe
por causa disso.

Rodar depois de fetch_osm_and_imagery.py. Gera data/curb_raw.json: lista de
{id, type, stations:[{s,x,y,left,right,occluded}]} por way (nao por rua --
o encadeamento por nome acontece em build_streets.py).
"""
import json
import math
import numpy as np
import cv2
from shapely.geometry import LineString, Point
from shapely.ops import unary_union
import config

# --- parametros (ver tabela no PROCESSO.md secao 7) ---
STEP_LEN = 3.0          # espacamento entre estacoes ao longo do eixo da via
TSTEP = 0.5             # passo do transecto perpendicular
MAX_R = 15.0            # raio maximo de busca por meio-fio
THRESH = 22.0           # limiar de distancia Lab pra considerar "fora da via"
MIN_RUN = 4             # amostras seguidas acima do limiar pra confirmar borda (4*0.5=2m)
REF_HALF_WINDOW = 2.5   # janela ao longo do eixo pra referencia de cor robusta
REF_STEP = 0.5
BRIDGE_BUFFER = 20.0    # buffer em volta da propria ponte, tudo dentro disso e' "ocluido"
PLAUSIBLE_WIDTH = (1.0, 26.0)  # descarta deteccao fora dessa faixa (provavel erro)


def load_ways():
    data = json.load(open(config.out_path('overpass_raw.json'), encoding='utf-8'))
    ways = []
    for el in data['elements']:
        if el['type'] != 'way' or 'highway' not in el.get('tags', {}):
            continue
        coords = [config.to_local(pt['lon'], pt['lat']) for pt in el.get('geometry', []) if pt]
        if len(coords) < 2:
            continue
        ways.append({
            'id': el['id'],
            'type': el['tags']['highway'],
            'is_bridge': el['id'] in config.BRIDGE_WAY_IDS,
            'coords': coords,
        })
    return ways


def build_bridge_occlusion(ways):
    bridge_lines = [LineString(w['coords']) for w in ways if w['is_bridge']]
    if not bridge_lines:
        return None
    return unary_union([ln.buffer(BRIDGE_BUFFER) for ln in bridge_lines])


def make_sampler(img_path):
    img_bgr = cv2.imread(img_path, cv2.IMREAD_COLOR)
    lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    H, W = img_bgr.shape[:2]
    minX, maxX, minY, maxY = config.MIN_X, config.MAX_X, config.MIN_Y, config.MAX_Y

    def sample_lab(x, y):
        col = (x - minX) / (maxX - minX) * (W - 1)
        row = (maxY - y) / (maxY - minY) * (H - 1)
        if col < 1 or col > W - 2 or row < 1 or row > H - 2:
            return None
        x0, y0 = int(math.floor(col)), int(math.floor(row))
        x1, y1 = x0 + 1, y0 + 1
        fx, fy = col - x0, row - y0
        return (lab[y0, x0] * (1 - fx) * (1 - fy) + lab[y0, x1] * fx * (1 - fy) +
                lab[y1, x0] * (1 - fx) * fy + lab[y1, x1] * fx * fy)

    return sample_lab


def robust_ref(sample_lab, mx, my, tx, ty):
    """Mediana de varias amostras ao longo do eixo (nao so 1 ponto no
    centro) -- evita que a referencia caia numa faixa de pintura ou mancha
    isolada."""
    samples = []
    d = -REF_HALF_WINDOW
    while d <= REF_HALF_WINDOW + 1e-6:
        c = sample_lab(mx + tx * d, my + ty * d)
        if c is not None:
            samples.append(c)
        d += REF_STEP
    if not samples:
        return None
    return np.median(np.array(samples), axis=0)


def find_edge(sample_lab, mx, my, dirx, diry, sign, ref):
    consec_high = 0
    last_low_d = 0.0
    d = 0.0
    while d < MAX_R:
        d += TSTEP
        c = sample_lab(mx + dirx * d * sign, my + diry * d * sign)
        if c is None:
            return None
        dist = np.linalg.norm(c - ref)
        if dist > THRESH:
            consec_high += 1
            if consec_high >= MIN_RUN:
                return last_low_d + TSTEP * 0.5
        else:
            consec_high = 0
            last_low_d = d
    return None


def detect_all():
    ways = load_ways()
    bridge_occlusion = build_bridge_occlusion(ways)
    sample_lab = make_sampler(config.out_path('esri_imagery.png'))
    normal_ways = [w for w in ways if not w['is_bridge']]

    results = []
    for w in normal_ways:
        line = LineString(w['coords'])
        total_len = line.length
        n_stations = max(2, int(total_len / STEP_LEN) + 1)
        stations = []
        for i in range(n_stations + 1):
            s = min(i * STEP_LEN, total_len)
            pt = line.interpolate(s)
            s2, s1 = min(s + 0.5, total_len), max(s - 0.5, 0)
            p2, p1 = line.interpolate(s2), line.interpolate(s1)
            dx, dy = p2.x - p1.x, p2.y - p1.y
            norm = math.hypot(dx, dy)
            if norm == 0:
                continue
            tx, ty = dx / norm, dy / norm
            px, py = -ty, tx
            occluded = bool(bridge_occlusion and bridge_occlusion.contains(Point(pt.x, pt.y)))
            left = right = None
            if not occluded:
                ref = robust_ref(sample_lab, pt.x, pt.y, tx, ty)
                if ref is not None:
                    left = find_edge(sample_lab, pt.x, pt.y, px, py, 1, ref)
                    right = find_edge(sample_lab, pt.x, pt.y, px, py, -1, ref)
                    if left is not None and right is not None:
                        total = left + right
                        if not (PLAUSIBLE_WIDTH[0] <= total <= PLAUSIBLE_WIDTH[1]):
                            left = right = None
            stations.append({'s': s, 'x': pt.x, 'y': pt.y, 'left': left, 'right': right, 'occluded': occluded})
        results.append({'id': w['id'], 'type': w['type'], 'stations': stations})

    valid = sum(1 for r in results for st in r['stations'] if st['left'] is not None and st['right'] is not None)
    total = sum(len(r['stations']) for r in results)
    print(f'estacoes com deteccao valida dos dois lados: {valid}/{total}')

    json.dump(results, open(config.out_path('curb_raw.json'), 'w'))
    print('salvo em', config.out_path('curb_raw.json'))


if __name__ == '__main__':
    detect_all()
