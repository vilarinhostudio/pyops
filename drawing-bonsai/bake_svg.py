"""Resolve the CSS colour/hatch rules of an exported Bonsai SVG into inline
style="" attributes, so the drawing opens correctly outside a browser.

Why this is needed: every colour and hatch rule in the exported CSS matches by
ATTRIBUTE SUBSTRING — [class*="material-..."] — which Inkscape and Affinity
support poorly or not at all. A plain tag+class selector (path.outline, .cut)
already works fine in both, and this script leaves those alone; it only bakes
the substring-matched rules, which are exactly the fill/hatch/colour ones.

What it does NOT touch: font-face, the .cut/.projection base rules, line
weights on path.outline/.crease — those use simple selectors Inkscape already
understands, and re-baking them would be extra work for no benefit (YAGNI).

Usage:
    python bake_svg.py TERREO_PLAN-315.svg

Writes TERREO_PLAN-315-baked.svg next to the input. Open the *-baked* file in
Inkscape or Affinity; keep using the original for the web, where the CSS
already works.
"""

import re
import sys

NOT_RE = re.compile(r':not\(\[class\*="([^"]+)"\]\)')
SCOPE_RE = re.compile(r'^\.(cut|projection)')
REQUIRE_RE = re.compile(r'\[class\*="([^"]+)"\]')
BLOCK_RE = re.compile(r'([^{}]+)\{([^{}]*)\}')
# !important is optional: the hatch-fill rules in the real generator do not
# carry it (only the solid-colour semantic rules do), and both need baking.
DECL_RE = re.compile(r'(fill|stroke)\s*:\s*([^;!]+?)\s*(?:!important)?\s*;')
TAG_RE = re.compile(r'<(\w+)((?:\s+[\w:.-]+="[^"]*")*)\s*/?>')
STYLE_ATTR_RE = re.compile(r'\sstyle="([^"]*)"')
CLASS_ATTR_RE = re.compile(r'\sclass="([^"]*)"')


def parse_selector(selector):
    """One selector -> (scope, requires, excludes), or None if unsupported.

    Only selectors of the shape `[.scope][class*="a"][class*="b"]:not(...)`
    are handled — a single element, no descendant combinator. Anything with a
    space (a descendant selector, e.g. the "path" scoped rules) is skipped:
    those already work via plain tag+class matching in Inkscape, so baking
    them would add complexity nothing needs.
    """
    selector = selector.strip()
    excludes = NOT_RE.findall(selector)
    selector = NOT_RE.sub("", selector)

    scope = None
    match = SCOPE_RE.match(selector)
    if match:
        scope = match.group(1)
        selector = selector[match.end():]

    requires = REQUIRE_RE.findall(selector)
    remainder = REQUIRE_RE.sub("", selector).strip()
    if remainder or not requires:
        return None  # leftover text: a descendant/tag selector, not ours

    return scope, requires, excludes


def parse_css(css_text):
    """CSS text -> ordered list of (scope, requires, excludes, {prop: value}).

    Order is kept because our stylesheet relies on later rules overriding
    earlier ones for the same property — every declaration carries
    !important, so with matching specificity the LAST rule in the file wins,
    same as a browser would resolve it.
    """
    rules = []
    for selectors, body in BLOCK_RE.findall(css_text):
        declarations = dict(DECL_RE.findall(body))
        if not declarations:
            continue
        for raw in selectors.split(","):
            parsed = parse_selector(raw)
            if parsed is None:
                continue
            scope, requires, excludes = parsed
            rules.append((scope, requires, excludes, declarations))
    return rules


def resolve(class_string, rules):
    """The final {prop: value} for one element's class string."""
    tokens = class_string.split()
    scope = "cut" if "cut" in tokens else ("projection" if "projection" in tokens else None)

    resolved = {}
    for rule_scope, requires, excludes, declarations in rules:
        if rule_scope is not None and rule_scope != scope:
            continue
        if not all(token in class_string for token in requires):
            continue
        if any(token in class_string for token in excludes):
            continue
        resolved.update(declarations)  # last match wins, per CSS rule order
    return resolved


def bake(svg_text, rules):
    """Write a style="" attribute into every tag whose class matches a rule.

    Done as a targeted regex substitution over the ORIGINAL text rather than a
    full XML reparse-and-rewrite: this file carries embedded base64 font data
    and mixed href/xlink:href attributes that a generic serializer could
    subtly reformat. Only tags with a class attribute are touched; everything
    else in the file is untouched byte for byte.
    """
    count = 0

    def replace(match):
        nonlocal count
        tag_text = match.group(0)
        class_match = CLASS_ATTR_RE.search(tag_text)
        if not class_match:
            return tag_text

        declarations = resolve(class_match.group(1), rules)
        if not declarations:
            return tag_text

        addition = ";".join(f"{prop}:{value}" for prop, value in declarations.items())
        style_match = STYLE_ATTR_RE.search(tag_text)
        if style_match:
            # Appended, not prepended: in a single style attribute the LAST
            # occurrence of a property wins, so this keeps our baked values
            # authoritative over whatever was already there.
            existing = style_match.group(1)
            new_style = f' style="{existing};{addition}"'
            tag_text = tag_text[:style_match.start()] + new_style + tag_text[style_match.end():]
        else:
            tag_text = tag_text[:-1].rstrip("/") + f' style="{addition}">'
            if match.group(0).endswith("/>"):
                tag_text = tag_text[:-1] + "/>"

        count += 1
        return tag_text

    baked = TAG_RE.sub(replace, svg_text)
    return baked, count


def main():
    if len(sys.argv) != 2:
        print("Uso: python bake_svg.py arquivo.svg")
        raise SystemExit(1)

    path = sys.argv[1]
    text = open(path, encoding="utf-8").read()

    css_text = "".join(re.findall(r"<style[^>]*>(.*?)</style>", text, re.S))
    rules = parse_css(css_text)
    if not rules:
        print("[bake] Nenhuma regra de substring encontrada no CSS embutido.")
        raise SystemExit(1)

    baked_text, count = bake(text, rules)
    output = path.rsplit(".", 1)[0] + "-baked.svg"
    open(output, "w", encoding="utf-8").write(baked_text)
    print(f"[bake] {len(rules)} regras lidas, {count} elementos com style gravado.")
    print(f"[bake] {output}")


if __name__ == "__main__":
    main()
