#!/bin/bash
if [ -t 1 ] && [ -f /src/dim-testsuite/docker/motd.txt ]; then
    cat /src/dim-testsuite/docker/motd.txt
fi
exec /bin/bash --rcfile /src/dim-testsuite/docker/shell-profile.sh -i
