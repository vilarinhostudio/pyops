"""
Rio (superficie plana, recortada no terreno) e estacionamento (poligono
oficial da OSM, tambem achatado). Os dois precisam de cota Z UNICA em vez
de seguir o relevo vertice-a-vertice -- o DEM do BlenderGIS aqui e muito
grosseiro (SRTM, ~30m/celula) e uma area plana de verdade (rio, lote de
estacionamento) sai com facetas visiveis se seguir o relevo ponto a ponto.
Ver PROCESSO.md secao 3 item 8.

A cota Z de cada um so pode ser calculada de dentro do Blender (precisa de
raycast contra o terreno) -- aqui so preparamos a GEOMETRIA (poligono 2D
recortado); o calculo de altura e a montagem da malha ficam em
blender_snippets.py.

Rodar depois de fetch_osm_and_imagery.py.
"""
import json
from shapely.geometry import Polygon, box
from shapely.ops import unary_union, polygonize
from shapely.geometry.polygon import orient
import config

TERRAIN_BOX = box(config.MIN_X, config.MIN_Y, config.MAX_X, config.MAX_Y)


def build_river():
    overpass = json.load(open(config.out_path('overpass_raw.json'), encoding='utf-8'))
    relation = next((el for el in overpass['elements']
                      if el['type'] == 'relation' and el.get('tags', {}).get('natural') == 'water'), None)
    if relation is None:
        print('nenhuma relation natural=water encontrada na bbox')
        return None

    from shapely.geometry import LineString
    outer_segments = [
        LineString([config.to_local(pt['lon'], pt['lat']) for pt in m['geometry']])
        for m in relation['members']
        if m['type'] == 'way' and 'geometry' in m and m.get('role') != 'inner'
    ]
    rings = list(polygonize(unary_union(outer_segments)))
    if not rings:
        print('nao consegui fechar o poligono do rio a partir dos segmentos')
        return None
    water_poly = max(rings, key=lambda p: p.area)
    clipped = water_poly.intersection(TERRAIN_BOX)
    clipped = orient(clipped, sign=1.0)

    out = {'exterior': list(clipped.exterior.coords), 'interiors': [list(r.coords) for r in clipped.interiors]}
    json.dump(out, open(config.out_path('rio_polygon.json'), 'w'))
    print('rio salvo em', config.out_path('rio_polygon.json'))
    return out


def build_parking():
    overpass = json.load(open(config.out_path('overpass_raw.json'), encoding='utf-8'))
    el = next(e for e in overpass['elements'] if e.get('id') == config.PARKING_WAY_ID)
    coords = [config.to_local(pt['lon'], pt['lat']) for pt in el['geometry']]
    poly = Polygon(coords)
    if not poly.is_valid:
        poly = poly.buffer(0)
    clipped = poly.intersection(TERRAIN_BOX)
    out = {'exterior': list(clipped.exterior.coords)}
    json.dump(out, open(config.out_path('parking_polygon.json'), 'w'))
    print('estacionamento salvo em', config.out_path('parking_polygon.json'))
    return out


if __name__ == '__main__':
    build_river()
    build_parking()
