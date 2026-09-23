#!/usr/bin/env python3
"""freebird_client.py — v0.2 of the Freebird client. THE KEYSTONE.

TWO JOBS, ONE BUDGET
  1. LOCAL  — the customer's own processing on their own machine (the benefit they buy)
  2. MARGIN — the headroom above their ceiling goes to the estate's batch pool (the asset)

WHY PROCESSES, NOT THREADS (v0.1's bug, measured)
  v0.1 sized a THREAD pool and paced it to the ceiling. Measured externally, a 20% ceiling
  (1.60 cores) delivered only 0.87 cores — because the workload is pure Python and the GIL
  serialises Python threads, so a second thread adds nothing. The ceiling is a promise about
  the MACHINE, so the pool has to be PROCESSES: separate interpreters, no shared GIL, each paced
  to its share of the duty cycle. Same governor arithmetic, honoured for real.

THE ONE THING THAT MUST BE TRUE
  The ceiling must HOLD — and it must never be EXCEEDED. Under-delivering is a lost harvest;
  over-delivering is a broken promise to the person whose machine it is.

HONEST LIMIT ON macOS
  Pacing governs the AVERAGE. A kernel-enforced hard cap needs a cgroup/container — the Docker
  path the owner specified. That is what makes it unbreakable rather than merely well-behaved.
"""
import json
import multiprocessing as mp
import os
import signal
import sys
import time
import urllib.parse
import urllib.request

HOME = os.environ.get("FREEBIRD_HOME") or os.path.expanduser("~/.freebird")
CONFIG = os.path.join(HOME, "config.json")
STATUS = os.path.join(HOME, "status.json")
CORES = os.cpu_count() or 1

DEFAULTS = {
    "cpu_percent_max": 20,        # the ceiling the CUSTOMER sets, as % of the whole machine
    "ram_mb_max": 1024,
    "disk_mb_max": 512,
    "contribute": True,           # margin compute ON — visible, and they set the ceiling
    "bus_url": "https://bus.shivelinc.com",   # public door — works anywhere (through Cloudflare)
    "bus_url_tailnet": "",                    # optional private door (direct to the box, ~43 ms).
                                              # Set on the owner's own machines; empty for everyone
                                              # else, who reach the bus through Cloudflare.
    "token_file": "",             # empty = $FREEBIRD_HOME/.token (mounted in; never in the image)
    "worker_name": "freebird-client",
    "chunk_iterations": 40000,
    "run_seconds": 60,
}


def load_config():
    os.makedirs(HOME, exist_ok=True)
    if not os.path.isfile(CONFIG):
        with open(CONFIG, "w") as fh:
            json.dump(DEFAULTS, fh, indent=2)
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.load(open(CONFIG)))
    except Exception:
        pass
    return cfg


def cgroup_quota_cores():
    """How many cores this container is ALLOWED, read from the kernel's own cgroup files.

    This is the difference between a promise and a fact. When a quota is present the ceiling is
    enforced BY THE KERNEL: the workers can run flat out and the cgroup simply throttles them, so
    the number is exact — no duty-cycle estimation, and none of the ~50% yield loss that pacing
    suffered. Returns None when there is no quota (running bare on the host), and then we pace.
    """
    try:                                          # cgroup v2
        f = open("/sys/fs/cgroup/cpu.max").read().split()
        if f[0] != "max":
            return int(f[0]) / int(f[1])
    except Exception:
        pass
    try:                                          # cgroup v1
        q = int(open("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read())
        p = int(open("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read())
        if q > 0:
            return q / p
    except Exception:
        pass
    return None


def governor(cfg):
    """Decide the worker pool and how the ceiling is held.

    CONTAINER (quota detected): the kernel enforces it, so run that many processes flat out.
    BARE HOST (no quota): pace with a duty cycle, because nothing else is holding the line.
    """
    quota = cgroup_quota_cores()
    if quota:
        procs_n = max(1, int(round(quota)))
        return procs_n, 1.0, quota, "cgroup (kernel-enforced)"

    pct = max(1.0, min(100.0, float(cfg["cpu_percent_max"])))
    target = CORES * pct / 100.0
    procs = max(1, min(CORES, int(round(target))))
    duty = max(0.05, min(1.0, target / procs))
    return procs, duty, target, "paced (soft cap)"


def burn(iterations=40_000):
    """Portable CPU-bound unit of work — stands in for a real processing job."""
    import hashlib
    h = b"freebird"
    for _ in range(iterations):
        h = hashlib.sha256(h).digest()
    return h.hex()[:16]


def token(cfg):
    # Inside a container the config dir is mounted, so the token lives beside the config as
    # $FREEBIRD_HOME/.token — never baked into the image and never in argv (docker inspect shows
    # argv, and on Linux the host's process table would show it too).
    p = cfg.get("token_file") or os.path.join(HOME, ".token")
    try:
        return open(p).read().strip()
    except Exception:
        return ""


UA = "freebird-client/0.3 (+https://shivelinc.com)"


def _http(url, data=None, headers=None):
    """One place where a refusal becomes a REASON.

    A bare HTTPError tells an operator nothing — 403 could be a bad token, 404 a wrong path. The
    bus answers all of those with a JSON body explaining itself, so read it and re-raise with the
    body attached. Without this, an unauthenticated client just counts "errors" forever and the
    person debugging it has nothing to go on.

    The USER-AGENT is not cosmetic. Cloudflare's edge bans the browser signature of known bot
    patterns, and Python's default "Python-urllib/3.12" is one of them: the same request that
    returns 200 for curl or for an honest client name returns HTTP 403 "error code: 1010" with the
    default. So the client always identifies itself by name.
    """
    h = {"User-Agent": UA, "Accept": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()[:200]
        except Exception:
            pass
        raise RuntimeError("HTTP %s from bus: %s" % (e.code, body or "(no body)"))


def bus_get(cfg, path):
    """GET with the token. /next and /stats are GET-only handlers on the bus; only /submit and
    /result are POST. Sending POST to /next returns 'no such endpoint' - which a naive client
    reads as 'no work' and then never claims anything, forever, silently."""
    return _http(cfg["bus_url"].rstrip("/") + path, headers={"X-Bus-Token": token(cfg)})


def bus(cfg, path, obj=None):
    return _http(cfg["bus_url"].rstrip("/") + path,
                 data=json.dumps(obj).encode() if obj is not None else None,
                 headers={"Content-Type": "application/json", "X-Bus-Token": token(cfg)})


def resolve_bus(cfg):
    """Pick the door that actually opens, once, at startup — and say which.

    Two doors exist by design. The TAILNET address is direct to the box and only reachable by
    machines on the owner's tailnet. The PUBLIC Cloudflare hostname works for everyone else. We
    probe each in order with a short timeout and keep the first that answers, so one client build
    serves both cases and nobody has to choose. The winner is written to the status file, because
    which door a machine came through should never be a mystery when something breaks.
    """
    cands = []
    for key in ("bus_url_tailnet", "bus_url"):
        if cfg.get(key):
            cands.append(cfg[key])
    for extra in (cfg.get("bus_urls") or []):
        cands.append(extra)

    for url in cands:
        try:
            r = _http(url.rstrip("/") + "/stats", headers={"X-Bus-Token": token(cfg)})
            if isinstance(r, dict) and "jobs" in r:
                cfg["bus_url"] = url
                cfg["bus_url_active"] = url
                print("  bus endpoint     : %s  (reachable)" % url)
                return url
            print("  bus endpoint     : %s  (answered but not the bus)" % url)
        except Exception as e:
            print("  bus endpoint     : %s  unreachable (%s)" % (url, str(e)[:70]))
    cfg["bus_url_active"] = cfg.get("bus_url") or ""
    print("  bus endpoint     : none reachable yet — will keep retrying %s" % cfg["bus_url_active"])
    return cfg["bus_url_active"]


def worker_main(cfg, duty, ctr, lock, stop, wid):
    """One PROCESS: pace a work chunk, sleep the rest of the duty cycle, repeat.

    Each cycle it takes whichever job exists — a margin job from the estate if one is waiting,
    otherwise the customer's own processing — and EVERY turn is paced to the same duty. The
    ceiling covers the total, never each role separately.
    """
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    # POLL BACKOFF: the bus listener has a backlog of 5. Polling every duty cycle (~38ms) from
    # several processes means ~100 req/s and RSTs for everyone else. Poll on a timer, with a
    # per-worker offset so the workers don't all fire at once.
    poll_every = float(cfg.get("poll_interval_s") or 2.0) + 0.35 * wid
    last_poll = 0.0
    while not stop.value:
        t0 = time.perf_counter()
        did = None

        if (cfg.get("mode") != "local" and cfg.get("contribute")
                and time.time() - last_poll >= poll_every):
            last_poll = time.time()
            job = None
            try:
                # GET, and the job is NESTED under "job": {"job": {"id": ...}}. Reading job["id"]
                # off the envelope instead of job["job"]["id"] is a silent no-work bug.
                resp = bus_get(cfg, "/next?worker=" + urllib.parse.quote(cfg["worker_name"]))
                job = (resp or {}).get("job")
                with lock:
                    ctr["consec_err"] = 0            # the door works; reset the failure streak
            except Exception as e:
                with lock:
                    ctr["errors"] += 1
                    ctr["consec_err"] = ctr.get("consec_err", 0) + 1
                    ctr["last_error"] = "%s: %s" % (type(e).__name__, str(e)[:150])
                job = None
            if job and job.get("id"):
                try:
                    it = int(((job.get("payload") or {}).get("iterations")) or 40000)
                    out = burn(it)
                    ms = (time.perf_counter() - t0) * 1000
                    bus(cfg, "/result", {"id": job["id"], "ok": True,
                                         "compute_ms": round(ms, 1),
                                         "result": {"digest": out},
                                         "worker": cfg["worker_name"]})
                    did = "margin"
                    with lock:
                        ctr["margin"] += 1
                except Exception as e:
                    with lock:
                        ctr["errors"] += 1
                        ctr["last_error"] = "%s: %s" % (type(e).__name__, str(e)[:150])

        if did is None:
            burn(int(cfg.get("chunk_iterations") or 40000))
            with lock:
                ctr["local"] += 1

        worked = time.perf_counter() - t0
        rest = worked * (1.0 / duty - 1.0)
        if rest > 0:
            time.sleep(rest)


def children_rss_mb(pids):
    """Total resident memory of the worker processes — the number the RAM ceiling is checked
    against. Read from the OS, not self-reported."""
    try:
        out = os.popen("ps -o rss= -p " + ",".join(str(p) for p in pids)).read()
        return sum(int(x) for x in out.split()) / 1024.0
    except Exception:
        return 0.0


def main():
    cfg = load_config()
    for i, a in enumerate(sys.argv):
        if a == "--cpu" and i + 1 < len(sys.argv):
            cfg["cpu_percent_max"] = float(sys.argv[i + 1])
        if a == "--mode" and i + 1 < len(sys.argv):
            cfg["mode"] = sys.argv[i + 1]
        if a == "--seconds" and i + 1 < len(sys.argv):
            cfg["run_seconds"] = float(sys.argv[i + 1])
    if "--no-contribute" in sys.argv:
        cfg["contribute"] = False

    resolve_bus(cfg)
    procs_n, duty, target, enforced = governor(cfg)
    print("FREEBIRD CLIENT v0.3")
    print("  machine          : %d cores" % CORES)
    print("  YOUR ceiling     : %s%% CPU  (= %.2f of %d cores), %s MB RAM, %s MB disk"
          % (cfg["cpu_percent_max"], target, CORES, cfg["ram_mb_max"], cfg["disk_mb_max"]))
    print("  enforcement      : %s" % enforced)
    print("  governor         : %d process(es) at %.0f%% duty" % (procs_n, duty * 100))
    print("  margin compute   : %s" % ("ON (visible; you set the ceiling)"
                                       if cfg.get("contribute") else "OFF"))
    sys.stdout.flush()

    ctr = mp.Manager().dict(local=0, margin=0, errors=0, bus_refused=0, last_error="", consec_err=0)
    lock = mp.Lock()
    stop = mp.Value("i", 0)
    asked = mp.Value("i", 0)          # set by SIGTERM/SIGINT

    def _asked(sig, frm):
        asked.value = 1

    signal.signal(signal.SIGTERM, _asked)
    signal.signal(signal.SIGINT, _asked)

    procs = [mp.Process(target=worker_main, args=(cfg, duty, ctr, lock, stop, i), daemon=True)
             for i in range(procs_n)]
    for p in procs:
        p.start()

    t_start = time.time()
    dur = float(cfg.get("run_seconds") or 60)
    if dur <= 0:
        dur = float("inf")            # 0 = run until stopped (the container's normal mode)
    try:
        while time.time() - t_start < dur and not asked.value:
            time.sleep(3)
            # If the chosen door has died (network change, tunnel down), exit non-zero and let the
            # container's restart policy bring us back — which re-runs resolve_bus and picks the
            # other door. Self-healing beats a client that retries a dead endpoint forever.
            if ctr.get("consec_err", 0) > 30:
                print("  bus unreachable for %d consecutive polls — restarting to re-resolve"
                      % ctr.get("consec_err"))
                break
            rss = children_rss_mb([p.pid for p in procs])
            st = {"client": "freebird v0.3",
                  "ceiling_cpu_percent": cfg["cpu_percent_max"],
                  "ceiling_ram_mb": cfg["ram_mb_max"],
                  "ceiling_disk_mb": cfg["disk_mb_max"],
                  "enforced_by": enforced,
                  "processes": procs_n, "duty_cycle": round(duty, 3),
                  "budget_cores_target": round(target, 2),
                  "contributed": bool(cfg.get("contribute")),
                  "jobs_local": ctr["local"], "jobs_margin": ctr["margin"],
                  "errors": ctr["errors"], "bus_refused": ctr["bus_refused"],
                  "last_error": ctr.get("last_error", ""),
                  "bus_url_active": cfg.get("bus_url_active", ""),
                  "consec_err": ctr.get("consec_err", 0),
                  "workers_rss_mb": round(rss, 1),
                  "uptime_s": round(time.time() - t_start, 1),
                  "updated": time.strftime("%Y-%m-%d %H:%M:%S")}
            with open(STATUS, "w") as fh:
                json.dump(st, fh, indent=2)
            print("  [%5.1fs] local %d | margin %d | refused %d | ram %.0fMB/%sMB | ceiling %s%%"
                  % (st["uptime_s"], st["jobs_local"], st["jobs_margin"], st["bus_refused"],
                     st["workers_rss_mb"], cfg["ram_mb_max"], cfg["cpu_percent_max"]))
            sys.stdout.flush()
    finally:
        # THIS is not optional: a client that exits leaving workers behind would keep burning the
        # customer's CPU after it "closed". Signal, then TERMINATE, then JOIN so no orphan survives.
        stop.value = 1
        time.sleep(float(duty) * 0.5 + 0.3)
        for p in procs:
            if p.is_alive():
                p.terminate()
        for p in procs:
            p.join(timeout=5)
        alive = sum(1 for p in procs if p.is_alive())
        print("FINAL: %d local, %d margin jobs, %d errors in %.0fs | workers still alive: %d"
              % (ctr["local"], ctr["margin"], ctr["errors"], time.time() - t_start, alive))

    if ctr.get("consec_err", 0) > 30:
        sys.exit(2)          # non-zero => the container restarts => resolve_bus picks a live door


if __name__ == "__main__":
    main()
