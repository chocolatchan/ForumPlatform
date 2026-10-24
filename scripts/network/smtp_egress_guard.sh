#!/bin/sh
# Per-container outbound SMTP guard.
#
# Installs narrow iptables/ip6tables OUTPUT rules inside the container's own
# network namespace, then drops CAP_NET_ADMIN before executing the application.
# No host-wide firewall configuration is changed.
#
# Protected environments: development, test
# Unprotected environments: staging, production (guard is skipped)
#
# The allowed SMTP destination is intentionally independent of SMTP_HOST /
# SMTP_PORT so an application configuration change cannot widen the allowlist.
set -eu

SMTP_GUARD_ALLOWED_HOST="${SMTP_GUARD_ALLOWED_HOST:-mailpit}"
SMTP_GUARD_ALLOWED_PORT="${SMTP_GUARD_ALLOWED_PORT:-1025}"
SMTP_GUARD_PORTS="${SMTP_GUARD_PORTS:-25 465 587 1025 2525}"
SMTP_GUARD_CHAIN="SMTP_EGRESS_GUARD"

log() {
    printf '%s\n' "smtp-egress-guard: $*" >&2
}

if [ "$#" -eq 0 ]; then
    log "refusing to start without an application command"
    exit 64
fi

drop_capability_and_exec() {
    # The rules persist after the capability is dropped. This prevents the
    # application process from modifying or removing its own egress guard.
    exec setpriv \
        --bounding-set=-net_admin \
        --inh-caps=-net_admin \
        --ambient-caps=-net_admin \
        -- "$@"
}

ENVIRONMENT="${ENVIRONMENT:-development}"

case "$ENVIRONMENT" in
    development|test)
        ;;
    staging|production)
        log "environment=$ENVIRONMENT is not protected; skipping SMTP egress rules"
        drop_capability_and_exec "$@"
        ;;
    *)
        log "unsupported ENVIRONMENT='$ENVIRONMENT'; refusing to guess a network policy"
        exit 78
        ;;
esac

if ! command -v iptables >/dev/null 2>&1; then
    log "iptables is not available; refusing to start unprotected"
    exit 69
fi

resolve_ipv4() {
    getent ahostsv4 "$1" 2>/dev/null | awk '{print $1}' | sort -u
}

resolve_ipv6() {
    getent ahostsv6 "$1" 2>/dev/null | awk '{print $1}' | sort -u
}

ALLOWED_IPV4="$(resolve_ipv4 "$SMTP_GUARD_ALLOWED_HOST" || true)"
ALLOWED_IPV6="$(resolve_ipv6 "$SMTP_GUARD_ALLOWED_HOST" || true)"

if [ -z "$ALLOWED_IPV4" ] && [ -z "$ALLOWED_IPV6" ]; then
    log "could not resolve allowed SMTP host '$SMTP_GUARD_ALLOWED_HOST'; refusing to start"
    exit 69
fi

install_rules() {
    ipt_bin="$1"
    allowed_ips="$2"

    "$ipt_bin" -N "$SMTP_GUARD_CHAIN" 2>/dev/null || true
    "$ipt_bin" -F "$SMTP_GUARD_CHAIN"

    # Never interfere with loopback or return traffic.
    "$ipt_bin" -A "$SMTP_GUARD_CHAIN" -o lo -j RETURN
    "$ipt_bin" -A "$SMTP_GUARD_CHAIN" \
        -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN

    # Preserve Docker DNS and ordinary name resolution.
    "$ipt_bin" -A "$SMTP_GUARD_CHAIN" -p udp --dport 53 -j RETURN
    "$ipt_bin" -A "$SMTP_GUARD_CHAIN" -p tcp --dport 53 -j RETURN

    # The only allowed SMTP route is the fixed Mailpit destination.
    for ip in $allowed_ips; do
        "$ipt_bin" -A "$SMTP_GUARD_CHAIN" \
            -p tcp -d "$ip" --dport "$SMTP_GUARD_ALLOWED_PORT" -j RETURN
    done

    # Block SMTP ports to every other destination, regardless of hostname or
    # application SMTP settings. Include Mailpit's own port so changing the
    # hostname to an external endpoint on 1025 cannot bypass the guard.
    for port in $SMTP_GUARD_PORTS; do
        "$ipt_bin" -A "$SMTP_GUARD_CHAIN" \
            -p tcp --dport "$port" -j REJECT --reject-with tcp-reset
    done

    if ! "$ipt_bin" -C OUTPUT -j "$SMTP_GUARD_CHAIN" 2>/dev/null; then
        "$ipt_bin" -I OUTPUT 1 -j "$SMTP_GUARD_CHAIN"
    fi
}

install_rules iptables "$ALLOWED_IPV4"

if command -v ip6tables >/dev/null 2>&1 && ip6tables -L OUTPUT >/dev/null 2>&1; then
    install_rules ip6tables "$ALLOWED_IPV6"
elif [ -s /proc/net/if_inet6 ]; then
    log "IPv6 is present but ip6tables is unavailable; refusing to start"
    exit 69
else
    log "no IPv6 interface addresses detected; IPv6 SMTP blocking is not applicable"
fi

log "active for environment=$ENVIRONMENT; allowing ${SMTP_GUARD_ALLOWED_HOST}:${SMTP_GUARD_ALLOWED_PORT}; blocking ports ${SMTP_GUARD_PORTS}"
drop_capability_and_exec "$@"
