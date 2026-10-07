#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
umask 077
action="${1:-start}"
case "$action" in start|stop|backup) ;; *) echo 'Usage: bash run.sh start|stop|backup' >&2; exit 1;; esac
command -v docker >/dev/null || { echo 'Install and start Rancher Desktop (dockerd/Moby) or Docker Engine first: https://rancherdesktop.io/' >&2; exit 1; }
endpoint="${DOCKER_HOST:-}"
if [[ -z "$endpoint" ]]; then endpoint="$(docker context inspect --format '{{.Endpoints.docker.Host}}')"; fi
case "$endpoint" in unix://*) ;; *) echo 'Use a local Linux container engine, not a remote Docker endpoint.' >&2; exit 1;; esac
unset DOCKER_CONTEXT DOCKER_HOST
docker_command=(docker --host "$endpoint")
[[ "$("${docker_command[@]}" info --format '{{.OSType}}')" == linux ]] || { echo 'Select dockerd (Moby) / Linux containers first.' >&2; exit 1; }
"${docker_command[@]}" compose version
if [[ ! -f .committee.env ]]; then
    [[ "$action" == start ]] || { echo 'Start the demo first.' >&2; exit 1; }
    retained_volumes="$("${docker_command[@]}" volume ls -q --filter label=com.docker.compose.project=wash-committee)"
    retained_containers="$("${docker_command[@]}" ps -aq --filter label=com.docker.compose.project=wash-committee)"
    if [[ -n "$retained_volumes" || -n "$retained_containers" ]]; then
        echo 'Existing committee data has no local settings file. Restore the original .committee.env from your existing copy. No containers or data were changed.' >&2
        exit 1
    fi
    command -v openssl >/dev/null || { echo 'OpenSSL is required to generate private local settings.' >&2; exit 1; }
    secret="$(openssl rand -hex 32)"
    (set -o noclobber; printf 'WASH_COMMITTEE_DB_PASSWORD=%s\nWASH_COMMITTEE_PORT=8765\n' "$secret" > .committee.env)
fi
compose=("${docker_command[@]}" compose --project-name wash-committee --env-file "$PWD/.committee.env" -f "$PWD/compose.yaml")
case "$action" in
start)
    if command -v sha256sum >/dev/null; then sha256sum --check images.sha256;
    else expected="$(cut -d ' ' -f 1 images.sha256)"; actual="$(shasum -a 256 images.tar)"; [[ "${actual%% *}" == "$expected" ]] || { echo 'Package checksum failed.' >&2; exit 1; }; fi
    present="$("${docker_command[@]}" image ls --no-trunc --format '{{.Repository}}:{{.Tag}} {{.ID}}')"
    missing=0
    while IFS= read -r image; do [[ $'\n'"$present"$'\n' == *$'\n'"$image"$'\n'* ]] || missing=1; done < images.ids
    if [[ "$missing" == 1 ]]; then "${docker_command[@]}" load --input images.tar; fi
    present="$("${docker_command[@]}" image ls --no-trunc --format '{{.Repository}}:{{.Tag}} {{.ID}}')"
    while IFS= read -r image; do [[ $'\n'"$present"$'\n' == *$'\n'"$image"$'\n'* ]] || { echo 'Expected application image missing.' >&2; exit 1; }; done < images.ids
    "${compose[@]}" up -d --wait --wait-timeout 180
    port="$(sed -n 's/^WASH_COMMITTEE_PORT=\([0-9]*\)$/\1/p' .committee.env)"
    url="http://127.0.0.1:$port/committee/"
    printf 'Committee guide: %s\nStop preserves all test data.\n' "$url"
    if [[ "${WASH_COMMITTEE_NO_BROWSER:-0}" != 1 ]]; then
        if command -v xdg-open >/dev/null; then xdg-open "$url" >/dev/null 2>&1 || true;
        elif command -v open >/dev/null; then open "$url"; fi
    fi
    ;;
stop) "${compose[@]}" stop; echo 'Stopped. Test data preserved.' ;;
backup)
    "${compose[@]}" exec -T app python scripts/backup.py /committee/backups
    folder="backups-$(date -u +%Y%m%d-%H%M%S)"
    mkdir -m 700 "$folder"
    "${compose[@]}" cp 'app:/committee/backups/.' "$PWD/$folder"
    echo "Backup copied to $folder"
    ;;
esac
