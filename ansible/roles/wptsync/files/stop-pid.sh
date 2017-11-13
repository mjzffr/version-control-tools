#!/bin/sh -e

wait_pid () {
    pid=$1
    forever=1
    i=0
    while [ $forever -gt 0 ]; do
        kill -0 $pid 1>/dev/null 2>&1
        if [ $? -eq 1 ]; then
            echo "OK"
            forever=0
        else
            kill -TERM "$pid"
            i=$((i + 1))
            if [ $i -gt 60 ]; then
                echo "ERROR"
                echo "Timed out while stopping (30s)"
                forever=0
            else
                sleep 0.5
            fi
        fi
    done
}


if [ ! $# == 1 ]; then
  echo Usage: $0 path/to/pid/file
  exit
fi

pid_file=$1

if [ -f "$pid_file" ]; then
    wait_pid $(cat "$pid_file")
else
    echo "NO PID FOUND"
fi
