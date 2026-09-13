#!/bin/bash
# systemd supplies EXIT_CODE, EXIT_STATUS and SERVICE_RESULT; no exception text escapes.
set -eu
case "${EXIT_CODE:-}:${EXIT_STATUS:-}" in
    exited:3) event=terminated ;;
    exited:0) exit 0 ;;
    *)
        [[ -f /run/factorylab/mode ]] || exit 0
        [[ $(cat /run/factorylab/mode) == resume ]] || exit 0
        event=failed_resume
        ;;
esac
# One code from factorylab's closed reason vocabulary, or none. Never free text.
reason=none
if [[ -f /run/factorylab/reason ]]; then
    read -r line < /run/factorylab/reason || line=""
    [[ $line =~ ^[a-z_]{1,40}$ ]] && reason=$line
fi
[[ -n ${FACTORY_WEBHOOK_URL:-} ]] || exit 1
# URL travels via stdin configuration, never the process argument list or logs.
# Permit HTTPS only and reject characters with curl-config syntax significance.
case "$FACTORY_WEBHOOK_URL" in
    https://*) ;;
    *) exit 1 ;;
esac
if [[ "$FACTORY_WEBHOOK_URL" == *'"'* || "$FACTORY_WEBHOOK_URL" == *'\'* ||
      "$FACTORY_WEBHOOK_URL" == *$'\n'* || "$FACTORY_WEBHOOK_URL" == *$'\r'* ]]; then
    exit 1
fi
printf 'url = "%s"\n' "$FACTORY_WEBHOOK_URL" |
    curl --config - --silent --fail --max-time 20 --proto '=https' \
        --header 'Content-Type: application/json' \
        --data-binary "$(printf '{"world":"funded","event":"%s","reason":"%s"}' \
            "$event" "$reason")"$'\n' \
        --output /dev/null 2>/dev/null
