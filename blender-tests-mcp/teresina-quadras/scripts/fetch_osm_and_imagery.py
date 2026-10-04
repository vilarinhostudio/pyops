"""
Baixa tudo que vem de fonte externa: ruas/rio/estacionamento da OSM
(Overpass) e a imagem de satelite de alta resolucao (Esri World Imagery,
publico, sem API key). Ver PROCESSO.md secao 2 (passos 1 e 3).

Rodar direto: `python fetch_osm_and_imagery.py`
"""
import json
import requests
import config

USER_AGENT = 'pyops-blender-tests/1.0'  # overpass-api.de responde 406 sem isso


def fetch_overpass():
    south, west, north, east = config.BBOX_LATLON
    query = f'''
[out:json][timeout:60];
(
  way["highway"]({south},{west},{north},{east});
  way["waterway"]({south},{west},{north},{east});
  way["natural"="water"]({south},{west},{north},{east});
  relation["natural"="water"]({south},{west},{north},{east});
  way["amenity"="parking"]({south},{west},{north},{east});
  way["landuse"="parking"]({south},{west},{north},{east});
);
out geom;
'''
    r = requests.post(
        'https://overpass-api.de/api/interpreter',
        data={'data': query},
        headers={'User-Agent': USER_AGENT},
        timeout=120,
    )
    r.raise_for_status()
    data = r.json()
    print('overpass: elementos recebidos', len(data['elements']))
    json.dump(data, open(config.out_path('overpass_raw.json'), 'w'))
    return data


def fetch_esri_imagery(px_size=4000):
    """Esri World Imagery via export REST publico. Sem API key. A resolucao
    real varia por regiao (o provedor de imagem que o Esri usa la embaixo
    muda por lugar) -- testar antes de assumir que fica melhor que o
    Google/Bing que o BlenderGIS ja baixou."""
    minX, maxX = config.MIN_X, config.MAX_X
    minY, maxY = config.MIN_Y, config.MAX_Y
    x0, y0 = minX + config.CRS_X0, minY + config.CRS_Y0
    x1, y1 = maxX + config.CRS_X0, maxY + config.CRS_Y0
    aspect = (maxX - minX) / (maxY - minY)
    h = px_size
    w = int(h * aspect)
    url = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export'
    params = {
        'bbox': f'{x0},{y0},{x1},{y1}',
        'bboxSR': 102100,
        'imageSR': 102100,
        'size': f'{w},{h}',
        'format': 'png32',
        'f': 'image',
    }
    r = requests.get(url, params=params, timeout=120)
    r.raise_for_status()
    out = config.out_path('esri_imagery.png')
    open(out, 'wb').write(r.content)
    print('imagem Esri salva:', out, len(r.content) / 1e6, 'MB', f'{w}x{h}px')
    return out


if __name__ == '__main__':
    fetch_overpass()
    fetch_esri_imagery()
