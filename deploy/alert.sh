#!/bin/bash
# The one path by which a failure of the unattended host reaches a human: one JSON line
# {"world":"funded","event":<code>,"reason":<code>} POSTed to FACTORY_WEBHOOK_URL.
#
#   alert.sh                      ExecStopPost of factorylab.service: classify the stop
#                                 from systemd's EXIT_CODE, EXIT_STATUS and SERVICE_RESULT
#   alert.sh --event <event> [reason]
#                                 factorylab-alert@<event>.service (OnFailure= of the
#                                 backup, wake and health units, the heartbeat timer) and
#                                 deploy/witness_liveness.py
#   alert.sh --test               a synthetic alert, to prove the path end to end
#   alert.sh --ping               one GET to FACTORY_HEARTBEAT_URL, a dead-man's switch
#                                 (deploy/witness_liveness.py, hourly, only when healthy);
#                                 does nothing when that optional URL is unset
#   alert.sh --test-ping          the same ping, by a person: fails when unset
#
# Event and reason are closed vocabularies; no exception text, summary, path, balance,
# key or model content is ever sent. Chapter II §III: the evaluation of the host runs
# online and continuously; §IV.c: the apparatus watching the factory may not be slower
# than it, so a silent failure is the one failure this script exists to prevent. Nothing
# here enters the world: the receiver is the owner, never a seat.
#
# The body is short enough to read as a push notification (ntfy shows it as the text).
# Delivery is retried with backoff (5 attempts, 10 s each, 2/4/8/16 s apart: at most
# 80 s, inside factorylab.service's TimeoutStopSec=90s). Exit 0 only when delivered.
# The dead-man's switch is the one signal that survives the host itself dying: the
# service it pings alerts the owner when the hourly pings stop.
set -eu
world=funded
# The health record deploy/witness_liveness.py writes each hour: "<status> [reasons...]".
health=/srv/factorylab/runs/funded.health

event=""
reason=none
label=""
case "${1:-}" in
    "")
        # systemd supplies EXIT_CODE, EXIT_STATUS and SERVICE_RESULT.
        case "${EXIT_CODE:-}:${EXIT_STATUS:-}" in
            exited:3) event=terminated ;;
            exited:0) exit 0 ;;
            killed:TERM|killed:INT)
                # An ordered stop (systemctl stop, a reboot) is reported, never hidden.
                if [[ ${SERVICE_RESULT:-} == success ]]; then
                    event=stopped
                fi
                ;;
        esac
        if [[ -z $event ]]; then
            mode=""
            [[ -f /run/factorylab/mode ]] && read -r mode < /run/factorylab/mode || true
            case "$mode" in
                resume) event=failed_resume ;;
                run) event=failed_run ;;  # the initial run crashing is a failure too
                *) event=failed_start ;;
            esac
        fi
        # One code from factorylab's closed reason vocabulary, or systemd's own result.
        if [[ -f /run/factorylab/reason ]]; then
            line=""
            read -r line < /run/factorylab/reason || true
            [[ $line =~ ^[a-z_]{1,40}$ ]] && reason=$line
        fi
        if [[ $reason == none && $event != stopped ]]; then
            case "${SERVICE_RESULT:-}" in
                exit-code|signal|core-dump|timeout|watchdog|oom-kill|resources|start-limit-hit)
                    reason=${SERVICE_RESULT//-/_} ;;
            esac
        fi
        ;;
    --test)
        event=test
        label="alert test"
        ;;
    --ping)
        event=ping
        ;;
    --test-ping)
        event=ping
        label="ping test"
        ;;
    --event)
        event=${2:-}
        case "$event" in
            unhealthy|unknown|backup_failed|wake_failed|health_failed|heartbeat) ;;
            test) label="alert test" ;;
            *) exit 2 ;;
        esac
        candidate=${3:-none}
        [[ $candidate =~ ^[a-z_]{1,40}$ ]] && reason=$candidate
        if [[ $event == heartbeat ]]; then
            # A heartbeat says what the last hourly check found, and never "healthy"
            # from a record that check did not refresh: stale or missing is unknown.
            reason=unknown
            if [[ -f $health && -n $(find "$health" -mmin -150 2>/dev/null) ]]; then
                status=""
                read -r status _ < "$health" || true
                case "$status" in
                    healthy|dormant|terminated|unhealthy|unknown) reason=$status ;;
                esac
            fi
        fi
        ;;
    *)
        exit 2
        ;;
esac

say() {
    # Only for a test run by a person at a terminal; fixed words, never the URL.
    [[ -n $label ]] && printf '%s: %s\n' "$label" "$1" || true
}
# One request to the URL named by $1, retried with backoff; the remaining arguments are
# curl's (the body). The URL travels via stdin configuration, never the process argument
# list or logs. HTTPS only, and no character with curl-config syntax significance.
deliver() {
    local url=$1 delay=2 attempt=1
    shift
    if [[ -z $url ]]; then
        say "not configured"
        return 1
    fi
    case "$url" in
        https://*) ;;
        *) say "refused"; return 1 ;;
    esac
    if [[ "$url" == *'"'* || "$url" == *'\'* || "$url" == *$'\n'* || "$url" == *$'\r'* ]]; then
        say "refused"
        return 1
    fi
    while :; do
        if printf 'url = "%s"\n' "$url" |
            curl --config - --silent --fail --max-time 10 --connect-timeout 5 \
                --proto '=https' "$@" --output /dev/null 2>/dev/null; then
            say "delivered"
            return 0
        fi
        if [[ $attempt -ge 5 ]]; then
            say "not delivered"
            return 1
        fi
        sleep "$delay"
        delay=$((delay * 2))
        attempt=$((attempt + 1))
    done
}
if [[ $event == ping ]]; then
    # The dead-man's switch: a bare GET that says only "still here". Optional, so an
    # unset URL is not an error, except when a person is testing it.
    [[ -n ${FACTORY_HEARTBEAT_URL:-} || -n $label ]] || exit 0
    deliver "${FACTORY_HEARTBEAT_URL:-}" --get
    exit
fi
body="$(printf '{"world":"%s","event":"%s","reason":"%s"}' "$world" "$event" "$reason")"$'\n'
deliver "${FACTORY_WEBHOOK_URL:-}" --header 'Content-Type: application/json' \
    --data-binary "$body"
