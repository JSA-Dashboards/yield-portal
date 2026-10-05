# Email sync on the Droplet

The 30-minute report-email sync can run on the Droplet instead of Kolten's PC
(no Outlook, no PC that has to be on): `deploy/run_email_sync.sh` runs
`match_emails.py --target snowflake --source graph --auto`, which reads the
mailbox on the server through Microsoft Graph (`graph_mail.py`). It's the same
parser and matcher as the PC, and the converted HTML parses into exactly the
same reports as Outlook's text (`tests/test_parsing_local.py` checks this on
saved emails).

**Blocked until IT grants Graph Mail.Read.** The tenant's existing app
("Basis Tracker") has Mail.Send only; `graph_mail` stops with "The Graph app
can't read mail yet" until an app has **Mail.Read (Application)**,
admin-consented, scoped to the one mailbox it reads (ApplicationAccessPolicy or
Exchange RBAC for Applications). A separate app is cleanest: scoping the Basis
Tracker app to one mailbox would also restrict whose mail it can *send* as.

## Turning it on (once the permission exists)

Values (account, user, mailbox) are in `CLAUDE.local.md`, not here. The repo
is public.

1. Clone and install:
   `git clone https://github.com/JSA-Dashboards/yield-portal /opt/yield-portal`,
   `cd /opt/yield-portal && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`,
   `chmod +x deploy/*.sh`.
2. Snowflake key: generate a new key pair **on the Droplet** so the private
   key never leaves it:
   `openssl genrsa 2048 | openssl pkcs8 -topk8 -nocrypt -out yield_portal_svc.p8 && chmod 600 yield_portal_svc.p8`
   (gitignored: `*.p8`). Register its public key as the service user's
   `RSA_PUBLIC_KEY_2` (needs the Analytics ACCOUNTADMIN sign-in). The PC and
   Cloud keep key 1.
3. `.env` (chmod 600) with `USE_SNOWFLAKE=1`, `SNOWFLAKE_ACCOUNT`,
   `SNOWFLAKE_USER`, `SNOWFLAKE_ROLE`, `SNOWFLAKE_WAREHOUSE`,
   `SNOWFLAKE_PRIVATE_KEY_PATH=/opt/yield-portal/yield_portal_svc.p8`,
   `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, `YIELD_MAILBOX`, and
   `GRAPH_CLIENT_SECRET`, which Kolten pastes himself.
4. Dry run: `.venv/bin/python match_emails.py --target snowflake --source graph`.
   Every email should come back "already applied", with the same count as the
   PC's run.
5. Cron, through the alerting wrapper like every other job:
   `*/30 * * * * /opt/alerting/cron-alert "Yield Portal email sync" "/opt/yield-portal/logs/email_sync_*.log" /opt/yield-portal/deploy/run_email_sync.sh`.
   Then add a healthchecks.io check with a 30-minute period, America/Chicago.
6. Stop the PC's copy: `Disable-ScheduledTask -TaskName "Yield Portal - email sync"`.
   Review & edit then shows the Droplet's hostname on the "emails checked" line.
