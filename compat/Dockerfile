# Image of the C0 compatibility runner (toolkit-only, never used by projects).
# Only what the harness and the real projects' checks need. Every image is pinned by
# digest; the only ENV is the base image's PATH (checked by `_compat.py preflight`).
FROM ubuntu:24.04@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852

LABEL harness-compat="1"

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      bash make git gcc libc6-dev python3 ca-certificates valgrind \
 && rm -rf /var/lib/apt/lists/*

# uv pinned to the host's version (0.8.24) so lockfiles behave the same.
COPY --from=ghcr.io/astral-sh/uv:0.8.24@sha256:1d31be550ff927957472b2a491dc3de1ea9b5c2d319a9cea5b6a48021e2990a6 /uv /usr/local/bin/uv

# norminette for 42-style C projects (codexion), same version as the host. Transitive
# deps frozen with --exclude-newer; Python from the image, never downloaded. This is
# project-specific tooling: it moves to the C/42 adapter in Track 2.
RUN UV_TOOL_DIR=/opt/uv-tools UV_TOOL_BIN_DIR=/usr/local/bin UV_PYTHON_DOWNLOADS=never \
    uv tool install --python /usr/bin/python3 --exclude-newer 2026-11-01T00:00:00Z \
      norminette==3.3.60 \
 && rm -rf /root/.cache

COPY --chmod=0555 guard_env.sh selfcheck.sh /usr/local/lib/compat/
COPY --chmod=0555 entrypoint.sh /usr/local/bin/compat-entrypoint
COPY --chmod=0555 entrypoint_prep.sh /usr/local/bin/compat-entrypoint-prep

ENTRYPOINT ["/usr/local/bin/compat-entrypoint"]
