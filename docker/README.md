# squire-stack (Docker)

One-command way to run squire's local backend (Ollama) plus the `squire` CLI
in containers, for anyone who doesn't want to install Ollama on bare metal.

Everything binds to `127.0.0.1` only. No telemetry, no LAN/WAN exposure.

## Prerequisites

- Docker Engine with the Compose plugin (`docker compose version`).
- **GPU (recommended):** an NVIDIA GPU + the
  [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
  installed on the host (`nvidia-ctk`), so Docker can grant containers GPU
  access. Verify with `docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi`.
- **No GPU:** use the CPU override below. Model inference will be much slower
  (qwen2.5:14b is a 14B model), but it works.

## Bring up the backend

```sh
cd docker
docker compose up          # GPU (default)
# or
docker compose -f docker-compose.yml -f docker-compose.cpu.yml up   # CPU fallback
```

This starts:
- `ollama` — the model server, healthchecked, port bound to `127.0.0.1:11434`
  (override with `SQUIRE_STACK_OLLAMA_PORT` if that port is taken, e.g. by a
  host Ollama already running).
- `model-pull` — a one-shot init job that pulls `SQUIRE_MODEL`
  (default `qwen2.5:14b`) and `SQUIRE_EMBED_MODEL` (default `nomic-embed-text`),
  then exits. `docker compose up` waits for it to finish before anything else
  that depends on it runs.

## Run squire

`squire` is a CLI, not a long-running server, so it's invoked per command
rather than started with `up`:

```sh
docker compose run --rm squire run -- pytest -q
docker compose run --rm squire sum somefile.log
docker compose run --rm squire ask "what failed?" somefile.log
docker compose run --rm squire stats
docker compose run --rm squire doctor --json    # 0 = backend+models ready, 2 = UNKNOWN
```

Inside compose, `squire` is pinned to talk only to the `ollama` service
(`SQUIRE_OLLAMA=http://ollama:11434`, `SQUIRE_ALLOW_HOSTS=ollama`) — squire
refuses any backend host that isn't localhost/127.0.0.1/::1 unless it's on
that allow-list, so `ollama` (the compose network hostname) is explicitly
allowed. The `squire` service also carries a healthcheck that runs
`squire doctor --json`; note Docker treats any non-zero exit as unhealthy, so
squire's own UNKNOWN (exit 2) and a hard failure both show as "unhealthy" in
`docker ps` — check `docker compose run --rm squire doctor` (non-`--json`) or
`docker inspect` for the distinction.

The `squire` service is built from `Dockerfile` (python:3-slim, stdlib only,
copies `../squire.py`) and talks to the `ollama` service over the compose
network at `http://ollama:11434`. Its ledger persists in the `squire-data`
named volume.

## Pointing a host-installed squire at this stack instead

If you'd rather run `squire.py` directly on the host and just use the
container for Ollama:

```sh
export SQUIRE_OLLAMA=http://127.0.0.1:${SQUIRE_STACK_OLLAMA_PORT:-11434}
```

## Tear down

```sh
docker compose down            # stop and remove containers
docker compose down -v         # also delete the model/ledger volumes
```

## Notes

- This compose file is independent of any Ollama already running on the host.
  If port 11434 is taken, set `SQUIRE_STACK_OLLAMA_PORT` to something else
  (e.g. 11435) before bringing the stack up.
- `SQUIRE_MODEL` / `SQUIRE_EMBED_MODEL` env vars override the pulled models;
  keep them consistent between `model-pull` and `squire`.
- Per squire's design (see `../squire.py` header, `../CLAUDE.md` §5.8): the
  exit code and raw tail of any wrapped command are always real; model output
  is a local, ASSUMED-until-checked summary, never a pass/fail or security
  decision.
