"""Generate one CSS and one patterns SVG per theme, with no CSS variables.

Why no variables: Inkscape and Affinity do not resolve custom properties
declared inside an SVG <style>, so a drawing that depends on var() opens
without colours or hatches outside a browser. Every colour here is a literal
hex.

That has one consequence worth stating: a <pattern> cannot be recoloured from
CSS — CSS can only point at a pattern that already exists. So the hatch colours
live inside the pattern SVG, and the pattern SVG therefore becomes one file PER
THEME instead of one file shared by all.

    patterns-source.svg     geometry only, no colour — the single source
    patterns-<theme>.svg    generated, colours baked in
    theme-<theme>.css       generated, hex literals

Editing: change a Theme entry below (colours, font, pens) and re-run. Nothing
in the generated files should be edited by hand.

Run: python build_themes.py
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

SOURCE = "patterns-source.svg"

# How far each level steps toward the page colour. Mixing with the background
# rather than with white or black is what lets one rule serve light and dark
# themes: the tones lighten on one and darken on the other, same names.
LEVELS = (0.0, 0.33, 0.66)

# Only these carry hatches. Every colour still works for fill and stroke — a
# hatch in all thirty would multiply the pattern file for tones never used as
# line work.
HATCH_FOREGROUNDS = ("principal1", "warning1", "danger1", "info1", "accent1")

# suffix -> veil opacity over the page colour. "" means no veil.
DEPTHS = {"": None, "25": 0.25}

PENS = (
    ("005", 0.05, "ultrathin"), ("013", 0.13, "extrathin"),
    ("018", 0.18, "thin"), ("025", 0.25, "mediumthin"),
    ("035", 0.35, "medium"), ("050", 0.50, "thick"),
    ("070", 0.70, "extrathick"), ("100", 1.00, "ultrathick"),
)

SCOPES = (("", ""), ("p", ".projection"), ("c", ".cut"))


@dataclass
class Theme:
    """Data only. Nothing here knows how to export itself."""

    name: str
    colors: dict            # base name -> hex of level 1
    font_size: float = 4.13
    font_family: str = (
        "'OpenGost Type B TT', 'DejaVu Sans Condensed', "
        "'Liberation Sans', 'Arial Narrow', 'Arial'"
    )
    fixed: dict = field(default_factory=dict)   # blue0 and friends, no levels

    def resolve(self) -> dict:
        """Every usable colour name -> hex, with levels expanded."""
        background = self.colors["bg"]
        out = {}
        for base, value in self.colors.items():
            for index, amount in enumerate(LEVELS, start=1):
                out[f"{base}{index}"] = mix(value, background, amount)
        out.update(self.fixed)
        return out


def mix(color, background, amount):
    a = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(background[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(a, b))


def read_geometry(path=SOURCE):
    """(id, tile attributes, geometry) per hatch, stroke colour removed.

    The colour comes off so one geometry serves every variant: each pattern
    wraps a <use> in a group that sets the stroke, and <use> — unlike
    <pattern> — really does inherit from its host.
    """
    text = open(path, encoding="utf-8").read()
    found = []
    for pattern_id, attrs, body in re.findall(
        r'<pattern id="([^"]+)"([^>]*)>(.*?)</pattern>', text, re.S
    ):
        geometry = re.sub(r"\s*<rect[^>]*/>", "", body, count=1)
        geometry = re.sub(r"stroke\s*:\s*var\(--color-[a-z0-9]+\)\s*;?\s*", "", geometry)
        geometry = re.sub(r'\sstroke="var\(--color-[a-z0-9]+\)"', "", geometry)
        found.append((pattern_id, dict(re.findall(r'(\w+)="([^"]*)"', attrs)),
                      geometry.strip()))
    return found


def export_theme_patterns(theme, hatches, path):
    colors = theme.resolve()
    page, veil = colors["bg1"], colors["principal1"]

    svg = [
        '<?xml version="1.0" encoding="utf-8" ?>',
        '<svg baseProfile="full" version="1.1" '
        'xmlns="http://www.w3.org/2000/svg" '
        'xmlns:xlink="http://www.w3.org/1999/xlink">',
        f"<!-- {theme.name} — gerado por build_themes.py, nao editar a mao -->",
        "<defs>",
    ]
    svg += [f'<g id="geom-{pid}">{geom}</g>' for pid, _a, geom in hatches]
    svg.append("</defs>")

    count = 0
    for pattern_id, attrs, _geometry in hatches:
        width, height = attrs.get("width", "1"), attrs.get("height", "1")
        transform = attrs.get("patternTransform", "")
        head = (
            f'width="{width}" height="{height}"'
            + (f' patternTransform="{transform}"' if transform else "")
            + ' patternUnits="userSpaceOnUse"'
        )
        for foreground in HATCH_FOREGROUNDS:
            for depth, opacity in DEPTHS.items():
                # The tile background is always the page colour: cut hatching
                # has to occlude what lies behind it.
                tile = f'<rect width="{width}" height="{height}" fill="{page}"/>'
                if opacity is not None:
                    tile += (
                        f'<rect width="{width}" height="{height}" '
                        f'fill="{veil}" fill-opacity="{opacity}"/>'
                    )
                suffix = f"-bg{depth}" if depth else ""
                svg.append(
                    f'<pattern id="{pattern_id}-fg{foreground}{suffix}" {head}>'
                    f'{tile}<g stroke="{colors[foreground]}">'
                    f'<use xlink:href="#geom-{pattern_id}" '
                    f'href="#geom-{pattern_id}"/></g></pattern>'
                )
                count += 1
    svg.append("</svg>")
    open(path, "w", encoding="utf-8").write("\n".join(svg))
    return count


def guard(prefix, token):
    """Keep an unscoped rule from matching a scoped class.

    "pfillwarning1" CONTAINS "fillwarning1", and every selector matches by
    substring — without this a projection-only colour also paints the cut.
    """
    if prefix:
        return ""
    return f':not([class*="p{token}"]):not([class*="c{token}"])'


def export_theme_css(theme, hatches, path):
    colors = theme.resolve()
    names = list(colors)
    principal, page = colors["principal1"], colors["bg1"]

    css = [
        f"/* {theme.name} — gerado por build_themes.py, nao editar a mao.",
        "   Sem var(): abre correto em Inkscape e Affinity. */",
        "",
        "* { stroke-linecap: round; stroke-linejoin: round; }",
        "",
        f"text, tspan {{ fill: {principal}; stroke: none; "
        f"font-family: {theme.font_family}; font-size: {theme.font_size}px; }}",
        f"a text, a tspan {{ fill: {colors['accent1']} !important; "
        "text-decoration: underline; }",
        "",
        f".cut {{ fill: {principal}; stroke: {principal}; "
        "stroke-width: 0.35; fill-rule: evenodd; }",
        f".projection {{ fill: {page}; stroke: {principal}; stroke-width: 0.25; }}",
        f".surface {{ fill: {page}; stroke-width: 0.1; }}",
        "",
        f"path.outline  {{ stroke: {principal}; stroke-width: 0.35; }}",
        f"path.boundary {{ stroke: {principal}; stroke-width: 0.30; stroke-opacity: 0.9; }}",
        f"path.crease   {{ stroke: {principal}; stroke-width: 0.25; stroke-opacity: 0.85; }}",
        f"path.sharp    {{ stroke: {principal}; stroke-width: 0.18; stroke-opacity: 0.7; }}",
        f"path.flush    {{ stroke: {principal}; stroke-width: 0.10; stroke-opacity: 0.4; }}",
        "",
        f".annotation, .IfcAnnotation {{ fill: none; stroke: {principal}; "
        "stroke-width: 0.3; }",
        f".PredefinedType-LINEWORK {{ stroke: {principal}; stroke-width: 0.25; }}",
        f".PredefinedType-TEXT {{ fill: {principal}; stroke: none; }}",
        ".PredefinedType-GRID { marker-start: url(#grid-marker); "
        "marker-end: url(#grid-marker); }",
        ".PredefinedType-DIMENSION { marker-start: url(#dimension-marker-start); "
        "marker-end: url(#dimension-marker-end); }",
        "",
        "/* Opacidade. O :not() existe porque 'fillopacity25' contem 'opacity25'. */",
    ]
    for value in (10, 25, 50, 75):
        css.append(
            f'[class*="fillopacity{value}"] {{ fill-opacity: {value / 100:.2f} !important; }}'
        )
    for value in (10, 25, 50, 75):
        css.append(
            f'[class*="opacity{value}"]:not([class*="fillopacity{value}"]) '
            f"{{ opacity: {value / 100:.2f} !important; }}"
        )

    css += ["", "/* Espessuras de linha. p = so projecao, c = so corte. */"]
    for prefix, scope in SCOPES:
        for code, width, alias in PENS:
            css.append(
                f'{scope}[class*="{prefix}pen{code}"]{guard(prefix, f"pen{code}")}, '
                f'{scope}[class*="{prefix}line{alias}"]{guard(prefix, f"line{alias}")} '
                f"{{ stroke-width: {width}px !important; }}"
            )

    css += ["", "/* Cores. Niveis crescentes: nenhum nome e prefixo de outro. */"]
    for prop in ("stroke", "fill"):
        for prefix, scope in SCOPES:
            for name in names:
                token = f"{prop}{name}"
                css.append(
                    f'{scope}[class*="{prefix}{token}"]{guard(prefix, token)} '
                    f"{{ {prop}: {colors[name]} !important; }}"
                )

    css += [
        "",
        "/* Projecao desenha pelos filhos (path.outline, path.crease), que tem",
        "   regra propria — uma regra no grupo perde. */",
    ]
    for prefix, scope in [(p, s) for p, s in SCOPES if p]:
        for name in names:
            css.append(
                f'{scope}[class*="{prefix}stroke{name}"] path '
                f"{{ stroke: {colors[name]} !important; }}"
            )
        for code, width, alias in PENS:
            css.append(
                f'{scope}[class*="{prefix}pen{code}"] path, '
                f'{scope}[class*="{prefix}line{alias}"] path '
                f"{{ stroke-width: {width}px !important; }}"
            )

    css += ["", "/* Hachuras. As cores vivem no patterns SVG deste tema. */"]
    for pattern_id, _attrs, _geometry in hatches:
        for foreground in HATCH_FOREGROUNDS:
            for depth in DEPTHS:
                suffix = f"-bg{depth}" if depth else ""
                bg = f'[class*="bg{depth}"]' if depth else ""
                for prefix, scope in SCOPES:
                    css.append(
                        f'{scope}[class*="material-{prefix}{pattern_id}"]'
                        f'[class*="{prefix}fg{foreground}"]{bg} '
                        f"{{ fill: url(#{pattern_id}-fg{foreground}{suffix}); }}"
                    )

    open(path, "w", encoding="utf-8").write("\n".join(css) + "\n")


THEMES = [
    Theme("default-light", {
        "principal": "#000000", "secondary": "#555555", "accent": "#0066cc",
        "bg": "#ffffff", "surface": "#f4f4f4", "warning": "#e53935",
        "success": "#43a047", "info": "#1e88e5", "danger": "#f9a825",
        "muted": "#9e9e9e",
    }),
    Theme("default-dark", {
        "principal": "#ffffff", "secondary": "#aaaaaa", "accent": "#4da6ff",
        "bg": "#121212", "surface": "#1e1e1e", "warning": "#ff5252",
        "success": "#69f0ae", "info": "#40c4ff", "danger": "#ffd54f",
        "muted": "#757575",
    }),
    Theme("flatly-light", {
        "principal": "#2c3e50", "secondary": "#95a5a6", "accent": "#18bc9c",
        "bg": "#ffffff", "surface": "#ecf0f1", "warning": "#e74c3c",
        "success": "#2ecc71", "info": "#3498db", "danger": "#f39c12",
        "muted": "#b4bcc2",
    }),
    Theme("flatly-dark", {
        "principal": "#ecf0f1", "secondary": "#7f8c8d", "accent": "#1abc9c",
        "bg": "#1a252f", "surface": "#2c3e50", "warning": "#e74c3c",
        "success": "#2ecc71", "info": "#3498db", "danger": "#f1c40f",
        "muted": "#5d6d7e",
    }),
    Theme("gruvbox-light", {
        "principal": "#3c3836", "secondary": "#7c6f64", "accent": "#076678",
        "bg": "#fbf1c7", "surface": "#ebdbb2", "warning": "#9d0006",
        "success": "#79740e", "info": "#427b58", "danger": "#b57614",
        "muted": "#928374",
    }),
    Theme("gruvbox-dark", {
        "principal": "#ebdbb2", "secondary": "#a89984", "accent": "#83a598",
        "bg": "#282828", "surface": "#3c3836", "warning": "#fb4934",
        "success": "#b8bb26", "info": "#8ec07c", "danger": "#fabd2f",
        "muted": "#928374",
    }),
]


def main():
    hatches = read_geometry()
    for theme in THEMES:
        made = export_theme_patterns(
            theme, hatches, f"patterns-{theme.name}.svg"
        )
        export_theme_css(theme, hatches, f"theme-{theme.name}.css")
        print(f"{theme.name}: {made} padroes")


if __name__ == "__main__":
    main()
