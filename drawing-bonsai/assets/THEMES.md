## Gerar tudo

```bash
python build_themes.py
```

Regenera os 12 arquivos de saída (`patterns-<tema>.svg` + `theme-<tema>.css`) a partir de `patterns-source.svg` e das classes `Theme` definidas no script.

## Editar cor de um tema

Em `build_themes.py`, dentro de `THEMES`:

```python
Theme("gruvbox-light", {
    "principal": "#3c3836",
    "accent":    "#076678",
    ...
})
```

Cada entrada vira `accent1`, `accent2`, `accent3` automaticamente. Depois:

```bash
python build_themes.py
```

## Adicionar cor nova a um tema

```python
Theme("gruvbox-light", {
    ...,
    "highlight": "#d79921",   # nova entrada
})
```

Vira `highlight1`, `highlight2`, `highlight3` sozinho. Rode o script de novo.

## Adicionar cor fixa, sem nível

```python
Theme("gruvbox-light", {
    ...
}, fixed={"blue0": "#0000ff"})
```

## Adicionar tema novo

```python
THEMES.append(
    Theme("meutema", {
        "principal": "#111111", "secondary": "#555555", "accent": "#0066cc",
        "bg": "#ffffff", "surface": "#f0f0f0", "warning": "#e53935",
        "success": "#43a047", "info": "#1e88e5", "danger": "#f9a825",
        "muted": "#9e9e9e",
    })
)
```

Todas as 10 chaves (`principal` até `muted`) são obrigatórias. Rode o script.

## Adicionar hachura nova

Só em `patterns-source.svg`, no formato dos outros 32 — um `<rect>` de fundo seguido do desenho em `stroke`:

```xml
<pattern id="minhahachura" width="1" height="1" patternUnits="userSpaceOnUse">
  <rect width="1" height="1" fill="var(--color-bg)"/>
  <line x1="0" y1="0" x2="1" y2="1" style="stroke:var(--color-principal);stroke-width:0.2"/>
</pattern>
```

```bash
python build_themes.py
```

As variantes de cor saem automáticas para os 6 temas. Precisa ser `stroke`, não `fill` colorido.

## Escolher quais cores viram hachura

Em `build_themes.py`:

```python
HATCH_FOREGROUNDS = ("principal1", "warning1", "danger1", "info1", "accent1")
```

Adicionar uma cor aqui multiplica `32 hachuras × 2 profundidades` no tamanho do SVG.

## Usar no Bonsai

Trocar de tema exige trocar os dois arquivos juntos:

```
patterns-gruvbox-light.svg  +  theme-gruvbox-light.css
```

Nome do material:

```
cconcrete-cfgwarning1-cpen050    hachura de concreto, warning1, só no corte, 0.5mm
pbrick-pfgmuted2                 hachura de tijolo, muted2, só na projeção
```
