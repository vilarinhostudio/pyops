"""
Pipeline completo pra ir de curb_raw.json (deteccao por way, ruidosa) ate
uma malha de quads limpa por rua real, com cruzamento como face unica.
Ver PROCESSO.md secoes 2 e 3 pro racional de cada etapa -- os comentarios
aqui marcam só o "por que", nao repetem o que já ta la.

Rodar depois de detect_curbs.py. Gera data/ruas_quadmesh.json:
  {verts: [[x,y],...], quads: [[i,i,i,i],...], tris/ngons: [[i,i,i,...],...]}
(faces de tamanho variavel -- Blender aceita ngon em from_pydata, entao
"tris" aqui as vezes tem mais de 3 indices quando um cruzamento virou ngon
convexo em vez de leque).
"""
import json
import math
from collections import defaultdict

import numpy as np
import networkx as nx
from scipy.ndimage import median_filter
from shapely.geometry import Polygon, LineString, Point, box
from shapely.ops import unary_union
from shapely.geometry.polygon import orient
import mapbox_earcut as earcut

import config

# --- parametros (ver tabela no PROCESSO.md secao 7) ---
JUMP_THRESH = 4.0        # oscilacao media ponto-a-ponto acima disso = via "nao confiavel"
MIN_PAIRS = 2
SMOOTH_WIN = 5           # janela do filtro de mediana (so pra vias confiaveis)
SIMPLIFY_TOL = 2.0       # tolerancia do Douglas-Peucker pro eixo
BREAKPOINT_WINDOW = 3.0  # janela pra largura robusta em cada ponto de quebra
MIN_LEN_FOR_SIMPLIFY = 15.0
TARGET_SPACING = 6.0     # espacamento dos quads ao longo de cada rua
MIN_INSET, MAX_INSET = 2.0, 8.0   # tamanho do "buraco" reservado pra cruzamento
JUNCTION_ROUND = 1       # decimais pra juntar pontos de cruzamento quase iguais
NGON_MAX_SIDES = 8
NGON_CONVEXITY_MIN = 0.85  # area / area_do_casco_convexo -- abaixo disso vira leque

WIDTH_FLOOR = {  # metros, total (os dois lados somados) -- rede de seguranca, nao deteccao
    'primary': 7.0, 'primary_link': 6.0,
    'secondary': 6.0, 'secondary_link': 6.0,
    'tertiary': 6.0,
    'residential': 5.0,
    'service': 4.0,
    'footway': 1.5,
}
DEFAULT_FLOOR = 5.0

TERRAIN_BOX = box(config.MIN_X, config.MIN_Y, config.MAX_X, config.MAX_Y)


def nk(pt, prec=2):
    return (round(pt[0], prec), round(pt[1], prec))


def build_chains(ways_by_id):
    """Agrupa ways da OSM que tem o mesmo tags['name'] em ruas continuas de
    verdade, andando pelo grafo de conectividade (ponta compartilhada). Sem
    isso, a mesma rua real (cortada em N ways pela OSM a cada esquina) vira
    N larguras independentes -- ver PROCESSO.md secao 3 item 4."""
    groups = defaultdict(list)
    for wid, w in ways_by_id.items():
        key = ('name', w['name']) if w['name'] else ('solo', wid)
        groups[key].append(wid)

    chains = []
    for key, wids in groups.items():
        if len(wids) == 1:
            chains.append([(wids[0], False)])
            continue
        G = nx.MultiGraph()
        for wid in wids:
            w = ways_by_id[wid]
            G.add_edge(nk(w['coords'][0]), nk(w['coords'][-1]), way_id=wid)
        for comp in nx.connected_components(G):
            sub = G.subgraph(comp)
            degrees = dict(sub.degree())
            deg1 = [n for n, d in degrees.items() if d == 1]
            bad = any(d > 2 for d in degrees.values())
            if bad or len(deg1) not in (0, 2):
                # nome reaproveitado em topologia que nao e' um caminho simples
                # (ramificacao com o mesmo nome) -- nao arrisca, mantem solto
                for u, v, d in sub.edges(data=True):
                    chains.append([(d['way_id'], False)])
                continue
            start = deg1[0] if deg1 else list(comp)[0]
            visited, order, cur = set(), [], start
            while True:
                nxt = None
                for u, v, k, d in sub.edges(cur, keys=True, data=True):
                    if d['way_id'] in visited:
                        continue
                    nxt = (u, v, d)
                    break
                if nxt is None:
                    break
                u, v, d = nxt
                wid = d['way_id']
                visited.add(wid)
                w = ways_by_id[wid]
                reversed_ = nk(w['coords'][0]) != cur
                order.append((wid, reversed_))
                cur = v if u == cur else u
            chains.append(order)
    return chains


def merge_chain_stations(chain, curb_results):
    """Concatena as estacoes (com s recalculado) dos ways de uma chain, na
    ordem/direcao corretas."""
    all_stations = []
    s_offset = 0.0
    chain_type = None
    for wid, reversed_ in chain:
        r = curb_results[wid]
        chain_type = chain_type or r['type']
        stations = list(r['stations'])
        if reversed_:
            orig_max = r['stations'][-1]['s']
            stations = [dict(st, s=orig_max - st['s']) for st in stations[::-1]]
        for st in stations:
            all_stations.append(dict(st, s=st['s'] + s_offset))
        if stations:
            s_offset = all_stations[-1]['s'] + 3.0
    return {'way_ids': [w for w, _ in chain], 'type': chain_type, 'stations': all_stations}


def interp_side(svals, vals):
    vals = np.array([np.nan if v is None else v for v in vals], dtype=float)
    valid = ~np.isnan(vals)
    if valid.sum() == 0:
        return None
    svals = np.array(svals)
    vals[~valid] = np.interp(svals[~valid], svals[valid], vals[valid])
    return vals


def is_reliable(stations):
    """Confiabilidade pela OSCILACAO ponto-a-ponto, nao pela variancia
    total -- uma rua comprida pode variar de largura de verdade ao longo do
    percurso sem que isso seja ruido de deteccao. Ver PROCESSO.md secao 3."""
    widths = sorted((st['s'], st['left'] + st['right']) for st in stations
                     if st['left'] is not None and st['right'] is not None)
    if len(widths) < MIN_PAIRS + 1:
        return len(widths) >= 3
    jumps = [abs(widths[i + 1][1] - widths[i][1]) for i in range(len(widths) - 1)]
    return np.mean(jumps) <= JUMP_THRESH


class ChainModel:
    """Representa uma rua continua como: eixo simplificado (reto nos
    trechos retos) + largura(s) continua (linear por partes entre os
    pontos de quebra da simplificacao -- NAO constante por trecho, ver
    PROCESSO.md secao 3 item 5)."""

    def __init__(self, ctype, coords, svals, left_arr, right_arr):
        self.type = ctype
        self.line = LineString(coords)
        simplified = self.line.simplify(SIMPLIFY_TOL, preserve_topology=True)
        simp_coords = list(simplified.coords)
        if len(simp_coords) < 2:
            simp_coords = [coords[0], coords[-1]]
        breaks = sorted(set(round(self.line.project(Point(c)), 3) for c in simp_coords))
        if breaks[0] > 0:
            breaks[0] = 0.0
        if breaks[-1] < svals[-1]:
            breaks[-1] = svals[-1]
        svals_arr = np.array(svals)

        def local_width_at(sk, arr):
            window = (svals_arr >= sk - BREAKPOINT_WINDOW) & (svals_arr <= sk + BREAKPOINT_WINDOW)
            if window.any():
                return float(np.median(arr[window]))
            return float(arr[int(np.argmin(np.abs(svals_arr - sk)))])

        self.breaks = breaks
        self.simp_coords = simp_coords
        self.bp_left = [local_width_at(sk, np.asarray(left_arr)) for sk in breaks]
        self.bp_right = [local_width_at(sk, np.asarray(right_arr)) for sk in breaks]

    def pos_and_dir(self, s):
        """Posicao/direcao interpoladas DENTRO do sub-trecho simplificado
        usando o proprio s (arc-length da linha ORIGINAL nos breakpoints) --
        nao usar `simplified.interpolate(s)` direto: a linha simplificada
        tem comprimento total diferente (mais curta em curva), entao as
        unidades nao batem e a posicao sai distorcida (bug real, ver
        PROCESSO.md secao 3 item 6)."""
        s = min(max(s, self.breaks[0]), self.breaks[-1])
        seg = 0
        while seg < len(self.breaks) - 2 and s > self.breaks[seg + 1]:
            seg += 1
        s0, s1 = self.breaks[seg], self.breaks[seg + 1]
        x0, y0 = self.simp_coords[seg]
        x1, y1 = self.simp_coords[seg + 1]
        t = (s - s0) / (s1 - s0) if s1 > s0 else 0.0
        pos = (x0 + t * (x1 - x0), y0 + t * (y1 - y0))
        dx, dy = x1 - x0, y1 - y0
        norm = math.hypot(dx, dy) or 1.0
        return pos, (dx / norm, dy / norm)

    def width_at(self, s):
        left = float(np.interp(s, self.breaks, self.bp_left))
        right = float(np.interp(s, self.breaks, self.bp_right))
        return left, right

    def left_right_pts(self, s):
        (cx, cy), (tx, ty) = self.pos_and_dir(s)
        px, py = -ty, tx
        left, right = self.width_at(s)
        return (cx + px * left, cy + py * left), (cx - px * right, cy - py * right)


def build_models(curb_results, chains, excluded_idx, class_default, overall_default):
    models = {}
    for i, chain in enumerate(chains):
        if i in excluded_idx:
            continue
        rec = merge_chain_stations(chain, curb_results)
        stations = rec['stations']
        if len(stations) < 2:
            continue
        svals = [st['s'] for st in stations]
        coords = [(st['x'], st['y']) for st in stations]
        floor_total = WIDTH_FLOOR.get(rec['type'], DEFAULT_FLOOR)
        reliable = is_reliable(stations)

        if reliable:
            left = interp_side(svals, [st['left'] for st in stations])
            right = interp_side(svals, [st['right'] for st in stations])
        else:
            left = right = None

        if left is None or right is None:
            w = max(class_default.get(rec['type'], overall_default), floor_total)
            left_arr = np.full(len(stations), w / 2.0)
            right_arr = np.full(len(stations), w / 2.0)
        else:
            if len(left) >= 3:
                left = median_filter(left, size=min(SMOOTH_WIN, len(left)), mode='nearest')
                right = median_filter(right, size=min(SMOOTH_WIN, len(right)), mode='nearest')
            total = left + right
            below = total < floor_total
            if below.any():
                scale = np.where(below, floor_total / np.maximum(total, 0.01), 1.0)
                left = left * np.where(below, scale, 1.0)
                right = right * np.where(below, scale, 1.0)
            left_arr, right_arr = left, right

        models[i] = ChainModel(rec['type'], coords, svals, left_arr, right_arr)
    return models


def find_junctions(models):
    """Cruzamento = onde os EIXOS de duas ruas se cruzam geometricamente,
    nao so onde as pontas coincidem -- uma rua continua (ja encadeada por
    nome) atravessa uma transversal sem ter "ponta" ali. So checar pontas
    sub-conta demais (ver PROCESSO.md secao 3 item 11)."""
    junction_groups = defaultdict(list)
    idxs = list(models.keys())
    for a in range(len(idxs)):
        i = idxs[a]
        mi = models[i]
        for b in range(a + 1, len(idxs)):
            j = idxs[b]
            mj = models[j]
            if not mi.line.intersects(mj.line):
                continue
            inter = mi.line.intersection(mj.line)
            if inter.is_empty:
                continue
            pts = [inter] if inter.geom_type == 'Point' else (
                list(inter.geoms) if inter.geom_type == 'MultiPoint' else [])
            for pt in pts:
                key = (round(pt.x, JUNCTION_ROUND), round(pt.y, JUNCTION_ROUND))
                junction_groups[key].append((i, mi.line.project(pt)))
                junction_groups[key].append((j, mj.line.project(pt)))

    for i, m in models.items():
        for s_end in (m.breaks[0], m.breaks[-1]):
            pt = m.line.interpolate(s_end)
            key = (round(pt.x, JUNCTION_ROUND), round(pt.y, JUNCTION_ROUND))
            if not any(ci == i and abs(cs - s_end) < 0.5 for ci, cs in junction_groups.get(key, [])):
                junction_groups[key].append((i, s_end))

    for key in list(junction_groups.keys()):
        seen = []
        for ci, cs in junction_groups[key]:
            if not any(ci == sci and abs(cs - scs) < 0.5 for sci, scs in seen):
                seen.append((ci, cs))
        junction_groups[key] = seen
    return junction_groups


def build_quad_mesh(models, junction_groups):
    real_junctions = {k: v for k, v in junction_groups.items() if len(v) >= 2}

    chain_cuts = defaultdict(list)
    for entries in junction_groups.values():
        is_real = len(entries) >= 2
        for (ci, cs) in entries:
            m = models[ci]
            if is_real:
                l, r = m.width_at(cs)
                ins = min(max((l + r) * 0.5, MIN_INSET), MAX_INSET)
            else:
                ins = 0.0  # ponta morta de verdade -- vai ate a extremidade
            chain_cuts[ci].append((cs, ins))

    all_verts, all_quads, all_faces = [], [], []

    def add_vert(pt):
        all_verts.append((pt[0], pt[1]))
        return len(all_verts) - 1

    for i, m in models.items():
        excluded = sorted((s - ins, s + ins) for s, ins in chain_cuts.get(i, []) if ins > 0)
        merged_excl = []
        for lo, hi in excluded:
            if merged_excl and lo <= merged_excl[-1][1]:
                merged_excl[-1] = (merged_excl[-1][0], max(merged_excl[-1][1], hi))
            else:
                merged_excl.append((lo, hi))

        full_lo, full_hi = m.breaks[0], m.breaks[-1]
        segments, cur = [], full_lo
        for lo, hi in merged_excl:
            if lo > cur:
                segments.append((cur, min(lo, full_hi)))
            cur = max(cur, hi)
        if cur < full_hi:
            segments.append((cur, full_hi))
        segments = [(a, b) for a, b in segments if b - a > 0.5]

        # recorta no terreno -- os eixos da OSM se estendem bem alem do site
        clipped_geo = m.line.intersection(TERRAIN_BOX)
        parts = [clipped_geo] if clipped_geo.geom_type == 'LineString' else (
            list(clipped_geo.geoms) if hasattr(clipped_geo, 'geoms') else [])
        valid_ranges = []
        for part in parts:
            if part.is_empty or part.length < 0.01:
                continue
            cs = list(part.coords)
            s_a = m.line.project(Point(cs[0]))
            s_b = m.line.project(Point(cs[-1]))
            valid_ranges.append((min(s_a, s_b), max(s_a, s_b)))

        clipped_segments = []
        for s0, s1 in segments:
            for vlo, vhi in valid_ranges:
                lo, hi = max(s0, vlo), min(s1, vhi)
                if hi - lo > 0.5:
                    clipped_segments.append((lo, hi))

        for s0, s1 in clipped_segments:
            length = s1 - s0
            n_seg = max(1, round(length / TARGET_SPACING))
            svals_strip = [s0 + k * (length / n_seg) for k in range(n_seg + 1)]
            left_pts = [m.left_right_pts(s)[0] for s in svals_strip]
            right_pts = [m.left_right_pts(s)[1] for s in svals_strip]
            idxs_l = [add_vert(p) for p in left_pts]
            idxs_r = [add_vert(p) for p in right_pts]
            for k in range(n_seg):
                all_quads.append([idxs_l[k], idxs_l[k + 1], idxs_r[k + 1], idxs_r[k]])

    n_junction_faces = 0
    for key, entries in real_junctions.items():
        if not (config.MIN_X <= key[0] <= config.MAX_X and config.MIN_Y <= key[1] <= config.MAX_Y):
            continue
        stub_polys = []
        for ci, s_node in entries:
            m = models[ci]
            ins = min(max(sum(m.width_at(s_node)) * 0.5, MIN_INSET), MAX_INSET)
            s_lo, s_hi = max(s_node - ins, m.breaks[0]), min(s_node + ins, m.breaks[-1])
            n_sub = 4
            svals_stub = [s_lo + (s_hi - s_lo) * k / n_sub for k in range(n_sub + 1)]
            lefts = [m.left_right_pts(s)[0] for s in svals_stub]
            rights = [m.left_right_pts(s)[1] for s in svals_stub]
            poly = Polygon(lefts + rights[::-1])
            if not poly.is_valid:
                poly = poly.buffer(0)
            if not poly.is_empty:
                stub_polys.append(poly)
        if not stub_polys:
            continue
        merged = unary_union(stub_polys).intersection(TERRAIN_BOX)
        polys = [merged] if merged.geom_type == 'Polygon' else (list(merged.geoms) if hasattr(merged, 'geoms') else [])
        for poly in polys:
            if poly.is_empty or poly.area < 0.01:
                continue
            # face unica sem diagonal SO quando a forma e' simples o
            # suficiente -- unir "pontas" de rua costuma formar uma cruz
            # concava, entao isso raramente acontece (ver PROCESSO.md
            # secao 3 item 12). Fallback: leque triangulado (ainda uma
            # unica area consolidada, so nao e' uma face sem diagonal).
            ext = list(poly.exterior.coords)[:-1]
            simple_enough = False
            if len(poly.interiors) == 0 and 3 <= len(ext) <= NGON_MAX_SIDES:
                hull_area = poly.convex_hull.area
                simple_enough = hull_area > 0 and poly.area / hull_area > NGON_CONVEXITY_MIN
            if simple_enough:
                base = len(all_verts)
                for pt in ext:
                    all_verts.append((pt[0], pt[1]))
                all_faces.append([base + k for k in range(len(ext))])
            else:
                rings = [list(poly.exterior.coords)] + [list(r.coords) for r in poly.interiors]
                coords_arr = np.array([pt for ring in rings for pt in ring], dtype=np.float64)
                ring_end = np.cumsum([len(r) for r in rings]).astype(np.uint32)
                tris = np.array(earcut.triangulate_float64(coords_arr, ring_end)).reshape(-1, 3)
                base = len(all_verts)
                for pt in coords_arr:
                    all_verts.append((pt[0], pt[1]))
                for t in tris:
                    all_faces.append([int(t[0] + base), int(t[1] + base), int(t[2] + base)])
            n_junction_faces += 1

    print('quads:', len(all_quads), 'faces de cruzamento:', n_junction_faces, 'verts:', len(all_verts))
    return all_verts, all_quads, all_faces


def find_excluded_by_parking(chains, curb_results, parking_poly, margin=8.0, frac_thresh=0.7):
    """Ways de servico que ficam dentro do lote de estacionamento nao viram
    rua (ver PROCESSO.md secao 5) -- o estacionamento e' tratado a parte em
    build_river_and_parking.py."""
    buffered = parking_poly.buffer(margin)
    excluded = set()
    for i, chain in enumerate(chains):
        rec = merge_chain_stations(chain, curb_results)
        stations = rec['stations']
        if not stations:
            continue
        inside = sum(1 for st in stations if buffered.contains(Point(st['x'], st['y'])))
        if inside / len(stations) > frac_thresh:
            excluded.add(i)
    return excluded


def main():
    overpass = json.load(open(config.out_path('overpass_raw.json'), encoding='utf-8'))
    curb_results = {r['id']: r for r in json.load(open(config.out_path('curb_raw.json'), encoding='utf-8'))}

    ways_by_id = {}
    for el in overpass['elements']:
        if el['type'] != 'way' or 'highway' not in el.get('tags', {}):
            continue
        if el['id'] in config.BRIDGE_WAY_IDS:
            continue
        coords = [config.to_local(pt['lon'], pt['lat']) for pt in el.get('geometry', []) if pt]
        if len(coords) < 2:
            continue
        ways_by_id[el['id']] = {'id': el['id'], 'type': el['tags']['highway'],
                                 'name': el['tags'].get('name'), 'coords': coords}

    chains = build_chains(ways_by_id)
    print('chains:', len(chains), 'de', len(ways_by_id), 'ways')

    parking_el = next(el for el in overpass['elements'] if el.get('id') == config.PARKING_WAY_ID)
    parking_coords = [config.to_local(pt['lon'], pt['lat']) for pt in parking_el['geometry']]
    parking_poly = Polygon(parking_coords)
    excluded_idx = find_excluded_by_parking(chains, curb_results, parking_poly)
    print('chains excluidas (dentro do estacionamento):', len(excluded_idx))

    # medianas de largura por classe, so das ruas confiaveis -- usado como
    # fallback pras nao-confiaveis (nao e' um numero inventado)
    by_type = defaultdict(list)
    for i, chain in enumerate(chains):
        if i in excluded_idx:
            continue
        rec = merge_chain_stations(chain, curb_results)
        if not is_reliable(rec['stations']):
            continue
        for st in rec['stations']:
            if st['left'] is not None and st['right'] is not None:
                by_type[rec['type']].append(st['left'] + st['right'])
    class_default = {t: float(np.median(w)) for t, w in by_type.items()}
    overall_default = float(np.median([w for ws in by_type.values() for w in ws]))
    print('medianas por classe:', {k: round(v, 1) for k, v in class_default.items()})

    models = build_models(curb_results, chains, excluded_idx, class_default, overall_default)
    print('modelos de rua construidos:', len(models))

    junction_groups = find_junctions(models)
    print('cruzamentos reais encontrados:', sum(1 for v in junction_groups.values() if len(v) >= 2))

    verts, quads, faces = build_quad_mesh(models, junction_groups)

    json.dump({'verts': verts, 'quads': quads, 'faces': faces},
              open(config.out_path('ruas_quadmesh.json'), 'w'))
    print('salvo em', config.out_path('ruas_quadmesh.json'))


if __name__ == '__main__':
    main()
