#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
project_dir="$PWD"
mkdir -p .local
if [[ ! -x .venv/bin/python ]]; then python3 -m venv .venv; fi
.venv/bin/python -m pip install --cache-dir "$project_dir/.local/pip-cache" -r requirements.txt
pgbin="$project_dir/.local/postgres/usr/lib/postgresql/17/bin"
if [[ -z "${DATABASE_URL:-}" ]] && [[ ! -x "$pgbin/postgres" || ! -x "$pgbin/psql" ]]; then
    . /etc/os-release
    if [[ "$ID" != debian || "$VERSION_ID" != 13 ]]; then
        echo 'This local PostgreSQL setup is tested on Debian 13. Use a PostgreSQL DATABASE_URL on other platforms.' >&2
        exit 1
    fi
    mkdir -p .local/apt/lists/partial .local/apt/archives/partial .local/apt/packages .local/postgres
    cat > .local/apt/apt.conf <<'EOF'
Dir::Etc::parts "-";
Dir::Etc::main "/dev/null";
EOF
    cat > .local/apt/sources.list <<'EOF'
deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] https://deb.debian.org/debian trixie main
deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] https://security.debian.org/debian-security trixie-security main
EOF
    apt_options=(-o Dir::Etc::sourcelist="$project_dir/.local/apt/sources.list"
        -o Dir::Etc::sourceparts=- -o Dir::State::lists="$project_dir/.local/apt/lists"
        -o Dir::Cache::archives="$project_dir/.local/apt/archives")
    APT_CONFIG="$project_dir/.local/apt/apt.conf" /usr/bin/apt-get "${apt_options[@]}" update
    (
        cd .local/apt/packages
        APT_CONFIG="$project_dir/.local/apt/apt.conf" /usr/bin/apt-get "${apt_options[@]}" download postgresql-17 postgresql-client-17 libllvm19 libpq5
    )
    for package in .local/apt/packages/*.deb; do dpkg-deb -x "$package" .local/postgres; done
fi
if [[ -z "${DATABASE_URL:-}" ]]; then bash scripts/postgres.sh start; fi
.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py check
