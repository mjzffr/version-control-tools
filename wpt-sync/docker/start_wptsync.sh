#!/usr/bin/env bash

set -o errexit
set -o pipefail
set -o nounset
set -o xtrace

# USE the trap if you need to also do manual cleanup after the service is stopped,
#     or need to start multiple services in the one container
trap "echo TRAPed signal" HUP INT QUIT TERM

echo Args: $@

cp -v ${WPTSYNC_CONFIG:-/app/vct/wpt-sync/sync.ini} /app/workspace/sync.ini

if [ "$1" != "--test" ]; then
    eval "$(ssh-agent -s)"
    cp -v ${WPTSYNC_SSH_CONFIG:-/app/vct/wpt-sync/docker/ssh_config} /app/.ssh/config
    # Install ssh keys
    cp -v ${WPTSYNC_GH_SSH_KEY:-/app/workspace/ssh/id_github} /app/.ssh/id_github
    cp -v ${WPTSYNC_HGMO_SSH_KEY:-/app/workspace/ssh/id_hgmo} /app/.ssh/id_hgmo
    ssh-add /app/.ssh/id_github
    ssh-add /app/.ssh/id_hgmo
fi

env

if [ "$1" == "--shell" ]; then
    bash
elif [ "$1" == "--worker" ]; then
    service --status-all
    sudo service rabbitmq-server start
    sudo service rabbitmq-server status

    echo "Starting celerybeat"

    /app/venv/bin/celery beat --detach --app sync.worker \
                         --pidfile=${WPTSYNC_ROOT}/celerybeat.pid \
                         --logfile=${WPTSYNC_ROOT}/celerybeat.log --loglevel=DEBUG

    echo "Starting celery worker"

    /app/venv/bin/celery multi start syncworker1 -A sync.worker \
                         --concurrency=1 \
                         --pidfile=${WPTSYNC_ROOT}/%n.pid \
                         --logfile=${WPTSYNC_ROOT}/%n%I.log --loglevel=DEBUG

    echo "Starting pulse listener"

    exec /app/venv/bin/wptsync listen
elif [ "$1" == "--test" ]; then
    exec /app/venv/bin/wptsync test
else
    exec /app/venv/bin/wptsync "$@"
fi
