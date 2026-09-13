# Logo assets

`logo-source.svg` is the ONLY hand-maintained file here. Everything else in this
directory — `icon.svg`, `logo-light.svg`, `logo-dark.svg`, `social-preview.svg` and
`social-preview.png` — is generated from it by `scripts/build_logo_assets.py` and must
never be hand-edited; the CI-enforced staleness check (`tests/test_logo_assets.py`)
fails the build if an output no longer matches the source.

## To change the logo, replace ONE file

1. Replace `docs/assets/logo-source.svg` with the new mark (an outline path plus any
   solid shapes, `viewBox="0 0 64 64"`, light-mode colours baked in — see the source
   file's own comments for what each shape means).
2. Run:
   ```sh
   python3 scripts/build_logo_assets.py
   ```
3. Commit the regenerated files under `docs/assets/`. `pytest` fails
   (`tests/test_logo_assets.py`) if you forget this step.

Colour swap for the dark variant is a fixed table in `build_logo_assets.py`
(`COLOR_SWAP`) — light-mode hex values mapped to their documented dark equivalents —
not a computed inversion, so a colour change is a reviewable one-line diff.

`python3 scripts/build_logo_assets.py --print-snippet` prints the small inline `<svg>`
jsonholdings.com's Open Source card embeds directly (inline, not an external file
request, per that site's own no-CDN rule). Re-run it and re-paste after any
`logo-source.svg` change; it is not synced automatically since it lives in a different
repo (`business/jsonholdings/site`).

## README embed

<!-- Paste into README.md. Renders logo-dark.svg under a dark OS/browser theme,
     logo-light.svg otherwise. GitHub's own README rendering honors prefers-color-scheme
     inside <picture> for images. -->
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/logo-light.svg">
  <img alt="squire" src="docs/assets/logo-light.svg" width="272" height="51">
</picture>
