# SIM-only origin TLS and private nginx audit

This is an operator-applied configuration, not an OAuth enable switch and not an
automatic change made by the application deploy job. The owner approved SIM-only
HTTPS8443 and removal of sensitive OAuth parameters from logs on 2026-11-16.

Scope: retain SIM's existing HTTP listener/routes and add 8443 using the existing
wildcard Origin CA certificate. Preserve lbot as the implicit default for8443;
never add default_server, change443/mtproto, other vhosts, firewall or zone SSL.
Cloudflare requires two exact-host rules for sim.zapleo.com: origin port8443 and
Full(strict), with compatible origin SNI. No Cloudflare access is currently proven.
Do not enable OAuth until the actual edge-to-origin route and CDN log privacy are
verified. Exact CIMD/callback and owner consent remain separate gates.

Access audit records only time, request ID, method, normalized path without query,
status and duration. It does not record headers, cookies, bodies, referer or Location.
SIM errors go directly to a dedicated AF_UNIX rsyslog instance, never /dev/log or
journald. It persists only generated time, parsed severity and a fixed redacted
event; no raw message templates, forwarding or disk queues. The global rsyslog
and neighboring nginx logs remain unchanged. Access audit preserves request outcomes
even if the separate error receiver is temporarily unavailable; Unix datagrams are
not a lossless transport. Monitor the receiver and available disk space.

Limit: malformed traffic before nginx selects the SIM virtual host can still use
the existing default-host error log. This scope cannot change that other vhost.
Cloudflare logs are also unverified: check Logpush/security logging/custom fields;
ClientRequestURI includes query while ClientRequestPath does not. Never claim that
all upstream/default-host logging is private from SIM's configuration alone.

Install only after exact-commit CI and configuration approval:

1. Record revision, hashes of SIM/lbot/nginx config,443 owner, UFW status and strict
   TLS+hostname verification of the existing certificate. Keep a timestamped root-only
   backup of the existing SIM vhost and record any pre-existing target files. Refuse
   unreviewed differences; do not read/copy private keys or truncate existing logs.
2. Install sim-audit.conf to /etc/nginx/conf.d/sim-audit.conf and
   sim-server-security.conf to /etc/nginx/snippets/sim-server-security.conf.
   Add only its include directly after SIM's server_name declaration. Leave the
   neighboring/default vhost untouched and compare its hash afterwards.
3. Install rsyslog-sim.conf to /etc/rsyslog-sim-nginx.conf, the dedicated service to
   /etc/systemd/system/sim-nginx-audit.service, and the logrotate stanza to
   /etc/logrotate.d/sim-nginx-audit. Create /var/log/sim-nginx root:adm0750.
   Validate rsyslog with -N1 and the systemd unit, then start/enable only this service.
   Ensure nginx's www-data can reach its socket. Do not restart global rsyslog.
4. Run nginx -t before graceful reload; stop and restore backup on failure. Verify
   strict TLS/SNI and SIM /api/ready via loopback8443, old SIM HTTP readiness, and
   unchanged lbot route/443 service/config/firewall. Only safe read-only endpoints.
5. With existing authorized Cloudflare access, inspect current rules first and apply
   only exact-host origin-port/strict settings. Without access, stop this step and
   keep OAuth disabled; local origin readiness is not full-route proof.

Rollback must be written before mutation: restore the exact backed-up SIM vhost,
nginx -t and graceful reload first; then stop/disable the dedicated receiver and
remove only newly installed configuration/unit files, daemon-reload. Keep the new
audit logs and old logs. Cloudflare rule rollback requires recorded pre-change
values; never downgrade zone-wide settings. A failed local check triggers local
rollback; an unknown Cloudflare state blocks activation instead of being guessed.

CI uses real nginx/rsyslog inside a disposable Ubuntu22.04 container with network
disabled during testing (no host service/AppArmor changes), a disposable certificate, loopback-only fake backend,
synthetic secrets,502/303 responses, hostname verification and a neighboring vhost.
It verifies method/path/status audit and absence of request/response secrets in all
test logs. No local tests, production tokens, real providers or client messages.
