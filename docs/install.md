# Install

## From source
```sh
git clone <repo-url> squire && cd squire
python3 squire.py --help
ln -s "$(pwd)/squire.py" ~/bin/squire   # optional: put it on PATH
```
Requires only Python 3 stdlib and a running local backend (see [backends.md](backends.md)).

## pipx
The package (`squire-offload`) is not on PyPI. Install it from GitHub:
`pipx install "squire-offload @ git+https://github.com/jsonholdings/squire.git"`.

## Single-file zipapp
Built via `pyproject.toml`'s packaging: `python3 squire.pyz --help`, no install step. GitHub
releases exist (see [Releases](https://github.com/jsonholdings/squire/releases)); the zipapp is
not yet attached as a release asset.

## Docker
```sh
cd docker
docker compose up                                              # GPU
docker compose -f docker-compose.yml -f docker-compose.cpu.yml up   # CPU fallback
docker compose run --rm squire doctor --json                   # 0 ready / 2 UNKNOWN
```
Brings up `ollama` (healthchecked, bound to `127.0.0.1:11434`, override with
`SQUIRE_STACK_OLLAMA_PORT`) and a one-shot `model-pull` job for `SQUIRE_MODEL` and
`SQUIRE_EMBED_MODEL`. `squire` runs as a one-off container pinned to talk only to the `ollama`
service (`SQUIRE_ALLOW_HOSTS=ollama`). VERIFIED: `docker compose config` validates; a live Ollama
health check passed on an alternate port during testing. Full detail: `docker/README.md`.

## Verifying it's ready
```sh
squire doctor --json    # 0 = backend + model(s) ready, 2 = UNKNOWN, never a false "ready"
```
