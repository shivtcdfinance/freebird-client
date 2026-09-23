# Freebird client

Runs the processing **on your own machine**, inside the ceiling you set. Everything above that
ceiling is spare headroom the network may use; nothing beyond the number you configure is ever
touched, and the limit is enforced by your operating system kernel — not by the software politely
agreeing to behave. Switching contribution off leaves the software working.

## Run it

```bash
python3 freebird.py            # start (and apply any config change)
python3 freebird.py ui         # open the control panel in your browser (127.0.0.1:8787)
python3 freebird.py status     # what it is using right now
python3 freebird.py logs       # live output
python3 freebird.py stop       # stop and remove
python3 freebird.py config     # show the ceiling it is running with
```

No Docker required specifically — it uses whichever container runtime you already have
(`docker`, `podman` or `nerdctl`). Force one with `FREEBIRD_RUNTIME=podman`. **We do not recommend a
runtime**; bring the one you already run.

## The ceiling

`~/.freebird/config.json`:

| setting | meaning |
|---|---|
| `cpu_percent_max` | percent of **this whole machine** (converted to cores for the container) |
| `ram_mb_max` | hard memory cap, swap pinned so it cannot be exceeded |
| `disk_mb_max` | size cap for the only writable scratch area |
| `contribute` | whether spare headroom helps the network (your ceiling still applies) |
| `bus_url` | the door it dials. Leave as published unless told otherwise |
| `bus_url_tailnet` | the owner's private door. Empty for anyone not on the tailnet |
| `run_seconds` | `0` = run until stopped. Not a timer |

Edit the file and run `freebird.py` again — it recreates the container and **prints what changed**.
Nothing is applied silently.

## Everything it can do today

- **Work, without you thinking about it.** It dials out, pulls work, finishes it, and reports how
  much it did. It never accepts anything dialled in — there is no listening port.
- **A local control panel** (`freebird.py ui`): compute contributed this week / this month / all
  time, a five-window table, the ceiling sliders showing the applied difference, and what is
  actually being used against what is allowed.
- **A web console** at `freebird.shivelinc.com` for the same numbers per account, plus the list of
  machines contributing under that account and the ability to revoke one on its own.
- **Per-machine identity.** Each install carries its own key, minted from the console or the
  download. One download, one machine. A key is stored only as a hash and is shown once.
- **Honest reporting.** The console reports *how much* compute; it never reports *what* was
  processed. No per-job view, no workload names, no process breakdown — by design, not by omission.
- **It survives its own network.** If the door cannot be reached it backs off and retries rather
  than exiting (see the known defect below — this is the part still to finish).

## Known defects — carried into the next build

1. **It gives up instead of waiting.** After 30 consecutive failed polls the client exits and relies
   on the container's restart policy to try again. On a machine whose network is briefly down that
   turns into a restart loop — measured on 2026-09-23: 22 restarts, 33 errors per 28-second run, no
   work done. The fix is to back off exponentially and stay alive; a client that disappears and
   reappears is worse for the customer than one that waits quietly.
2. **Windows is untested natively.** The image is Linux, so Windows works through WSL or Git Bash
   only. No PowerShell installer exists.
3. **The GUI lives outside the software.** Today the control panel is a page the launcher serves;
   the customer runs `freebird.py ui` to bring it up. See the plan below.

## Next version — the plan

Owner direction (2026-09-23): *"if we add all functionality and GUI to the software it makes sense
too"* — so the next build moves everything the estate currently does around the client **into** the
client, and this file is where that scope is kept so it is not lost between builds.

1. **The interface becomes part of the software, not a page beside it.** The panel's numbers, the
   ceiling controls, the machine list and the disclosure all open from the app itself; no separate
   command to remember, no browser step. One thing to install, one thing to open.
2. **All functionality in the same place:** which products this machine is entitled to and what each
   one costs with the contribution applied, the download/install for a second machine, revoking a
   machine, the contribution switch, and the disclosure text — each reachable from the app.
3. **The give-up defect above is fixed** (back off, stay alive, report "waiting for the network" in
   the interface rather than dying).
4. **A native Windows path**, so Windows does not require WSL.

## What it can and cannot touch

The container runs unprivileged with **all Linux capabilities dropped**, cannot gain new privileges,
and its root filesystem is **read-only**. The only writable places are the mounted config directory
and a size-capped scratch area. It opens no ports — it only ever dials out to pull work, so nothing
can connect in to you.

## Where it is

- `freebird_client.py` — the client
- `freebird.py` — the launcher
- `freebird_ui.py` — the local control panel
- `install.sh` — the one-line installer
- `Dockerfile` — the image
- published multi-arch at `ghcr.io/shivtcdfinance/freebird-client`
- downloaded from your own console: `freebird.shivelinc.com/download/freebird.sh`
  (the file arrives with your key already in it — treat it like a password)

## If it will not start

- **"cannot resolve host" while other sites work:** your machine has a stale *negative* answer
  cached for the door's hostname (a query that failed once gets remembered). `dig <host>` will
  return an address while `curl <host>` says it cannot resolve — that mismatch is the tell. The
  container is given its own resolvers so it does not inherit this, but the host-side check needs a
  DNS cache flush.
- **Nothing arriving, no errors:** check the ceiling is above 0 and `contribute` is true, then
  `freebird.py status` — it prints when the file was written and which door is active, so a stale
  reading cannot pass for a current one.
