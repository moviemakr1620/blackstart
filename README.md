# Blackstart

A control-room web UI that watches your **EcoFlow Delta 3 Plus** (via the UPS USB port → NUT → PeaNUT) and **orchestrates Proxmox, Synology, Docker VMs, and compose stacks** when the grid dies — then brings them back in the order you choose once the battery recovers.

You already have PeaNUT in an LXC talking to the EcoFlow. Blackstart sits next to it. It does **not** replace PeaNUT. PeaNUT is the dashboard for the UPS; Blackstart is the playbook engine.

```
EcoFlow Delta 3 Plus  --USB HID-->  NUT (upsd)  -->  PeaNUT
                                              \-->  Blackstart  -->  Proxmox / Synology / Docker / SSH / WOL
```

## What you can change

Every playbook is a list of **when → do what** steps you edit in the UI:

| When | Example |
| --- | --- |
| Grid lost (`on_battery`) | Notify, arm the shed |
| Battery ≤ 70% for 20s | Stop the `media` compose stack |
| Battery ≤ 45% | ACPI-shutdown a Proxmox VM |
| Runtime ≤ 15 min | Shutdown Synology |
| Grid back + battery ≥ 55% for 90s | WOL the NAS, then start everything Blackstart stopped |

Restore can either run **explicit start steps** or a single `restore_auto` action that reverses whatever this outage actually stopped (so a VM that was already off stays off).

**Live actions are off by default.** Until you flip that switch in Settings, playbooks evaluate and log but never touch a host. Use **Dry run** and the built-in **EcoFlow simulator** first.

## Deploy on Proxmox (LXC)

Step-by-step: push this repo to GitHub (or scp a tarball), create a Debian 12 CT, run the installer.

**[deploy/lxc.md](deploy/lxc.md)** — Git, Create CT, `install-lxc.sh`, systemd, first login.

## Quick start (this Mac / Docker)

```bash
cd blackstart
cp .env.example .env          # set BLACKSTART_SECRET_KEY
docker compose up -d --build
# open http://YOUR-HOST:8088
# login admin / blackstart
```

Or run the two halves locally:

```bash
# terminal 1
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=. uvicorn app.main:app --reload --port 8088

# terminal 2
cd frontend
npm install
npm run dev
# http://127.0.0.1:5173
```

Change the password in **Settings** immediately.

## Wire it to the Delta 3 Plus / PeaNUT

1. EcoFlow **UPS USB port** is already passed into the PeaNUT/NUT LXC. Leave that alone.
2. In Blackstart → **Settings → UPS sources**:
   - **PeaNUT**: `http://<peanut-lxc-ip>:8080`, device name from PeaNUT (often `ecoflow`). Add the PeaNUT username/password if you enabled auth.
   - **or NUT direct**: host = the LXC IP, port `3493`, same device name, plus the `upsd` user if required.
3. Click **Test**, then **Use this**. Disable the simulator once the real feed looks right.

`upsd` must listen on the LAN, not just localhost. In the NUT LXC, `/etc/nut/upsd.conf` typically needs:

```
LISTEN 0.0.0.0 3493
```

and `upsd.users` needs a user Blackstart can read as.

## Add hosts

**Inventory → Add host**

| Kind | Address | Credentials | Discover |
| --- | --- | --- | --- |
| **Proxmox** | `192.168.x.x` port `8006` | API token `user@pam!tokenid` + secret. Privileges: `VM.Audit`, `VM.PowerMgmt`, `Sys.Audit`. Optional `Sys.PowerMgmt` only if you will shut the node itself. | Imports QEMU + LXC |
| **Synology** | DSM IP port `5001` | A dedicated DSM user that can shut down the NAS (and Docker if you want Container Manager control). | Imports NAS + containers |
| **Docker** | `unix:///var/run/docker.sock` or `tcp://host:2375` | None for a local socket. Mount the socket into the Blackstart container if you use Compose. | Imports containers + compose **stacks** |
| **SSH** | any Linux box | user/password or private key | Add custom targets + `ssh` actions |

Mark **peanut** and **blackstart** (and Pi-hole / OPNsense if they live here) as **Critical**. Settings → “Never stop targets marked critical” is on by default.

For Synology restore, enable **Wake on LAN** in DSM and put the NAS MAC on the target (`extra.mac`). The default restore playbook sends a magic packet before starting workloads.

## Playbooks

Open **Playbooks**.

- **Load shed** fires while `on_battery`.
- **Restore** fires after the grid returns.

Each step:

1. **When** — `on_battery`, `battery_lte`, `battery_gte`, `runtime_lte`, `on_online`, or a compound “all of…” rule. **Hold seconds** is how long the condition must stay true (anti-flap).
2. **Do** — notify, wait, WOL, Proxmox start/shutdown/stop, Docker container or **stack**, Synology shutdown, SSH, webhook, or `restore_auto`.
3. **Target** — pick from Inventory.
4. Delay, timeout, continue-on-error.

**Dry run** executes the playbook against the current UPS state but only logs. **Run live** actually calls the APIs — only if live actions are enabled.

A sensible first ladder (the seed playbook):

1. Grid lost → notify  
2. ≤ 70% → stop media stack  
3. ≤ 45% → shutdown media VMs  
4. ≤ 25% → shutdown Synology  
5. ≤ 15% → notify “critically low” (leave peanut + blackstart up)  
6. Online + ≥ 40% → WOL Synology  
7. Wait until DSM port 5001 answers (`wait_tcp`)  
8. Online + ≥ 55% → `restore_auto`

Edit the numbers. Your lab, your runtime.

## Simulator

Until PeaNUT is pointed in, the engine uses a **Delta 3 Plus–class simulator** (1024 Wh). On the dashboard:

- **Pull the plug** — status becomes `OB`, charge drains with load
- **Restore grid** — `OL CHRG`, charge climbs
- Jump to 80 / 40 / 15% to fire the next ladder rung

Use this to watch playbooks fire in **History** without touching real machines.

## Safety model

- Live actions default **off**
- Engine can be **paused**
- Critical targets cannot be stopped
- Each threshold must hold for N seconds
- Each step fires **once per outage episode**
- Restore can start **only what this episode stopped**
- Every action is written to the run log

Do not give the Proxmox token more than it needs. Do not let Blackstart shut down the node that runs NUT + Blackstart unless you like walking to the rack.

## Where to run it

Best: a small **privileged-enough LXC or VM that stays up** — same idea as PeaNUT. It must survive the shed. Put it on the EcoFlow, mark it critical, and never add it to a shed playbook.

Docker Compose on a VM also works. If you need Wake-on-LAN, `network_mode: host` (Linux) is the reliable option.

SQLite lives in `/data` (Compose volume `blackstart-data`). Back that volume up.

## API

Authenticated with `Authorization: Bearer <jwt>` after `POST /api/auth/login`.

Useful endpoints: `/api/ups/status`, `/api/policies`, `/api/hosts/{id}/discover`, `/api/policies/{id}/run`, `/api/ws`. Full schema at `/docs`.

## Stack

FastAPI + SQLAlchemy/SQLite · React/Vite/Tailwind · NUT TCP + PeaNUT HTTP · Proxmox API · DSM WebAPI · Docker Engine API · SSH · WOL · ntfy/Gotify/webhook.

Default login: `admin` / `blackstart`.
