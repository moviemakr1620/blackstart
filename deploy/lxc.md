# Deploy Blackstart as a Proxmox LXC

Keep this container **up during a shed**. It is the brain. Same idea as PeaNUT: dedicated LXC on the EcoFlow, marked **critical** in Blackstart, never listed in a load-shed playbook.

You do **not** need to pass the EcoFlow USB into this LXC. PeaNUT already has the UPS. Blackstart talks to PeaNUT (HTTP) or NUT (port 3493) over the LAN.

The LXC only needs **Python 3**. The web UI is already built and shipped in `backend/static/`.

---

## 0. Put the code on Git (from your Mac)

The project lives at `~/blackstart`. It is a git repo you can push to GitHub, then clone inside the LXC.

### A. Create a **private** GitHub repo

1. In a browser: [https://github.com/new](https://github.com/new)
2. Name: `blackstart`
3. **Private**
4. Do **not** add a README / .gitignore / license (the repo already has them)
5. Create repository

This Mac has no GitHub SSH key and no `gh` CLI, so use **HTTPS + a personal access token**.

6. GitHub → Settings → Developer settings → [Personal access tokens](https://github.com/settings/tokens)
   - Fine-grained token, only this repo, permission **Contents: Read and write**
   - Or a classic token with the `repo` scope
7. On the Mac:

```bash
cd ~/blackstart
git remote add origin https://github.com/YOUR_USER/blackstart.git
git branch -M main
git push -u origin main
# username: YOUR_USER
# password: paste the token, not your GitHub password
```

Later pulls on the LXC can use the same URL if the repo is private — either a **read-only token** or, simpler, make the repo private and use a deploy token / SSH key **on the LXC**.

### B. Skip GitHub — copy a tarball

If you just want it on Proxmox tonight:

```bash
cd ~
tar czf blackstart.tar.gz \
  --exclude blackstart/backend/.venv \
  --exclude blackstart/frontend/node_modules \
  --exclude blackstart/frontend/dist \
  --exclude '.git' \
  blackstart

scp blackstart.tar.gz root@PROXMOX_IP:/root/
```

On the Proxmox host, after the LXC exists:

```bash
pct push 120 /root/blackstart.tar.gz /root/blackstart.tar.gz
pct enter 120
# inside the CT:
mkdir -p /opt && tar xzf /root/blackstart.tar.gz -C /opt
# now /opt/blackstart exists
```

---

## 1. Create the LXC (Proxmox UI)

This is the path to use the first time.

1. Datacenter → your node → **Create CT**
2. **General**
   - CT ID: any free id (example `120`)
   - Hostname: `blackstart`
   - Password: set a root password
   - Unprivileged: **on**
3. **Template**: Debian 12 (download one under local → CT Templates if the list is empty)
4. **Disks**: 8 GB on `local-lvm` (or your usual storage)
5. **CPU**: 2 cores
6. **Memory**: 1024 MB (512 works; 1024 is comfortable)
7. **Network**
   - Bridge: `vmbr0` (same LAN as PeaNUT)
   - IPv4: DHCP, or a static address you will remember
8. **DNS**: your LAN DNS / gateway
9. Confirm → **Start after created**

Then:

- Options → **Start at boot**: yes  
- Options → **Features** → enable **Nesting** (harmless; only required if you later run Docker in here)

You do **not** need USB passthrough on this CT.

Give it a shell from the Proxmox UI (**>_ Console**) or:

```bash
pct enter 120
```

---

## 2. Create the LXC (CLI, optional)

On the Proxmox host, edit the variables at the top of `deploy/create-lxc.sh`, then:

```bash
# If you already pushed to git, grab just that script:
curl -fsSL https://raw.githubusercontent.com/YOUR_USER/blackstart/main/deploy/create-lxc.sh -o create-lxc.sh
# or copy it over with scp

# Download a Debian 12 template if needed:
pveam update
pveam available --section system | grep debian-12
pveam download local debian-12-standard_12.12-1_amd64.tar.zst

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

# --- if you pushed to GitHub ---
git clone https://github.com/YOUR_USER/blackstart.git /opt/blackstart

# --- if you used the tarball ---
# mkdir -p /opt && tar xzf /root/blackstart.tar.gz -C /opt

cd /opt/blackstart
# optional: set PeaNUT before first start
# nano deploy/blackstart.env.example   # then the installer copies it

bash deploy/install-lxc.sh
```

The installer:

- creates a `blackstart` system user
- installs Python deps in a venv
- writes `/etc/blackstart.env` with a random `BLACKSTART_SECRET_KEY`
- enables `systemd` so it survives reboots

Open **http://\<lxc-ip\>:8088**

Login: `admin` / `blackstart` — change the password in Settings immediately.

Useful commands:

```bash
systemctl status blackstart
journalctl -u blackstart -f
# after a git pull:
git config --global --add safe.directory /opt/blackstart
cd /opt/blackstart && git pull
runuser -u blackstart -- /opt/blackstart/backend/.venv/bin/pip install -r backend/requirements.txt
systemctl restart blackstart
```

Edit `/etc/blackstart.env` to set `BLACKSTART_PEANUT_URL=http://<peanut-ip>:8080`, then `systemctl restart blackstart`.

---

## 4. First-run in the UI

1. **Settings → UPS sources** — add PeaNUT (`http://<peanut-lxc>:8080`), **Test**, **Use this**
2. **Inventory** — add Proxmox / Synology / Docker, Discover, mark **peanut** and **blackstart** critical
3. Leave **Enable live actions** off
4. Dashboard → **Pull the plug** on the simulator (or wait for a real reading) and watch History
5. Only then flip live actions

---

## 5. Resources and safety

| | |
| --- | --- |
| Stay on the EcoFlow | Yes — same UPS as PeaNUT |
| USB passthrough | No |
| Privileged CT | No |
| Nesting | Optional |
| Start at boot | Yes |
| Shed this CT | **Never** |

Firewall / reachability from this LXC:

- PeaNUT `:8080` or NUT `:3493`
- Proxmox `:8006`
- Synology `:5001`
- Docker / SSH as needed
- UDP `9` if you use Wake-on-LAN

Do not publish `:8088` to the internet.
