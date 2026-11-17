#!/bin/bash
echo "start $(date -u +%FT%TZ)"
docker run --rm --name s4zulip --platform linux/amd64 -v /Users/kiyo/ds-mirrors/zulip__zulip.git:/mirror:ro -v <scratch>/s4_zulip/out:/out -v <scratch>/s4_zulip/inner.sh:/inner.sh:ro zulip/ci:noble bash /inner.sh
echo "container exit=$?"
echo "end $(date -u +%FT%TZ)"
