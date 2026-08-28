#!/usr/bin/env bash
set -Eeuo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

if ! command -v docker >/dev/null 2>&1; then
    echo "Docker is not installed or is not on PATH." >&2
    exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
    echo "Docker Compose v2 is required (the 'docker compose' command)." >&2
    exit 1
fi

mkdir -p sounds

if [[ ! -f .env ]]; then
    echo "First-time SoundBoard setup"
    read -r -s -p "Discord bot token: " discord_token
    echo
    if [[ -z "$discord_token" ]]; then
        echo "A Discord bot token is required." >&2
        exit 1
    fi

    if command -v openssl >/dev/null 2>&1; then
        web_token="$(openssl rand -hex 32)"
    else
        web_token="$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')"
    fi

    {
        printf 'DISCORD_TOKEN=%s\n' "$discord_token"
        printf 'AUDIO_DIRECTORIES=/media/sounds\n'
        printf 'WEB_API_TOKEN=%s\n' "$web_token"
        printf 'SOUNDBOARD_PORT=8000\n'
    } > .env
    chmod 600 .env
    echo "Created .env with a random web API token."
fi

discord_token="$(sed -n 's/^DISCORD_TOKEN=//p' .env | tail -n 1)"
if [[ -z "$discord_token" || "$discord_token" == "replace-me" ]]; then
    echo "Set DISCORD_TOKEN in .env, then run this script again." >&2
    exit 1
fi

docker compose config --quiet
docker compose up --detach --build --remove-orphans
docker compose ps

published_port="$(sed -n 's/^SOUNDBOARD_PORT=//p' .env | tail -n 1)"
published_port="${published_port:-8000}"
server_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
server_ip="${server_ip:-SERVER-IP}"

echo
echo "SoundBoard is starting at http://${server_ip}:${published_port}"
echo "Run 'docker compose logs -f soundboard' to watch startup."
echo "The web API token is stored in .env (run 'grep WEB_API_TOKEN .env')."
