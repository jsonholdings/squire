<!-- Paste into README.md. Renders logo-dark.svg under a dark OS/browser theme,
     logo-light.svg otherwise. GitHub's own README rendering honors prefers-color-scheme
     inside <picture> for images. -->
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/logo-light.svg">
  <img alt="squire" src="docs/assets/logo-light.svg" width="272" height="51">
</picture>
