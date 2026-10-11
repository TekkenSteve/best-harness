# dsh harness mount image. The CLI version is supplied by harness YAML build args.
#
# Mirrors the opencode mount structurally: @deepseek-ai/dsh is an npm package whose
# bin is a JS entry (lib/bin.js) run under node, so the image installs the package
# with npm, then writes a launcher shim that execs node with the entrypoint by
# ABSOLUTE path under the mount — nothing has to be on the container's PATH except
# the shim itself. ripgrep ships baked-in for the same reason as the opencode mount:
# dsh's grep/glob helpers resolve `rg` from PATH first, and a run-time download
# cannot work under the bench's egress control.

ARG DSH_VERSION

FROM node:22-slim AS build
ARG DSH_VERSION
ARG MOUNT_PATH=/opt/dsh
ENV MOUNT_PATH=${MOUNT_PATH}
RUN test -n "${DSH_VERSION}"
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
RUN npm install -g "@deepseek-ai/dsh@${DSH_VERSION}"

ARG RIPGREP_VERSION=14.1.1
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) rgtriple="x86_64-unknown-linux-musl" ;; \
      arm64) rgtriple="aarch64-unknown-linux-gnu" ;; \
      *) echo "unsupported arch for ripgrep: $arch" >&2; exit 1; ;; \
    esac; \
    url="https://github.com/BurntSushi/ripgrep/releases/download/${RIPGREP_VERSION}/ripgrep-${RIPGREP_VERSION}-${rgtriple}.tar.gz"; \
    curl -fsSL "$url" -o /tmp/rg.tar.gz; \
    tar -xzf /tmp/rg.tar.gz -C /tmp; \
    install -m 0755 "/tmp/ripgrep-${RIPGREP_VERSION}-${rgtriple}/rg" /usr/local/bin/rg; \
    rm -rf /tmp/rg.tar.gz "/tmp/ripgrep-${RIPGREP_VERSION}-${rgtriple}"; \
    rg --version

# Resolve the JS entry from the installed package and write the shim. dsh's bin is
# always lib/bin.js today, but the resolution stays package.json-driven so a future
# native binary execs directly. Fail the BUILD (not the run) when neither exists.
RUN set -eux; \
    mkdir -p /out/bin /out/lib; \
    cp -r /usr/local/lib/node_modules /out/lib/node_modules; \
    cp /usr/local/bin/node /out/bin/node; \
    cp /usr/local/bin/rg /out/bin/rg; \
    node -e ' \
      const fs = require("fs"), path = require("path"); \
      const root = "/usr/local/lib/node_modules/@deepseek-ai/dsh"; \
      const pkg = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8")); \
      const rawBin = pkg.bin; \
      const entry = typeof rawBin === "string" ? rawBin : (rawBin && rawBin.dsh); \
      if (!entry) throw new Error("Cannot find dsh bin entry in @deepseek-ai/dsh package.json"); \
      const mountPath = process.env.MOUNT_PATH || "/opt/dsh"; \
      const rel = entry.replace(/^\.\//, ""); \
      const target = path.posix.join(mountPath, "lib/node_modules/@deepseek-ai/dsh", rel); \
      const nodeBin = path.posix.join(mountPath, "bin/node"); \
      const localEntry = path.join(root, rel); \
      if (!fs.existsSync(localEntry)) throw new Error("resolved entry missing: " + localEntry); \
      const isElf = (() => { try { const fd = fs.openSync(localEntry, "r"); const b = Buffer.alloc(4); \
        fs.readSync(fd, b, 0, 4, 0); fs.closeSync(fd); return b[0] === 0x7f && b.toString("latin1", 1, 4) === "ELF"; \
      } catch { return false; } })(); \
      const cmd = isElf ? `exec "${target}" "$@"` : `exec "${nodeBin}" "${target}" "$@"`; \
      fs.writeFileSync("/out/bin/dsh", `#!/usr/bin/env bash\n${cmd}\n`, { mode: 0o755 }); \
      console.error("shim -> " + (isElf ? "native " : "node ") + target); \
    '

FROM scratch
COPY --from=build /out/bin/ /bin/
COPY --from=build /out/lib/ /lib/
