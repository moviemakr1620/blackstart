# Deploy Blackstart as a Proxmox LXC

Keep this container **up during a shed**. It is the brain. Same idea as PeaNUT: dedicated LXC on the UPS, marked **critical** in Blackstart, never listed in a load-shed playbook.

You do **not** pass the UPS USB into this LXC. PeaNUT already has the UPS. Blackstart talks to PeaNUT (HTTP `:8080`) or NUT (TCP `:3493`) on the LAN.

The LXC only needs **Python 3**. The web UI is already built and shipped in `backend/static/`.

---

## 1. Create the LXC (Proxmox UI)

1. Datacenter → your node → **Create CT**
2. **General**
   - CT ID: any free id (example `120`)
   - Hostname: `blackstart`
   - Password: set a root password
   - Unprivileged: **on**
3. **Template**: Debian 12 (download one under local → CT Templates if the list is empty)
4. **Disks**: 8 GB
5. **CPU**: 2 cores
6. **Memory**: 1024 MB (512 works; 1024 is comfortable)
7. **Network**
   - Bridge: `vmbr0` (same LAN as PeaNUT)
   - IPv4: DHCP, or a static address you will remember
8. **DNS**: your LAN DNS / gateway
9. Confirm → **Start after created**

Then:

- Options → **Start at boot**: yes
- Options → **Features** → enable **Nesting** (optional)

You do **not** need USB passthrough on this CT.

Shell from the Proxmox UI (**>_ Console**) or:

```bash
pct enter 120
```

---

## 2. Create the LXC (CLI, optional)

On the Proxmox **host**, edit the variables at the top of `deploy/create-lxc.sh`, then:

```bash
pveam update
pveam available --section system | grep debian-12
pveam download local debian-12-standard_12.12-1_amd64.tar.zst   # name may differ

# copy create-lxc.sh onto the host, or:
curl -fsSL https://raw.githubusercontent.com/moviemakr1620/blackstart/main/deploy/create-lxc.sh -o create-lxc.sh
chmod +x create-lxc.sh
# edit CTID / STORAGE / TEMPLATE / IP first
./create-lxc.sh
pct enter 120
```

---

## 3. Inside the LXC — install Blackstart

```bash
apt update
apt install -y git ca-certificates

git clone https://github.com/moviemakr1620/blackstart.git /opt/blackstart
cd /opt/blackstart
bash deploy/install-lxc.sh
```

The installer:

- creates a `blackstart` system user
- installs Python deps in a venv
- writes `/etc/blackstart.env` with a random `BLACKSTART_SECRET_KEY`
- enables systemd so it survives reboots

Open **http://\<lxc-ip\>:8088**

Login: `admin` / `blackstart` — change the password in Settings immediately.

Useful commands:

```bash
systemctl status blackstart
journalctl -u blackstart -f

# after a git pull
cd /opt/blackstart && git pull
runuser -u blackstart -- /opt/blackstart/backend/.venv/bin/pip install -r backend/requirements.txt
systemctl restart blackstart
```

Edit `/etc/blackstart.env` to set `BLACKSTART_PEANUT_URL=http://<peanut-ip>:8080`, then `systemctl restart blackstart`. You can also add the UPS in the UI.

Tarball instead of git (air-gap):

```bash
# on your laptop
tar czf blackstart.tar.gz --exclude blackstart/backend/.venv --exclude blackstart/frontend/node_modules --exclude blackstart/.git blackstart
scp blackstart.tar.gz root@PROXMOX:/root/
pct push 120 /root/blackstart.tar.gz /root/blackstart.tar.gz
pct enter 120
mkdir -p /opt && tar xzf /root/blackstart.tar.gz -C /opt
cd /opt/blackstart && bash deploy/install-lxc.sh
```

---

## 4. First-run in the UI

1. **Settings → UPS sources** — add PeaNUT (`http://<peanut-lxc>:8080`), **Test**, enable it, disable the simulator
2. **Inventory** — add Proxmox / Synology / Docker, Discover, mark **peanut** and **blackstart** critical
3. Leave **Enable live actions** off
4. Dashboard → **Pull the plug** on the simulator and watch History
5. Only then flip live actions

PeaNUT install: [peanut.md](peanut.md). Proxmox API token: [proxmox-token.md](proxmox-token.md).

---

## 5. Resources and safety

| | |
| --- | --- |
| Stay on the UPS | Yes — same as PeaNUT |
| USB passthrough | **No** (that belongs on the PeaNUT CT) |
| Privileged CT | No |
| Nesting | Optional |
| Start at boot | Yes |
| Shed this CT | **Never** |

From this LXC you need reachability to:

- PeaNUT `:8080` or NUT `:3493`
- Proxmox `:8006` / PBS `:8007`
- Synology `:5001`
- Docker / SSH as needed
- UDP `9` if you use Wake-on-LAN

Do not publish `:8088` to the internet.
