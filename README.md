# Freebird client

Runs your product's processing **on your own machine**, inside the ceiling you set. The spare
headroom above that ceiling helps the network; nothing beyond the number you configure is ever
used, and the limit is enforced by the operating system kernel — not by the software politely
agreeing to behave.

## Run it

```bash
python3 freebird.py            # start (and apply any config change)
python3 freebird.py status     # what it is using right now
python3 freebird.py logs       # live output
python3 freebird.py stop       # stop and remove
python3 freebird.py config     # show the ceiling it is running with
```

No Docker required specifically — it uses whichever container runtime you already have
(`docker`, `podman` or `nerdctl`), in that order. Force one with `FREEBIRD_RUNTIME=podman`.

## The ceiling

`~/.freebird/config.json`:

| setting | meaning |
|---|---|
| `cpu_percent_max` | percent of **this whole machine** (converted to cores for the container) |
| `ram_mb_max` | hard memory cap, swap pinned so it cannot be exceeded |
| `disk_mb_max` | size cap for the only writable scratch area |
| `contribute` | whether spare headroom helps the network (your ceiling still applies) |

Edit the file and run `freebird.py` again — it recreates the container and **prints what
changed**. Nothing is applied silently.

## What it can and cannot touch

The container runs unprivileged with **all Linux capabilities dropped**, cannot gain new
privileges, and its root filesystem is **read-only**. The only writable places are the mounted
config directory and a size-capped scratch area. It opens no ports — it only ever dials out to
pull work, so nothing can connect in to you.

## Where it is

- `freebird_client.py` — the client
- `freebird.py` — the launcher
- `Dockerfile` — the image
- published multi-arch at `ghcr.io/shivtcdfinance/freebird-client`
