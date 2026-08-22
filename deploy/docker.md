# Run Blackstart with Docker

The image builds the React UI and serves it from FastAPI on port **8088**. SQLite lives in `/data`.

Wake-on-LAN from a bridge network is unreliable. Use `network_mode: host` on Linux if you need WOL.

Do **not** publish `:8088` to the internet.

## docker compose (from this repo)

```bash
git clone https://github.com/moviemakr1620/blackstart.git
cd blackstart
cp .env.example .env
# set BLACKSTART_SECRET_KEY to a long random string
# optional: BLACKSTART_PEANUT_URL=http://192.168.1.50:8080

docker compose up -d --build
```

Open `http://<host>:8088` — login `admin` / `blackstart`, then change the password.

```bash
docker compose logs -f
docker compose pull   # after you rebuild / retag
docker compose down
```

Uncomment in `docker-compose.yml` if this container should talk to Docker on the same machine:

```yaml
volumes:
  - /var/run/docker.sock:/var/run/docker.sock
```

Host networking (Linux, recommended for WOL + easy LAN access to PeaNUT):

```yaml
network_mode: host
# then drop the "ports:" section — the app already binds 8088
```

## docker run

```bash
git clone https://github.com/moviemakr1620/blackstart.git
cd blackstart
docker build -t blackstart .

docker run -d --name blackstart --restart unless-stopped \
  -p 8088:8088 \
  -e BLACKSTART_SECRET_KEY="$(openssl rand -hex 32)" \
  -e BLACKSTART_ADMIN_USER=admin \
  -e BLACKSTART_ADMIN_PASSWORD=blackstart \
  -e BLACKSTART_PEANUT_URL=http://192.168.1.50:8080 \
  -v blackstart-data:/data \
  blackstart
```

Host network (Linux):

```bash
docker run -d --name blackstart --restart unless-stopped --network host \
  -e BLACKSTART_SECRET_KEY="$(openssl rand -hex 32)" \
  -e BLACKSTART_PEANUT_URL=http://192.168.1.50:8080 \
  -v blackstart-data:/data \
  blackstart
```

With Docker socket (to Discover compose stacks on the same host):

```bash
docker run -d --name blackstart --restart unless-stopped \
  -p 8088:8088 \
  -e BLACKSTART_SECRET_KEY="$(openssl rand -hex 32)" \
  -v blackstart-data:/data \
  -v /var/run/docker.sock:/var/run/docker.sock \
  blackstart
```

## Data and upgrades

| Path | What |
| --- | --- |
| volume `blackstart-data` → `/data` | SQLite (`blackstart.db`). Back this up. |
| env `BLACKSTART_SECRET_KEY` | JWT signing. Changing it logs everyone out. |
| env `BLACKSTART_LIVE_ACTIONS` | Keep `false` until you are ready. Also a switch in Settings. |

Upgrade:

```bash
cd blackstart
git pull
docker compose up -d --build
# or: docker build -t blackstart . && docker rm -f blackstart && docker run ... (same -v)
```

The database is in the volume, not the image. Playbooks and inventory survive rebuilds.

## Compose vs LXC

| | Docker | Proxmox LXC |
| --- | --- | --- |
| Best for | Lab already on Compose | Always-on brain next to PeaNUT |
| USB UPS | Keep USB on a **separate** NUT/PeaNUT container | PeaNUT LXC with USB passthrough |
| WOL | `network_mode: host` | Works on the CT’s LAN |
| Updates | `git pull` + rebuild | `git pull` + `systemctl restart` (UI is already in `backend/static`) |
