# Deploy to the Docker server

This deployment is designed for a Linux Docker host, including Docker running
inside a Proxmox LXC. It builds one container locally on the server, keeps the
SQLite statistics database in a Docker volume, and reads audio from `./sounds`.

## 1. Copy the project to the server

Use either Git or an SFTP client to put the project in your server user's home
directory as `~/soundboard`. Do not copy `.env`, `.venv`, `.tools`, `data`, or
Python cache folders from the development machine.

For example, if the project is in Git:

```bash
ssh your-user@SERVER-IP
git clone YOUR-REPOSITORY-URL ~/soundboard
cd ~/soundboard
```

If it is not in Git, create `~/soundboard` with your SFTP client and upload
these items:

```text
app/
sounds/                  (including your audio files)
.dockerignore
.env.example
compose.yaml
deploy.sh
Dockerfile
requirements.txt
```

## 2. Deploy

On the Docker server:

```bash
cd ~/soundboard
bash deploy.sh
```

On its first run, the script securely asks for the Discord bot token, creates a
random web API token, builds the image, and starts the service. Open the URL it
prints, click the gear button, and paste the `WEB_API_TOKEN` from `.env`.

The site defaults to `http://SERVER-IP:8000`. If port 8000 is already used,
change `SOUNDBOARD_PORT=8000` in `.env` and rerun `bash deploy.sh`.

## Updating

Upload or pull the changed source files, then run the same command again:

```bash
cd ~/soundboard
bash deploy.sh
```

Compose rebuilds only changed layers and retains the database and `.env`.

## Audio and backups

Put audio files in `~/soundboard/sounds`; the container sees changes without a
restart when the web library refreshes. To use another host folder, add a
read-only mount to `compose.yaml` and its container path to `AUDIO_DIRECTORIES`
in `.env.

Back up the database with:

```bash
docker compose exec -T soundboard python -c "import sqlite3; source=sqlite3.connect('/data/stats.db'); backup=sqlite3.connect('/data/stats-backup.db'); source.backup(backup); backup.close(); source.close()"
docker compose cp soundboard:/data/stats-backup.db ./stats-backup.db
```

The second command creates `~/soundboard/stats-backup.db` on the server.

## Useful commands

```bash
docker compose ps
docker compose logs -f soundboard
docker compose restart soundboard
docker compose down
```

`docker compose down` stops the app but retains the database volume. Do not add
`--volumes` unless you intentionally want to delete the stored statistics.

## Proxmox LXC note

If Docker already runs successfully in the LXC, no Proxmox change is needed for
this app. The LXC needs network access to Discord, YouTube, and Python package
repositories during the initial build. Allow TCP port 8000 (or your selected
port) through the LXC/server firewall only from networks that should reach the
web UI. For public internet access, use an HTTPS reverse proxy with
authentication rather than exposing this port directly.
