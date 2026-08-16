"""Write every theme file from one template.

Each theme is a palette; everything else — typography, line weights, the colour
classes, the hatch rules — is identical and generated. Adding a theme is a new
entry in PALETTES; adding a colour is a new key inside one.

Run generate_hatches.py first: this reads hatches.css from it.

NAMING, and it is not cosmetic. Every selector matches by SUBSTRING and Bonsai
strips the separators out of the material name, so "danger" would also match
inside "danger1". Two rules keep that from biting:

  * palette colours always carry a level: danger1, danger2, danger3.
  * a colour with no levels — a fixed blue, a fixed grey — ends in 0: blue0.

Either way no name is a prefix of another, by construction rather than by
remembering. Keep to a single digit: danger1 is a substring of danger10.
"""

import re

LEVELS = ["1", "2", "3"]

# Palette colours. Each becomes name1 (full), name2 and name3 (stepping toward
# the page colour), so they read the same way on light and dark themes.
PALETTES = {
    "theme-default-light": {
        "principal": "#000000", "secondary": "#555555", "accent": "#0066cc",
        "bg": "#ffffff", "surface": "#f4f4f4", "warning": "#e53935",
        "success": "#43a047", "info": "#1e88e5", "danger": "#f9a825",
        "muted": "#9e9e9e",
    },
    "theme-default-dark": {
        "principal": "#ffffff", "secondary": "#aaaaaa", "accent": "#4da6ff",
        "bg": "#121212", "surface": "#1e1e1e", "warning": "#ff5252",
        "success": "#69f0ae", "info": "#40c4ff", "danger": "#ffd54f",
        "muted": "#757575",
    },
    "theme-flatly-light": {
        "principal": "#2c3e50", "secondary": "#95a5a6", "accent": "#18bc9c",
        "bg": "#ffffff", "surface": "#ecf0f1", "warning": "#e74c3c",
        "success": "#2ecc71", "info": "#3498db", "danger": "#f39c12",
        "muted": "#b4bcc2",
    },
    "theme-flatly-dark": {
        "principal": "#ecf0f1", "secondary": "#7f8c8d", "accent": "#1abc9c",
        "bg": "#1a252f", "surface": "#2c3e50", "warning": "#e74c3c",
        "success": "#2ecc71", "info": "#3498db", "danger": "#f1c40f",
        "muted": "#5d6d7e",
    },
    "theme-gruvbox-light": {
        "principal": "#3c3836", "secondary": "#7c6f64", "accent": "#076678",
        "bg": "#fbf1c7", "surface": "#ebdbb2", "warning": "#9d0006",
        "success": "#79740e", "info": "#427b58", "danger": "#b57614",
        "muted": "#928374",
    },
    "theme-gruvbox-dark": {
        "principal": "#ebdbb2", "secondary": "#a89984", "accent": "#83a598",
        "bg": "#282828", "surface": "#3c3836", "warning": "#fb4934",
        "success": "#b8bb26", "info": "#8ec07c", "danger": "#fabd2f",
        "muted": "#928374",
    },
}

# Colours with no levels, for anything that is a fixed value rather than a
# semantic slot. Written with a trailing 0 so they cannot be a prefix of
# anything. Same value in every theme — that is what makes them fixed.
FIXED_COLORS = {}


def mix(color, background, amount):
    """Step a colour toward the page colour.

    Mixing with the BACKGROUND rather than with white or black is what lets one
    rule serve both kinds of theme: on a light theme the page is white so the
    levels lighten, on a dark one it is near-black so they darken. The name is
    the same either way, which is the point of writing danger2."""
    a = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(background[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(a, b))


def palette_block(palette):
    lines = [":root {"]
    background = palette["bg"]
    for color, value in palette.items():
        for index, level in enumerate(LEVELS):
            lines.append(
                f"    --color-{color}{level}: "
                f"{mix(value, background, index * 0.33)};"
            )
    for name, value in FIXED_COLORS.items():
        lines.append(f"    --color-{name}: {value};")
    lines.append(
        "    --color-veil: "
        f"{palette['principal']};"
        "   /* véu de profundidade: o único cinza com significado */"
    )
    lines.append("}")
    return "\n".join(lines)


# Corte e projeção saem do MESMO material IFC, então a classe é idêntica nos
# dois — não dá para separá-los pelo nome. O que os separa é a classe que o
# Bonsai já põe: .cut e .projection. Prefixar com c e p amarra a regra a uma
# delas.
#
# Isso importa porque projeção não é uma coisa só: uma vizinhança e uma parte do
# próprio edifício estão ambas em projeção e precisam de pesos gráficos
# diferentes. Sem o prefixo, um só valor teria de servir aos dois.
SCOPES = [
    ("", ""),                  # ambos
    ("c", ".cut"),             # só o corte
    ("p", ".projection"),      # só a projeção
]


def color_classes(palette):
    names = [f"{c}{level}" for level in LEVELS for c in palette]
    names += list(FIXED_COLORS)
    lines = [
        "/* Classes Semânticas de Cor",
        "",
        "   Níveis em ordem crescente: nenhum nome é prefixo de outro, então a",
        "   correspondência por substring não pode confundir dois deles.",
        "",
        "   Prefixos de escopo:",
        "     fill / stroke / pen   valem em corte e projeção",
        "     cfill / cstroke / cpen   só no corte",
        "     pfill / pstroke / ppen   só na projeção",
        "",
        "   Os prefixados vêm depois e carregam a classe .cut ou .projection, o",
        "   que lhes dá especificidade maior — então refinam sem que se precise",
        "   remover a regra geral. */",
    ]
    for prop in ("stroke", "fill"):
        for prefix, scope in SCOPES:
            for name in names:
                selector = f'{scope}[class*="{prefix}{prop}{name}"]'
                if not prefix:
                    # "pfillwarning3" CONTAINS "fillwarning3", so without this
                    # the unscoped rule fires on a scoped class too and a
                    # projection-only colour leaks into the cut.
                    selector += (
                        f':not([class*="p{prop}{name}"])'
                        f':not([class*="c{prop}{name}"])'
                    )
                lines.append(
                    f'{selector} {{ {prop}: var(--color-{name}) !important; }}'
                )
        lines.append("")
    return "\n".join(lines)


PENS = [
    ("005", "0.05", "ultrathin"), ("013", "0.13", "extrathin"),
    ("018", "0.18", "thin"), ("025", "0.25", "mediumthin"),
    ("035", "0.35", "medium"), ("050", "0.50", "thick"),
    ("070", "0.70", "extrathick"), ("100", "1.00", "ultrathick"),
]


def child_classes(palette):
    """Scoped colour and weight reaching the paths INSIDE a projection group.

    A projection group draws through children — path.outline, path.crease — and
    those carry their own rules. A rule on the group loses: path.outline is a
    more specific selector than inheritance, and there is no fill to inherit
    anyway, since projection geometry is lines rather than closed shapes.

    So the scoped rules have to name the children. Leaving a class off keeps the
    defaults exactly as they were, which is what makes this safe to layer: only
    what is asked for is overridden."""
    names = [f"{c}{level}" for level in LEVELS for c in palette]
    names += list(FIXED_COLORS)
    lines = [
        "/* Projeção: alcance dos filhos",
        "",
        "   O grupo em projeção desenha através de path.outline e path.crease, e",
        "   esses têm regra própria — uma regra no grupo perde. Por isso o",
        "   escopo precisa nomear os filhos.",
        "",
        "   Sem classe nenhuma, outline e crease seguem como sempre: 0.35 e 0.25",
        "   de espessura, na cor principal. */",
    ]
    for name in names:
        lines.append(
            f'.projection[class*="pstroke{name}"] path '
            f'{{ stroke: var(--color-{name}) !important; }}'
        )
        lines.append(
            f'.cut[class*="cstroke{name}"] path '
            f'{{ stroke: var(--color-{name}) !important; }}'
        )
    lines.append("")
    for code, width, alias in PENS:
        # One weight for every child: outline and crease stop differing once a
        # pen is asked for. That is the trade — the class is an override, and an
        # override that kept two weights would not be one.
        lines.append(
            f'.projection[class*="ppen{code}"] path, '
            f'.projection[class*="pline{alias}"] path '
            f'{{ stroke-width: {width}px !important; }}'
        )
        lines.append(
            f'.cut[class*="cpen{code}"] path, '
            f'.cut[class*="cline{alias}"] path '
            f'{{ stroke-width: {width}px !important; }}'
        )
    return "\n".join(lines)


def pen_classes():
    lines = [
        "/* Espessuras de Linha (Pen)",
        "",
        "   cpen só no corte, ppen só na projeção — é como um contorno de",
        "   vizinhança fica mais leve que o do próprio edifício sem que os dois",
        "   precisem de materiais diferentes. */",
    ]
    for prefix, scope in SCOPES:
        for code, width, alias in PENS:
            # Same reason as the colours: "cpen050" contains "pen050", so the
            # unscoped rule has to exclude the scoped spellings or a weight
            # asked for the cut alone would also reach the projection.
            guard = (
                ""
                if prefix
                else f':not([class*="ppen{code}"]):not([class*="cpen{code}"])'
            )
            alias_guard = (
                ""
                if prefix
                else f':not([class*="pline{alias}"]):not([class*="cline{alias}"])'
            )
            selector = (
                f'{scope}[class*="{prefix}pen{code}"]{guard}, '
                f'{scope}[class*="{prefix}line{alias}"]{alias_guard}'
            )
            lines.append(f'{selector} {{ stroke-width: {width}px !important; }}')
    return "\n".join(lines)


BASE = """
/* ==========================================================================
   CONFIGURAÇÕES GERAIS, TIPOGRAFIA E ESTRUTURA BASE
   ========================================================================== */

* { stroke-linecap: round; stroke-linejoin: round; }

text, tspan {
    fill: var(--color-principal1, #000000);
    stroke: none;
    font-family: 'OpenGost Type B TT', 'DejaVu Sans Condensed', 'Liberation Sans', 'Arial Narrow', 'Arial';
    font-size: 4.13px;
}

a text, a tspan { fill: var(--color-accent1, blue) !important; text-decoration: underline; }
a:hover { cursor: pointer; }

.cut { fill: var(--color-principal1, black); stroke: var(--color-principal1, black); stroke-width: 0.35; fill-rule: evenodd; }
.projection { fill: var(--color-bg1, white); stroke: var(--color-principal1, black); stroke-width: 0.25; }

path.outline  { stroke: var(--color-principal1, black); stroke-width: 0.35; stroke-opacity: 1; }
path.boundary { stroke: var(--color-principal1, black); stroke-width: 0.3; stroke-opacity: 0.9; }
path.crease   { stroke: var(--color-principal1, black); stroke-width: 0.25; stroke-opacity: 0.85; }
path.sharp    { stroke: var(--color-principal1, black); stroke-width: 0.18; stroke-opacity: 0.7; }
path.flush    { stroke: var(--color-principal1, black); stroke-width: 0.1; stroke-opacity: 0.4; }

.surface { fill: var(--color-bg1, white); stroke-width: 0.1; }
.annotation, .IfcAnnotation { fill: none; stroke: var(--color-principal1, black); stroke-width: 0.3; }

/* Marcadores e Linhas Especiais */
.PredefinedType-LINEWORK { stroke: var(--color-principal1, black); stroke-width: 0.25; }
.PredefinedType-GRID { marker-start: url(#grid-marker); marker-end: url(#grid-marker); }
.PredefinedType-DIMENSION { marker-start: url(#dimension-marker-start); marker-end: url(#dimension-marker-end); }
.PredefinedType-TEXT { fill: var(--color-principal1, black); stroke: none; }

/* Opacidade

   O :not() existe porque "fillopacity25" contém "opacity25": sem ele, pedir
   opacidade de preenchimento também baixava a opacidade do traço. */
[class*="fillopacity10"] { fill-opacity: 0.10 !important; }
[class*="fillopacity25"] { fill-opacity: 0.25 !important; }
[class*="fillopacity50"] { fill-opacity: 0.50 !important; }
[class*="fillopacity75"] { fill-opacity: 0.75 !important; }

[class*="opacity10"]:not([class*="fillopacity10"]) { opacity: 0.10 !important; }
[class*="opacity25"]:not([class*="fillopacity25"]) { opacity: 0.25 !important; }
[class*="opacity50"]:not([class*="fillopacity50"]) { opacity: 0.50 !important; }
[class*="opacity75"]:not([class*="fillopacity75"]) { opacity: 0.75 !important; }
"""


def main():
    hatches = open("hatches.css", encoding="utf-8").read().rstrip()
    for name, palette in PALETTES.items():
        text = "\n\n".join([
            palette_block(palette),
            f"/* --- TEMA ATIVO: {name.replace('theme-', '').upper()} --- */",
            BASE.strip(),
            color_classes(palette),
            pen_classes(),
            child_classes(palette),
            hatches,
        ]) + "\n"
        open(f"{name}.css", "w", encoding="utf-8").write(text)
        print(f"{name}.css: {len(text) / 1024:.0f} KB")


if __name__ == "__main__":
    main()
