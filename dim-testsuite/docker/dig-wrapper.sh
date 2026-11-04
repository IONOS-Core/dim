#!/bin/bash
has_server=0
for arg in "$@"; do
    if [[ "$arg" == @* ]]; then
        has_server=1
        break
    fi
done

if [ "$has_server" -eq 1 ]; then
    exec /usr/bin/dig "$@"
else
    exec /usr/bin/dig @127.1.1.1 "$@"
fi
