"""Generate the hatch patterns and the CSS that selects them.

Two axes, and they are deliberately different in nature:

  fg  — the line colour, one entry per palette colour.
  bg  — a DEPTH veil, not a colour: none, 33% or 66% of --color-veil.

Making the veil a variable instead of a palette entry is what keeps this small.
The veil is white on dark themes and black on light ones because that is what
--color-veil is set to, so depth reads the same way in every theme without a
single extra pattern.

Why generate at all: an SVG <pattern> renders in its own context, so a CSS
variable set on the element that references it never reaches the pattern's
content. Per-element colour cannot come from the cascade — a pattern has to
exist for each combination. Masking would avoid that, but a mask applies to the
whole element and would chop the outline stroke into dashes, which is worse.

The geometry of each hatch is written ONCE and reused through <use>, which does
inherit from its host — that is why the file is a fraction of what it would be
otherwise.

TO TRIM: cut PATTERNS to the hatches you actually use. That is the real lever —
each hatch removed takes len(FOREGROUNDS) * len(DEPTHS) patterns with it.
Adding a palette colour costs len(PATTERNS) * len(DEPTHS) patterns.
"""

import re

# Base palette. Every entry gets three levels, written danger1, danger2,
# danger3, stepping toward the page colour — lighter on light themes, darker on
# dark ones.
#
# The number is NOT decoration. Every selector here matches by substring and
# Bonsai strips the separators, so a bare "danger" would also match inside
# "danger1". Ending every name in a digit makes no name a prefix of another, by
# construction rather than by vigilance. Keep it to one digit: danger1 is a
# substring of danger10.
BASE_COLORS = [
    "principal", "secondary", "accent", "bg", "surface",
    "warning", "success", "info", "danger", "muted",
]
LEVELS = ["1", "2", "3"]
FOREGROUNDS = [f"{color}{level}" for color in BASE_COLORS for level in LEVELS]

# The tile background is ALWAYS the page colour. A cut wall has to occlude what
# lies behind it — floor, furniture, projected lines — which is why CAD hatching
# is opaque and not transparent. Same colour as the page, so it is invisible and
# still does its job.
#
# name -> veil opacity over that background. The veil is the one grey that means
# something, so it stays deliberate and short: every tone spent on decoration is
# a tone no longer available to carry information.
DEPTHS = {"": None, "25": 0.25}

# None means every hatch found in the source.
PATTERNS = None

# Source and output are DIFFERENT files on purpose. Reading and writing the same
# path meant a second run re-ingested its own output and squared the pattern
# count — 32 hatches became 1952 and the file went from 590 KB to 38 MB.
# Edit or add hatches in SOURCE.
SOURCE = "patterns-source.svg"
SVG_OUT = "patterns.svg"
CSS_OUT = "hatches.css"


def parse(text):
    found = []
    for match in re.finditer(
        r'<pattern id="([^"]+)"([^>]*)>(.*?)</pattern>', text, re.S
    ):
        pattern_id, attributes, body = match.groups()
        # The first rect is the tile background; the rest is the drawing.
        geometry = re.sub(r'\s*<rect[^>]*/>', '', body, count=1)
        # Strip the stroke COLOUR so the geometry inherits it. stroke-width and
        # fill:none stay — those are structural, not decisions about colour.
        geometry = re.sub(r'stroke\s*:\s*var\(--color-[a-z]+\)\s*;?\s*', '', geometry)
        geometry = re.sub(r'\sstroke="var\(--color-[a-z]+\)"', '', geometry)
        found.append((pattern_id, dict(re.findall(r'(\w+)="([^"]*)"', attributes)),
                      geometry.strip()))
    return found


def default_foreground(pattern_id):
    # Demolition hatching is red by convention; worth keeping as its default
    # even though every colour is now reachable.
    return "warning1" if pattern_id == "demolish" else "principal1"


def build(patterns):
    svg = [
        '<?xml version="1.0" encoding="utf-8" ?>',
        '<svg baseProfile="full" version="1.1" '
        'xmlns="http://www.w3.org/2000/svg" '
        'xmlns:ev="http://www.w3.org/2001/xml-events" '
        'xmlns:xlink="http://www.w3.org/1999/xlink">',
        "",
        "<!-- Geometria de cada hachura, escrita uma vez. Sem cor: ela é",
        "     herdada do grupo em volta de cada <use>, e é isso que faz uma",
        "     definição servir a todas as variantes. -->",
        "<defs>",
    ]
    for pattern_id, _attrs, geometry in patterns:
        svg.append(f'<g id="geom-{pattern_id}">{geometry}</g>')
    svg += ["</defs>", ""]

    css = [
        "/* ==========================================================================",
        "   HACHURAS — gerado por generate_hatches.py, não editar à mão",
        "",
        "   Uso (os separadores somem no Bonsai, e tudo bem: os seletores casam",
        "   por substring):",
        "",
        "       concrete-fgprincipal1-pen050     linha principal sobre o fundo",
        "       concrete-fgaccent2-bg25          accent médio, com véu de 25%",
        "       cconcrete-cfgprincipal1          concreto só no corte",
        "       pbrick-pfgmuted2                 tijolo só em projeção, apagado",
        "",
        "   O Bonsai já escreve o prefixo material-; o que você digita vem",
        "   depois dele, então cearth chega como material-cearth.",
        "",
        f"   fg: {', '.join(BASE_COLORS)} — sempre com nível 1, 2 ou 3,",
        "   onde 1 é a cor cheia e 3 a mais próxima do fundo.",
        "   bg: 25 — profundidade, não cor. O fundo do tile é sempre a cor da",
        "   página; bg25 põe um véu de --color-veil por cima, branco nos temas",
        "   escuros e preto nos claros, então a leitura é a mesma em todos.",
        "   ========================================================================== */",
        "",
    ]

    for pattern_id, attrs, _geometry in patterns:
        width = attrs.get("width", "1")
        height = attrs.get("height", "1")
        transform = attrs.get("patternTransform", "")
        transform_attr = f' patternTransform="{transform}"' if transform else ""
        head = (f'width="{width}" height="{height}"{transform_attr} '
                f'patternUnits="userSpaceOnUse"')
        default = default_foreground(pattern_id)

        def tile(foreground, opacity):
            # bg1, not bg. The palette carries levels now, so plain --color-bg
            # does not exist: it resolved to nothing and the tile fell through to
            # whatever was underneath, which read as a dark background on a
            # light theme.
            base = (
                f'<rect width="{width}" height="{height}" fill="var(--color-bg1)"/>'
            )
            veil = (
                ""
                if opacity is None
                else f'<rect width="{width}" height="{height}" '
                     f'fill="var(--color-veil)" fill-opacity="{opacity}"/>'
            )
            return (
                f'{base}{veil}<g stroke="var(--color-{foreground})">'
                f'<use xlink:href="#geom-{pattern_id}" href="#geom-{pattern_id}"/></g>'
            )

        # The plain id keeps working, so existing drawings are unaffected.
        svg.append(f'<pattern id="{pattern_id}" {head}>{tile(default, None)}</pattern>')
        css.append(
            f'[class*="material-{pattern_id}"] {{ fill: url(#{pattern_id}); }}'
        )

        for foreground in FOREGROUNDS:
            for depth, opacity in DEPTHS.items():
                suffix = f"-bg{depth}" if depth else ""
                variant = f"{pattern_id}-fg{foreground}{suffix}"
                svg.append(
                    f'<pattern id="{variant}" {head}>'
                    f'{tile(foreground, opacity)}</pattern>'
                )
                # Three scopes for the same hatch: both, projection only, cut
                # only. A neighbouring building in projection needs less graphic
                # weight than the project's own projection, and the material
                # name cannot say which is which — the drawing already knows,
                # through .projection and .cut.
                #
                # The :not() pair matters more than it looks: "cfgwarning1"
                # CONTAINS "fgwarning1", so without it the unscoped rule would
                # fire on a scoped class too and both would apply.
                base = (
                    f'[class*="material-{pattern_id}"]'
                    f'[class*="fg{foreground}"]'
                    f':not([class*="pfg{foreground}"])'
                    f':not([class*="cfg{foreground}"])'
                )
                if depth:
                    base += (
                        f'[class*="bg{depth}"]'
                        f':not([class*="pbg{depth}"])'
                        f':not([class*="cbg{depth}"])'
                    )
                css.append(f'{base} {{ fill: url(#{variant}); }}')
                for scope, prefix in (("projection", "p"), ("cut", "c")):
                    # The scope letter is part of what the user types, and Bonsai
                    # writes material- itself: cdiagonal1 arrives as
                    # material-cdiagonal1.
                    scoped = (
                        f'.{scope}[class*="material-{prefix}{pattern_id}"]'
                        f'[class*="{prefix}fg{foreground}"]'
                    )
                    if depth:
                        scoped += f'[class*="{prefix}bg{depth}"]'
                    css.append(f'{scoped} {{ fill: url(#{variant}); }}')
        for depth in DEPTHS:
            if not depth:
                continue
            css.append(
                f'[class*="material-{pattern_id}"][class*="bg{depth}"]'
                f':not([class*="pbg{depth}"]):not([class*="cbg{depth}"]) '
                f'{{ fill: url(#{pattern_id}-fg{default}-bg{depth}); }}'
            )
            for scope, prefix in (("projection", "p"), ("cut", "c")):
                css.append(
                    f'.{scope}[class*="material-{prefix}{pattern_id}"]'
                    f'[class*="{prefix}bg{depth}"] '
                    f'{{ fill: url(#{pattern_id}-fg{default}-bg{depth}); }}'
                )
        # The hatch alone, scoped: pbrick draws only in projection.
        for scope, prefix in (("projection", "p"), ("cut", "c")):
            css.append(
                f'.{scope}[class*="material-{prefix}{pattern_id}"] '
                f'{{ fill: url(#{pattern_id}); }}'
            )

    svg.append("</svg>")
    return "\n".join(svg), "\n".join(css)


def main():
    patterns = parse(open(SOURCE, encoding="utf-8").read())
    if PATTERNS is not None:
        patterns = [p for p in patterns if p[0] in PATTERNS]
    svg, css = build(patterns)
    open(SVG_OUT, "w", encoding="utf-8").write(svg)
    open(CSS_OUT, "w", encoding="utf-8").write(css)
    print(f"{len(patterns)} hachuras x {len(FOREGROUNDS)} cores x "
          f"{len(DEPTHS)} profundidades = "
          f"{len(patterns) * len(FOREGROUNDS) * len(DEPTHS)} variantes")
    print(f"{SVG_OUT}: {len(svg) / 1024:.0f} KB   {CSS_OUT}: {len(css) / 1024:.0f} KB")


if __name__ == "__main__":
    main()
