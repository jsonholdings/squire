#!/usr/bin/env python3
"""Build every squire logo asset from ONE geometry source.

    python3 scripts/build_logo_assets.py            # rebuild all outputs
    python3 scripts/build_logo_assets.py --check     # exit 1 if any output is stale
    python3 scripts/build_logo_assets.py --print-snippet   # print the jsonholdings.com card snippet

Swap the logo by replacing ONE file:
    1. Replace docs/assets/logo-source.svg (the shield-and-pack mark: an outline path
       plus two solid rects, viewBox "0 0 64 64", light-mode colours baked in).
    2. Run `python3 scripts/build_logo_assets.py`.
    3. Everything downstream regenerates from that one shape: the icon, both wordmark
       variants, the social-preview image, and the snippet used on jsonholdings.com.
    See docs/assets/logo.md for the exact regenerate command and what it touches.

The dark variant is not re-derived from CSS or `prefers-color-scheme` at build time --
it is the SAME geometry with each light colour swapped for its documented dark
equivalent (`COLOR_SWAP` below), because an SVG shipped as a static file has no
`currentColor`/media-query context once dropped into a README or a card image.

Staleness (like build_avatar.py in the org's `.github` repo): a sha256 of
logo-source.svg is recorded in a manifest next to the outputs. `--check` never
compares rendered PNG/SVG bytes to the source -- a renderer can legitimately produce
different-but-correct bytes for the same input; only the recorded source hash and each
output file's mtime relative to the source are checked.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(HERE, "docs", "assets")
SOURCE = os.path.join(ASSETS, "logo-source.svg")
MANIFEST = os.path.join(ASSETS, "assets.manifest.json")

ICON = os.path.join(ASSETS, "icon.svg")
LOGO_LIGHT = os.path.join(ASSETS, "logo-light.svg")
LOGO_DARK = os.path.join(ASSETS, "logo-dark.svg")
SOCIAL_SVG = os.path.join(ASSETS, "social-preview.svg")
SOCIAL_PNG = os.path.join(ASSETS, "social-preview.png")
OUTPUTS = [ICON, LOGO_LIGHT, LOGO_DARK, SOCIAL_SVG, SOCIAL_PNG]

TAGLINE = "Offload the bulk, keep the context."

# Light-mode hex (as they appear in logo-source.svg) -> the documented dark-mode
# equivalent. Kept as an explicit table, not computed, so a colour change is a
# one-line, reviewable diff rather than a lightness-inversion guess.
COLOR_SWAP = {
    "#806536": "#c9a878",   # bronze stroke
    "#14161a": "#f2f0ea",   # ink fill -> paper-toned fill on a dark ground
}

_PATH_RE = re.compile(r'<path\b[^>]*/>', re.S)
_RECT_RE = re.compile(r'<rect\b[^>]*/>', re.S)


def _read_source():
    if not os.path.isfile(SOURCE):
        raise SystemExit("missing %s" % SOURCE)
    return open(SOURCE, encoding="utf-8").read()


def _shape_elements(svg_text):
    """The mark's actual drawing elements (paths + rects), stripped of the
    source's own <svg>/<title>/comment wrapper, so they can be re-wrapped at a
    different viewBox/transform for each output."""
    return "\n    ".join(_PATH_RE.findall(svg_text) + _RECT_RE.findall(svg_text))


def _recolor(svg_fragment):
    out = svg_fragment
    for light, dark in COLOR_SWAP.items():
        out = out.replace(light, dark)
    return out


def build_icon(source_text):
    # The source already IS the icon at native scale -- copy verbatim.
    open(ICON, "w", encoding="utf-8").write(source_text)


def _wordmark_svg(shapes, text_fill):
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 340 64" width="340" height="64" '
        'role="img" aria-label="squire logo">\n'
        '  <title>squire</title>\n'
        '  <g transform="translate(2,2) scale(0.875)">\n'
        f'    {shapes}\n'
        '  </g>\n'
        '  <text x="76" y="42" font-family="ui-monospace, SFMono-Regular, \'SF Mono\', Menlo, '
        'Consolas, \'Liberation Mono\', monospace" font-size="30" font-weight="600" '
        f'letter-spacing="0.5" fill="{text_fill}">squire</text>\n'
        '</svg>\n'
    )


def build_wordmarks(source_text):
    shapes_light = _shape_elements(source_text)
    open(LOGO_LIGHT, "w", encoding="utf-8").write(_wordmark_svg(shapes_light, "#14161a"))
    shapes_dark = _recolor(shapes_light)
    open(LOGO_DARK, "w", encoding="utf-8").write(_wordmark_svg(shapes_dark, "#f2f0ea"))


def build_social_preview(source_text):
    shapes = _recolor_none = _shape_elements(source_text)  # light colours, as authored
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 640" width="1280" height="640">
  <title>squire — {TAGLINE}</title>
  <rect x="0" y="0" width="1280" height="640" fill="#fbfaf7"/>
  <rect x="0" y="0" width="1280" height="640" fill="none" stroke="#e3ded4" stroke-width="4"/>

  <g transform="translate(120,180) scale(2.8125)">
    {shapes}
  </g>

  <text x="340" y="300" font-family="ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace"
        font-size="96" font-weight="600" letter-spacing="1" fill="#14161a">squire</text>

  <text x="342" y="360" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
        font-size="30" fill="#4a4f57">{TAGLINE}</text>

  <line x1="342" y1="400" x2="900" y2="400" stroke="#e3ded4" stroke-width="2"/>
  <text x="342" y="440" font-family="ui-monospace, SFMono-Regular, monospace" font-size="22" fill="#676d75">A JSON Holdings project</text>
</svg>
'''
    open(SOCIAL_SVG, "w", encoding="utf-8").write(svg)
    proc = subprocess.run(["rsvg-convert", "-w", "1280", "-h", "640", "-o", SOCIAL_PNG, SOCIAL_SVG],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise SystemExit("rsvg-convert failed on social-preview.svg: %s" % proc.stderr.strip())


def site_snippet(source_text):
    """The inline SVG jsonholdings.com's Open Source card embeds directly (no external
    file request, per that site's own CLAUDE.md: no CDNs, everything served locally --
    an inline snippet is the only form that satisfies both that rule and this repo's)."""
    shapes = _shape_elements(source_text)
    return (
        '<svg viewBox="0 0 64 64" width="28" height="28" role="img" aria-label="squire" '
        'style="vertical-align:-6px">\n'
        f'  {shapes}\n'
        '</svg>'
    )


def build():
    source_text = _read_source()
    build_icon(source_text)
    build_wordmarks(source_text)
    build_social_preview(source_text)
    manifest = {"source_sha256": _hash(SOURCE), "outputs": [os.path.basename(p) for p in OUTPUTS]}
    json.dump(manifest, open(MANIFEST, "w"), indent=2)
    print("wrote %s" % ", ".join(os.path.relpath(p, HERE) for p in OUTPUTS))


def _hash(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def check():
    if not os.path.isfile(MANIFEST):
        print("STALE: %s is missing" % os.path.relpath(MANIFEST, HERE))
        return 1
    manifest = json.load(open(MANIFEST))
    if manifest.get("source_sha256") != _hash(SOURCE):
        print("STALE: logo-source.svg changed since the assets were built -- "
              "run scripts/build_logo_assets.py")
        return 1
    src_mtime = os.path.getmtime(SOURCE)
    for path in OUTPUTS:
        if not os.path.isfile(path):
            print("STALE: %s is missing" % os.path.relpath(path, HERE))
            return 1
        if os.path.getmtime(path) < src_mtime:
            print("STALE: %s is older than logo-source.svg on disk" % os.path.relpath(path, HERE))
            return 1
    print("all logo assets are current")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--print-snippet", action="store_true")
    args = ap.parse_args()
    if args.print_snippet:
        print(site_snippet(_read_source()))
        return
    if args.check:
        sys.exit(check())
    build()


if __name__ == "__main__":
    main()
