# Run Blackstart with Docker

The published image is **`ghcr.io/moviemakr1620/blackstart:latest`**. It serves the React UI from FastAPI on port **8088**. SQLite lives in `/data`.

Wake-on-LAN from a bridge network is unreliable. Use `network_mode: host` on Linux if you need WOL.

Do **not** publish `:8088` to the internet.

You must set **`BLACKSTART_SECRET_KEY`** (a long random string) and **`BLACKSTART_ADMIN_PASSWORD`**. Compose will refuse to start if they are missing. The first-run login is `BLACKSTART_ADMIN_USER` (default `admin`) and that password — change it in the UI after you sign in.

```bash
openssl rand -hex 32    # a good secret key
```

## Portainer / Dockhand (pull the image)

The repo does not have to live on the NAS. In Portainer: **Stacks → Add stack**, paste the compose below, replace the two `REPLACE_ME_…` values, and deploy. In Dockhand, create a stack from the same YAML.

```yaml
services:
  blackstart:
    image: ghcr.io/moviemakr1620/blackstart:latest
    container_name: blackstart
    restart: unless-stopped
    ports:
      - "8088:8088"
    environment:
      # Replace both of these before you deploy. Do not leave the placeholders.
      BLACKSTART_SECRET_KEY: REPLACE_ME_with_a_long_random_string
      BLACKSTART_ADMIN_USER: admin
      BLACKSTART_ADMIN_PASSWORD: REPLACE_ME_with_a_strong_password
      BLACKSTART_DATA_DIR: /data
      # Optional — also configurable later in Settings → UPS sources
      BLACKSTART_PEANUT_URL: http://192.168.1.50:8080
      BLACKSTART_NUT_HOST: ""
      BLACKSTART_NUT_DEVICE: ecoflow
    volumes:
      - blackstart-data:/data
      # Uncomment if this container should drive Docker on the same host:
      # - /var/run/docker.sock:/var/run/docker.sock
    # If you need Wake-on-LAN, uncomment this and remove the "ports:" section:
    # network_mode: host

volumes:
  blackstart-data:
```

Open `http://<host>:8088`. Updates: pull the new `:latest` image and recreate the container (Portainer: **Pull and redeploy**; Dockhand: pull / update).

If the first pull of `ghcr.io/moviemakr1620/blackstart` asks you to sign in, the package may still be private. On GitHub: **Packages** on the [blackstart repo](https://github.com/moviemakr1620/blackstart) → the `blackstart` package → **Package settings** → **Change visibility** → Public.

## docker compose (from this repo)

```bash
git clone https://github.com/moviemakr1620/blackstart.git
cd blackstart
cp .env.example .env
# set BLACKSTART_SECRET_KEY (openssl rand -hex 32) and BLACKSTART_ADMIN_PASSWORD

docker compose pull
docker compose up -d
```

Open `http://<host>:8088`, then change the admin password.

```bash
docker compose logs -f
docker compose pull && docker compose up -d
docker compose down
```

To build from source instead of pulling, uncomment `build: .` in `docker-compose.yml` and run `docker compose up -d --build`.

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
docker run -d --name blackstart --restart unless-stopped \
  -p 8088:8088 \
  -e BLACKSTART_SECRET_KEY="$(openssl rand -hex 32)" \
  -e BLACKSTART_ADMIN_USER=admin \
  -e BLACKSTART_ADMIN_PASSWORD='pick-a-strong-password' \
  -e BLACKSTART_PEANUT_URL=http://192.168.1.50:8080 \
  -v blackstart-data:/data \
  ghcr.io/moviemakr1620/blackstart:latest
```

Host network (Linux):

```bash
docker run -d --name blackstart --restart unless-stopped --network host \
  -e BLACKSTART_SECRET_KEY="$(openssl rand -hex 32)" \
  -e BLACKSTART_ADMIN_PASSWORD='pick-a-strong-password' \
  -e BLACKSTART_PEANUT_URL=http://192.168.1.50:8080 \
  -v blackstart-data:/data \
  ghcr.io/moviemakr1620/blackstart:latest
```

With Docker socket (to Discover compose stacks on the same host):

```bash
docker run -d --name blackstart --restart unless-stopped \
  -p 8088:8088 \
  -e BLACKSTART_SECRET_KEY="$(openssl rand -hex 32)" \
  -e BLACKSTART_ADMIN_PASSWORD='pick-a-strong-password' \
  -v blackstart-data:/data \
  -v /var/run/docker.sock:/var/run/docker.sock \
  ghcr.io/moviemakr1620/blackstart:latest
```

## Data and upgrades

| Path | What |
| --- | --- |
| volume `blackstart-data` → `/data` | SQLite (`blackstart.db`). Back this up. |
| env `BLACKSTART_SECRET_KEY` | JWT signing. Changing it logs everyone out. |
| env `BLACKSTART_ADMIN_PASSWORD` | Used only to create the first user if the database is empty. |
| env `BLACKSTART_LIVE_ACTIONS` | Keep `false` until you are ready. Also a switch in Settings. |

Upgrade (image pull, no rebuild):

```bash
docker compose pull && docker compose up -d
# or, if you started it with docker run:
docker pull ghcr.io/moviemakr1620/blackstart:latest
docker rm -f blackstart
# then the same docker run as above (same -v blackstart-data:/data)
```

The database is in the volume, not the image. Playbooks and inventory survive upgrades.

## Compose vs LXC

| | Docker | Proxmox LXC |
| --- | --- | --- |
| Best for | Lab already on Compose / Portainer | Always-on brain next to PeaNUT |
| USB UPS | Keep USB on a **separate** NUT/PeaNUT container | PeaNUT LXC with USB passthrough |
| WOL | `network_mode: host` | Works on the CT’s LAN |
| Updates | `docker compose pull && docker compose up -d` | `git pull` + `systemctl restart` (UI is already in `backend/static`) |
