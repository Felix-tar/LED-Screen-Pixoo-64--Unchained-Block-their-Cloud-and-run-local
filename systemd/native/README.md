# Native systemd units (ALTERNATIVE deployment — not used by default)

Your chosen deployment is the **Docker macvlan** stack, managed by
[`../pixoo-local.service`](../pixoo-local.service). In that mode Pi-hole (:80)
and the shared Mosquitto (:1883) are untouched, so these native units are **not
installed**.

These units are provided for the *alternative native single-IP mode* (section 7
"Fall B") where Pi-hole's admin is moved off port 80 and the services run
directly on the host. They expect:

* a virtualenv at `/opt/pixoo-local/.venv`
* an unprivileged user/group `pixoo-local`
* the env file `/etc/pixoo-local/pixoo-local.env`
* a host-level Mosquitto reachable per `config.yaml`

Only enable these if you deliberately switch to native mode. They will conflict
with the Docker deployment (same ports), so run one or the other, never both.
Hardening (NoNewPrivileges, PrivateTmp, ProtectSystem, ProtectHome,
ReadWritePaths) is set per section 20.
