#!/usr/bin/env python3
"""freebird — the Freebird launcher. One file, any OS (macOS, Linux, Windows).

WHAT IT DOES
  Reads the ceiling the customer set, turns it into kernel-enforced container limits, and runs the
  client. The ceiling is not advice the software follows; it is a cgroup limit the kernel applies.

WHY A LAUNCHER AT ALL
  --cpus is expressed in CORES, but the customer thinks in PERCENT OF THEIR MACHINE. Docker Desktop
  also reports the VM's CPU count, not the host's, so the conversion must happen HERE, on the host,
  or the number silently means something else. Getting that wrong is how a "20%" setting becomes
  60% of a machine.

USAGE
  python3 freebird.py            # start (and apply any config change)
  python3 freebird.py ui         # open the control panel (weekly/monthly compute, ceiling sliders)
  python3 freebird.py status     # what it is using right now
  python3 freebird.py logs       # live output
  python3 freebird.py stop       # stop and remove
  python3 freebird.py config     # show the ceiling it is running with

  Config lives in FREEBIRD_HOME (default ~/.freebird/config.json). Edit it, run `up` again —
  the launcher recreates the container and TELLS YOU what changed. Nothing is silent.
"""
from __future__ import annotations
import json
import os
import platform
import shutil
import subprocess
import sys

IS_WIN = platform.system() == "Windows"
HOME = os.environ.get("FREEBIRD_HOME") or os.path.join(os.path.expanduser("~"), ".freebird")
CONFIG = os.path.join(HOME, "config.json")
TOKEN = os.path.join(HOME, ".token")
IMAGE = os.environ.get("FREEBIRD_IMAGE") or "ghcr.io/shivtcdfinance/freebird-client:0.4"
NAME = "freebird"
HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULTS = {
    "cpu_percent_max": 20,          # percent of THIS machine — the launcher converts it to cores
    "ram_mb_max": 1024,
    "disk_mb_max": 512,
    "contribute": True,             # spare headroom helps the network; you set the ceiling
    "mode": "full",
    "bus_url": "https://bus.shivelinc.com",   # public door (Cloudflare) — works for everyone
    "bus_url_tailnet": "",                    # private door for the owner's own machines only
    "run_seconds": 0,               # 0 = run until stopped
    "poll_interval_s": 2.0,
}


def load_cfg():
    os.makedirs(HOME, exist_ok=True)
    if not os.path.isfile(CONFIG):
        with open(CONFIG, "w") as fh:
            json.dump(DEFAULTS, fh, indent=2)
        print("created %s with the default ceiling (20%% CPU, 1024 MB RAM)" % CONFIG)
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.load(open(CONFIG)))
    except Exception as e:
        sys.exit("cannot read %s: %s" % (CONFIG, e))
    return cfg


def host_cores():
    return os.cpu_count() or 2


def cpu_cores(cfg):
    """Percent of the whole machine -> cores for --cpus. Docker needs cores; the customer gave a %."""
    cores = host_cores()
    pct = max(1.0, min(100.0, float(cfg["cpu_percent_max"])))
    return round(cores * pct / 100.0, 2), cores


RUNTIME = None      # detected once: docker, podman or nerdctl — the CLIENT'S choice, not ours


def _detect_runtime():
    """Use whichever container runtime this machine already has.

    The owner's ruling: the runtime is not ours to choose and its licensing is not ours to decide
    on someone else's behalf. So we do not insist on Docker and we do not steer anyone anywhere —
    we use what they have, in the order most likely to be present, and only ask for an install if
    there is no OCI runtime at all. Any of these runs the same image.
    """
    want = os.environ.get("FREEBIRD_RUNTIME")
    cands = [want] if want else ["docker", "podman", "nerdctl"]
    for c in cands:
        if c and shutil.which(c):
            return c
    return None


def docker(*args, **kw):
    """Runs the container CLI — whatever it is on this machine."""
    global RUNTIME
    if RUNTIME is None:
        RUNTIME = _detect_runtime()
    if RUNTIME is None:
        sys.exit("No container runtime found (looked for docker, podman, nerdctl). Install any one "
                 "of them — whichever you prefer — then run this again.")
    return subprocess.run([RUNTIME, *args], capture_output=True, text=True, **kw)


def ensure_docker():
    """Confirm a runtime exists and is actually running (not just installed)."""
    if docker("info", "--format", "{{.ServerVersion}}").returncode == 0:
        print("runtime: %s" % RUNTIME)
        return
    if platform.system() == "Darwin" and shutil.which("open"):
        print("%s isn't running — starting it..." % RUNTIME)
        if RUNTIME == "docker":
            subprocess.run(["open", "-a", "Docker"], capture_output=True)
        import time
        for _ in range(45):
            time.sleep(4)
            if docker("info", "--format", "{{.ServerVersion}}").returncode == 0:
                print("%s is up." % RUNTIME)
                return
    sys.exit("%s is installed but not running. Start it, then run this again." % RUNTIME)


def ensure_image():
    """Get the client image — PULLED first, local build only as a fallback.

    The published image is multi-arch, so pulling gives the right build for whatever CPU this
    machine has (amd64 or arm64) without the customer compiling anything. Building locally is for
    someone working on the client itself, not for someone installing it.
    """
    if docker("image", "inspect", IMAGE).returncode == 0:
        return
    print("pulling the client image: %s" % IMAGE)
    if docker("pull", IMAGE).returncode == 0:
        return
    if os.path.isfile(os.path.join(HERE, "Dockerfile")):
        print("pull failed — building locally instead...")
        r = docker("build", "-t", IMAGE, HERE)
        if r.returncode == 0:
            return
        sys.exit("image build failed:\n%s" % r.stderr[-800:])
    sys.exit("cannot obtain the client image %s" % IMAGE)


def current(name=NAME):
    """The running container's limits, for comparison — so a config change is never silent."""
    r = docker("inspect", name, "--format",
               "{{.HostConfig.NanoCpus}}|{{.HostConfig.Memory}}|{{.State.Running}}")
    if r.returncode != 0:
        return None
    nc, mem, running = r.stdout.strip().split("|")
    return {"cpus": int(nc) / 1e9 if nc.isdigit() else 0,
            "mem_mb": int(mem) // (1024 * 1024) if mem.isdigit() else 0,
            "running": running == "true"}


def run_args(cfg):
    cores, host = cpu_cores(cfg)
    disk = int(cfg.get("disk_mb_max") or 512)
    ram = int(cfg.get("ram_mb_max") or 1024)
    return [
        "run", "-d",
        "--name", NAME,
        "--cpus", str(cores),                                  # <- the ceiling, in cores
        "--memory", "%dm" % ram,
        "--memory-swap", "%dm" % ram,                          # no swap escape hatch
        "--pids-limit", "128",                                 # a fork bomb cannot take the machine
        "--cap-drop", "ALL",                                   # no Linux capabilities
        "--security-opt", "no-new-privileges",
        "--read-only",                                         # root fs unwritable
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=%dm" % disk,     # the only scratch, size-capped
        "--mount", "type=bind,source=%s,target=/data" % os.path.abspath(HOME),
        "--env", "FREEBIRD_HOME=/data",
        "--network", "bridge",                                  # outbound only; nothing dials in
        "--restart", "unless-stopped",
        "--label", "freebird.client=1",
        IMAGE,
    ]


def up():
    ensure_docker()
    ensure_image()
    cfg = load_cfg()

    # the token: never in argv (docker inspect exposes it), only in the mounted config dir.
    # A key can arrive three ways, in order: the installer put it in FREEBIRD_TOKEN; it is already
    # stored; or (the owner's own machines only) it was staged at /tmp/bus.token. Getting this wrong
    # is silent — every call goes out unauthenticated and the client just appears to find no work.
    tok_env = (os.environ.get("FREEBIRD_TOKEN") or "").strip()
    if tok_env and not os.path.isfile(TOKEN):
        with open(TOKEN, "w") as fh:
            fh.write(tok_env)
        os.chmod(TOKEN, 0o600)
        print("machine key stored in %s" % TOKEN)
    elif not os.path.isfile(TOKEN) and os.path.isfile("/tmp/bus.token"):
        shutil.copy("/tmp/bus.token", TOKEN)
        os.chmod(TOKEN, 0o600)
        print("token staged into %s" % TOKEN)

    before = current()
    if before:
        docker("rm", "-f", NAME)
    cores, host = cpu_cores(cfg)
    print("host: %d cores -> your ceiling of %s%% = %.2f cores, %s MB RAM, %s MB disk"
          % (host, cfg["cpu_percent_max"], cores, cfg["ram_mb_max"], cfg["disk_mb_max"]))
    if before is None:
        print("APPLIED: starting fresh")
    else:
        ch = []
        if abs(before["cpus"] - cores) > 0.001:
            ch.append("CPU %.2f -> %.2f cores" % (before["cpus"], cores))
        if before["mem_mb"] != int(cfg["ram_mb_max"]):
            ch.append("RAM %d -> %d MB" % (before["mem_mb"], int(cfg["ram_mb_max"])))
        print("APPLIED: " + ("; ".join(ch) if ch else "no change (restarted as-is)"))

    r = docker(*run_args(cfg))
    if r.returncode != 0:
        sys.exit("could not start the client:\n%s" % r.stderr[-800:])
    print("client started. It pulls work; nothing connects in to you.")
    return status()


def status():
    c = current()
    if not c:
        print("not running")
        return
    print("container %s: %s | capped at %.2f cores / %d MB RAM"
          % (NAME, "running" if c["running"] else "stopped", c["cpus"], c["mem_mb"]))
    r = docker("stats", "--no-stream", "--format",
               "actual usage: {{.CPUPerc}} CPU, {{.MemUsage}}", NAME)
    if r.returncode == 0 and r.stdout.strip():
        print(r.stdout.strip())
    st = os.path.join(HOME, "status.json")
    if os.path.isfile(st):
        try:
            d = json.load(open(st))
            # Always show WHEN the client wrote this, and which door it came through. Without the
            # timestamp a file left over from a previous run reads as current — which is how a
            # launcher ends up reporting a ceiling that is not the one actually applied.
            print("client reports (as of %s): ceiling %s%% | enforced by %s | endpoint %s"
                  % (d.get("updated"), d.get("ceiling_cpu_percent"), d.get("enforced_by"),
                     d.get("bus_url_active")))
            print("                            local %s | margin %s | errors %s | %s MB RAM"
                  % (d.get("jobs_local"), d.get("jobs_margin"), d.get("errors"),
                     d.get("workers_rss_mb")))
        except Exception:
            pass


def stop():
    ensure_docker()
    r = docker("rm", "-f", NAME)
    print("stopped and removed" if r.returncode == 0 else "was not running")


def logs():
    ensure_docker()
    os.execvp(RUNTIME, [RUNTIME, "logs", "-f", "--tail", "40", NAME])


def config():
    cfg = load_cfg()
    cores, host = cpu_cores(cfg)
    print(json.dumps(cfg, indent=2))
    print("applied as: --cpus=%s on a %d-core machine" % (cores, host))


COMMANDS = {"up": up, "status": status, "stop": stop, "logs": logs, "config": config}


def ui():
    """Open the local control panel — the surface a person actually uses."""
    import freebird_ui
    raise SystemExit(freebird_ui.main("--no-open" not in sys.argv))


COMMANDS["ui"] = ui

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "up"
    if cmd not in COMMANDS:
        sys.exit("usage: freebird [%s]" % "|".join(COMMANDS))
    COMMANDS[cmd]()
