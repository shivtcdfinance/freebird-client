#!/bin/sh
# Freebird client installer — one line, any POSIX shell, macOS/Linux/Windows(WSL or Git Bash).
#
#   curl -fsSL https://dl.shivelinc.com/freebird/install.sh | sh
#
# WHAT IT DOES
#   1. finds a container runtime you ALREADY have (docker, podman or nerdctl) — it does not tell
#      you which one to use or install one for you
#   2. installs the client into ~/.freebird/client
#   3. stores your machine key (from FREEBIRD_TOKEN) so the client can identify itself
#   4. applies your ceiling and starts the client
#
# It never asks for a password, never needs root, and never opens a port: the client dials out,
# nothing connects in.
set -eu

BASE="${FREEBIRD_BASE:-https://dl.shivelinc.com/freebird}"
ROOT="${FREEBIRD_HOME:-$HOME/.freebird}"
CLIENT="$ROOT/client"
CPU="${FREEBIRD_CPU:-20}"

say() { printf '  %s\n' "$1"; }
die() { printf '\n  %s\n\n' "$1" >&2; exit 1; }

echo
echo "  Freebird client"
echo "  --------------"

# ── a runtime the person already has ────────────────────────────────────────────────────────────
RT=""
for c in docker podman nerdctl; do
  if command -v "$c" >/dev/null 2>&1; then RT="$c"; break; fi
done
[ -n "$RT" ] || die "No container runtime found (looked for docker, podman, nerdctl).
  Install whichever you prefer, then run this again. Freebird does not choose one for you."
say "runtime        : $RT"

if ! "$RT" info >/dev/null 2>&1; then
  die "$RT is installed but not running. Start it, then run this again."
fi

# ── a downloader ────────────────────────────────────────────────────────────────────────────────
if command -v curl >/dev/null 2>&1; then
  fetch() { curl -fsSL "$1" -o "$2"; }
elif command -v wget >/dev/null 2>&1; then
  fetch() { wget -qO "$2" "$1"; }
else
  die "Neither curl nor wget is available, so the client cannot be downloaded."
fi

# ── python 3 ────────────────────────────────────────────────────────────────────────────────────
# Checked before downloading anything: a machine without python3 would otherwise fetch the client
# and then fail with a bare "python3: not found", which tells the person nothing about what to do.
if ! command -v python3 >/dev/null 2>&1; then
  die "python3 is required and was not found.
  macOS: it ships with the developer tools (run: xcode-select --install)
  Debian/Ubuntu: sudo apt install python3
  Fedora/RHEL:   sudo dnf install python3
  Windows: use WSL or Git Bash, which include it."
fi
say "python3        : $(python3 -V 2>&1)"

# ── the client itself ───────────────────────────────────────────────────────────────────────────
mkdir -p "$CLIENT"
fetch "$BASE/freebird.py" "$CLIENT/freebird.py"
fetch "$BASE/freebird_ui.py" "$CLIENT/freebird_ui.py"
chmod +x "$CLIENT/freebird.py" "$CLIENT/freebird_ui.py"
say "installed into : $CLIENT"

# ── the machine key (shown once in the console, never in a command you have to remember) ────────
if [ -n "${FREEBIRD_TOKEN:-}" ]; then
  umask 077
  printf '%s' "$FREEBIRD_TOKEN" > "$ROOT/.token"
  chmod 600 "$ROOT/.token"
  say "machine key    : stored ($ROOT/.token)"
  unset FREEBIRD_TOKEN
elif [ -f "$ROOT/.token" ]; then
  say "machine key    : already present"
else
  say "machine key    : MISSING — create one in the console (Add a machine), then re-run with"
  say "                 FREEBIRD_TOKEN=<key>"
fi

# ── apply the ceiling and start ─────────────────────────────────────────────────────────────────
cd "$CLIENT"
FREEBIRD_HOME="$ROOT" python3 - "$CPU" <<'PY' 2>/dev/null || true
import json, os, sys
p = os.path.join(os.environ.get("FREEBIRD_HOME") or os.path.expanduser("~/.freebird"), "config.json")
cfg = {}
if os.path.isfile(p):
    try: cfg = json.load(open(p))
    except Exception: cfg = {}
cfg["cpu_percent_max"] = float(sys.argv[1])
os.makedirs(os.path.dirname(p), exist_ok=True)
json.dump(cfg, open(p, "w"), indent=2)
PY

FREEBIRD_HOME="$ROOT" python3 freebird.py up

# A tiny wrapper that pins this install's home, so every later command works regardless of where
# it was installed. Without it, a home other than ~/.freebird is silently forgotten and the next
# command reads somebody else's config — the panel then shows the wrong machine's numbers.
cat > "$CLIENT/freebird" <<EOF
#!/bin/sh
FREEBIRD_HOME="$ROOT" exec python3 "$CLIENT/freebird.py" "\$@"
EOF
chmod +x "$CLIENT/freebird"

cat <<EOF

  Done. Next:
    open the panel   :  $CLIENT/freebird ui
    what it is using :  $CLIENT/freebird status
    stop it          :  $CLIENT/freebird stop

  Your contribution is visible in the console at https://freebird.shivelinc.com
  The ceiling is enforced by the kernel (a cgroup quota), not by the software agreeing to behave.

EOF
