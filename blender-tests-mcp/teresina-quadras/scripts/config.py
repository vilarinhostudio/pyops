"""
Config fixo deste site (Ponte Estaiada, Teresina). Pra rodar em outro lote,
gerar `CRS_X0`, `CRS_Y0` e o bbox local do terreno de novo com o snippet do
PROCESSO.md secao 1 (rodar dentro do Blender via execute_blender_code, ler
scene['crs x'], scene['crs y'] e o bound_box do mesh do terreno).
"""
import math
import os

# origem da projecao Web Mercator que o BlenderGIS gravou na cena
CRS_X0 = -4764626.801704407
CRS_Y0 = -565007.2403793335

# bbox do terreno em coordenadas locais do Blender (metros), objeto
# EXPORT_GOOGLE_SAT_WM com matrix_world identidade
MIN_X, MAX_X = -610.416, 612.577
MIN_Y, MAX_Y = -473.895, 443.350

# bbox em lat/lon (south, west, north, east) pra consultas Overpass / imagem
BBOX_LATLON = (-5.073162, -42.806854, -5.064954, -42.795868)

# ids das ways da OSM que sao a propria ponte estaiada -- nunca modelar,
# so usar pra saber o que esta ocluido na imagem por baixo dela
BRIDGE_WAY_IDS = {187871661, 187871662}

# way da OSM que ja e o poligono oficial do estacionamento (amenity=parking)
PARKING_WAY_ID = 683259197

R_EARTH = 6378137.0

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
os.makedirs(OUTPUT_DIR, exist_ok=True)


def to_local(lon, lat):
    """lon/lat -> coordenadas locais do Blender (metros)."""
    x = R_EARTH * math.radians(lon) - CRS_X0
    y = R_EARTH * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) - CRS_Y0
    return x, y


def to_lonlat(x_local, y_local):
    """Inverso de to_local."""
    x, y = x_local + CRS_X0, y_local + CRS_Y0
    lon = math.degrees(x / R_EARTH)
    lat = math.degrees(2 * math.atan(math.exp(y / R_EARTH)) - math.pi / 2)
    return lon, lat


def out_path(filename):
    return os.path.join(OUTPUT_DIR, filename)
