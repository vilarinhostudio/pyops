"""
NAO rodar este arquivo com `python`. Isso e' referencia -- cada bloco abaixo
e' pra colar no `execute_blender_code` (MCP do Blender), um de cada vez,
depois que os scripts .py deste diretorio ja rodaram e geraram os .json em
data/. Precisa de `bpy`/`mathutils`, que so existem dentro do Blender.

Pressupoe que existe um objeto de terreno chamado 'EXPORT_GOOGLE_SAT_WM' com
matrix_world identidade (confirmar antes -- se nao for identidade, os
raycasts abaixo precisam considerar a transformacao).

Ajustar DATA_DIR pro caminho real antes de colar.
"""

# ============================================================
# BLOCO 0 -- helper de drape (colar uma vez, reaproveitado pelos outros blocos)
# ============================================================
BLOCO_0_DRAPE_HELPER = r'''
import bpy, json, mathutils

DATA_DIR = r"C:\github-repositories\pyops\blender-tests-mcp\teresina-quadras\data"

MIN_X, MAX_X = -610.416, 612.577
MIN_Y, MAX_Y = -473.895, 443.350
CX_T, CY_T = (MIN_X + MAX_X) / 2, (MIN_Y + MAX_Y) / 2

terrain_obj = bpy.data.objects['EXPORT_GOOGLE_SAT_WM']
depsgraph = bpy.context.evaluated_depsgraph_get()
terrain_eval = terrain_obj.evaluated_get(depsgraph)
inv = terrain_obj.matrix_world.inverted()
inv3 = inv.to_3x3()

def raycast_z(x, y):
    origin_local = inv @ mathutils.Vector((x, y, 500.0))
    dir_local = inv3 @ mathutils.Vector((0, 0, -1))
    hit, loc, normal, idx = terrain_eval.ray_cast(origin_local, dir_local)
    if hit:
        return (terrain_obj.matrix_world @ loc).z
    return None

def drape(x, y, default_z=54.0):
    """Raycast contra o terreno, com nudge pro centro + repete quando o raio
    erra bem na borda do recorte (bug de ponto flutuante -- ver PROCESSO.md
    secao 3 item 7). NUNCA remover o loop de nudge: sem ele, pontos na
    borda caem silenciosamente no fallback (geometria "grudada no chao")."""
    z = raycast_z(x, y)
    if z is not None:
        return z
    for factor in (0.01, 0.03, 0.08, 0.2):
        xx = x + (CX_T - x) * factor
        yy = y + (CY_T - y) * factor
        z = raycast_z(xx, yy)
        if z is not None:
            return z
    return default_z

def get_or_make_collection(name):
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(coll)
    return coll

def replace_object(name, mesh):
    old = bpy.data.objects.get(name)
    if old:
        old_data = old.data
        bpy.data.objects.remove(old, do_unlink=True)
        if old_data and old_data.users == 0:
            bpy.data.meshes.remove(old_data)
    obj = bpy.data.objects.new(name, mesh)
    get_or_make_collection("GIS_Overpass_Teresina").objects.link(obj)
    return obj

def get_or_make_material(name, color=None):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        if color:
            mat.use_nodes = True
            bsdf = next(n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
            bsdf.inputs['Base Color'].default_value = color
    return mat

print("drape helper pronto")
'''

# ============================================================
# BLOCO 1 -- Ruas (malha em quads + faces de cruzamento ja prontas no json)
# ============================================================
BLOCO_1_RUAS = r'''
data = json.load(open(DATA_DIR + r"\ruas_quadmesh.json", encoding='utf-8'))
verts = [(x, y, drape(x, y) + 0.08) for x, y in data['verts']]
faces = data['quads'] + data['faces']

mesh = bpy.data.meshes.new("Ruas")
mesh.from_pydata(verts, [], faces)
mesh.update()
mesh.validate()

obj = replace_object("Ruas", mesh)
obj.data.materials.append(get_or_make_material("Mat_Ruas", (0.6, 0.6, 0.6, 1.0)))
print("Ruas:", len(verts), "verts,", len(faces), "faces")
'''

# ============================================================
# BLOCO 2 -- Rio (poligono plano, cota = percentil alto do interior --
# nunca seguir o relevo vertice a vertice aqui, ver PROCESSO.md secao 3.8)
# ============================================================
BLOCO_2_RIO = r'''
import numpy as np
from shapely.geometry import Polygon, Point
import mapbox_earcut as earcut

poly_data = json.load(open(DATA_DIR + r"\rio_polygon.json", encoding='utf-8'))
ring = poly_data['exterior'][:-1]
poly = Polygon(ring)

# grade de amostras internas pra achar uma cota que cubra o relevo ruidoso
minx, miny, maxx, maxy = poly.bounds
step = 8.0
sample_zs = []
x = minx
while x < maxx:
    y = miny
    while y < maxy:
        if poly.contains(Point(x, y)):
            z = raycast_z(x, y)
            if z is not None:
                sample_zs.append(z)
        y += step
    x += step
flat_z = sorted(sample_zs)[int(len(sample_zs) * 0.9)] + 0.15 if sample_zs else drape(*poly.centroid.coords[0])

# buracos no rio (poly_data['interiors']) sao raros mas suportados: cada
# ring de interior tambem entra na lista antes de triangular com earcut
all_rings = [ring] + [(r[:-1] if r[0] == r[-1] else r) for r in poly_data.get('interiors', [])]
coords_arr = np.array([pt for r in all_rings for pt in r], dtype=np.float64)
ring_end = np.cumsum([len(r) for r in all_rings]).astype(np.uint32)
tris = np.array(earcut.triangulate_float64(coords_arr, ring_end)).reshape(-1, 3)

verts = [(x, y, flat_z) for x, y in coords_arr]
faces = [[int(t[0]), int(t[1]), int(t[2])] for t in tris]

mesh = bpy.data.meshes.new("Rio")
mesh.from_pydata(verts, [], faces)
mesh.update()
obj = replace_object("Rio", mesh)
obj.data.materials.append(get_or_make_material("Mat_Rio", (0.3, 0.55, 0.85, 1.0)))
print("Rio: cota plana =", flat_z, "|", len(verts), "verts")
'''

# ============================================================
# BLOCO 3 -- Estacionamento (poligono oficial da OSM, tambem achatado)
# ============================================================
BLOCO_3_ESTACIONAMENTO = r'''
import numpy as np
from shapely.geometry import Polygon, Point
import mapbox_earcut as earcut

poly_data = json.load(open(DATA_DIR + r"\parking_polygon.json", encoding='utf-8'))
ring = poly_data['exterior'][:-1]
poly = Polygon(ring)

minx, miny, maxx, maxy = poly.bounds
step = 6.0
sample_zs = []
x = minx
while x < maxx:
    y = miny
    while y < maxy:
        if poly.contains(Point(x, y)):
            z = raycast_z(x, y)
            if z is not None:
                sample_zs.append(z)
        y += step
    x += step
flat_z = max(sample_zs) + 0.1 if sample_zs else drape(*poly.centroid.coords[0])

coords_arr = np.array(ring, dtype=np.float64)
tris = np.array(earcut.triangulate_float64(coords_arr, np.array([len(ring)], dtype=np.uint32))).reshape(-1, 3)
verts = [(x, y, flat_z) for x, y in ring]
faces = [[int(t[0]), int(t[1]), int(t[2])] for t in tris]

mesh = bpy.data.meshes.new("Estacionamento")
mesh.from_pydata(verts, [], faces)
mesh.update()
obj = replace_object("Estacionamento", mesh)
obj.data.materials.append(get_or_make_material("Mat_Estacionamento", (0.5, 0.45, 0.4, 1.0)))
print("Estacionamento: cota plana =", flat_z)
'''

# ============================================================
# BLOCO 4 -- Casas do entorno (blocos extrudados, altura unica)
# ============================================================
BLOCO_4_CASAS = r'''
data = json.load(open(DATA_DIR + r"\houses_mesh.json", encoding='utf-8'))
height = data['height']
n_buildings = data['n_buildings']

# cota base por predio = mediana do raycast nos vertices da tampa de baixo
building_pts = [[] for _ in range(n_buildings)]
for (x, y), bid, top in zip(data['verts_xy'], data['building_id'], data['is_top']):
    if not top:
        building_pts[bid].append((x, y))
base_z = [sorted(drape(x, y) for x, y in pts)[len(pts)//2] for pts in building_pts]

verts = []
for (x, y), bid, top in zip(data['verts_xy'], data['building_id'], data['is_top']):
    z = base_z[bid] + (height if top else 0.0)
    verts.append((x, y, z))

mesh = bpy.data.meshes.new("Casas")
mesh.from_pydata(verts, [], data['faces'])
mesh.update()
mesh.validate()
obj = replace_object("Casas", mesh)
obj.data.materials.append(get_or_make_material("Mat_Casas", (0.85, 0.75, 0.55, 1.0)))
print("Casas:", n_buildings, "predios,", len(verts), "verts")
'''

if __name__ == '__main__':
    print(__doc__)
    print('Este arquivo e so referencia -- copie os blocos BLOCO_* pro execute_blender_code.')
