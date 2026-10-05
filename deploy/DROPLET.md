# The yield portal's jobs on the Droplet

The dashboard stays on Streamlit Cloud; the Droplet runs what needs a schedule,
like its other apps: a git clone in `/opt/yield-portal`, a venv, `deploy/run_*.sh`
wrappers (flock, per-run logs) called by cron through `/opt/alerting/cron-alert`
(email on failure), with a healthchecks.io check per job.

Values (Snowflake account, user, addresses) are in `CLAUDE.local.md`, not here.
The repo is public.

## Setup (once)

1. `git clone https://github.com/JSA-Dashboards/yield-portal /opt/yield-portal`,
   `cd /opt/yield-portal && python3 -m venv .venv && .venv/bin/pip install -r deploy/requirements-droplet.txt`,
   `chmod +x deploy/*.sh`.
2. Snowflake key, generated **on the Droplet** so the private key never leaves:
   `openssl genrsa 2048 | openssl pkcs8 -topk8 -nocrypt -out yield_portal_svc.p8 && chmod 600 yield_portal_svc.p8`
   (`*.p8` is gitignored). Register its public key as the service user's
   `RSA_PUBLIC_KEY_2`; the PC and Cloud keep key 1.
3. `.env` (chmod 600): `USE_SNOWFLAKE=1`, `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`,
   `SNOWFLAKE_ROLE`, `SNOWFLAKE_WAREHOUSE`,
   `SNOWFLAKE_PRIVATE_KEY_PATH=/opt/yield-portal/yield_portal_svc.p8`, plus each
   job's settings below.

## Weekly email: live

`deploy/run_weekly_email.sh` runs `weekly_email.py --scheduled --via graph`:
Graph sendMail as the basis tracker's shared mailbox (its app has Mail.Send).

- `.env`: `GRAPH_ENV_FILE=/opt/basis-tracker/.env`, which reads `GRAPH_*` from
  the basis tracker's file so there is one copy of the secret to rotate, like
  `/opt/alerting`. Also `WEEKLY_EMAIL_TO`, `WEEKLY_EMAIL_REPLY_TO`, and
  optionally `WEEKLY_EMAIL_FROM_NAME`.
- Check: `.venv/bin/python weekly_email.py --preview` builds it (charts too) and
  sends nothing; `--to <address> --via graph` sends a test.
- Cron (Droplet time is Central):
  `0 7 * * 2 /opt/alerting/cron-alert "Yield Portal weekly email" "/opt/yield-portal/logs/weekly_email_*.log" /opt/yield-portal/deploy/run_weekly_email.sh`.
  Set its healthchecks.io schedule to `0 7 * * 2`, America/Chicago, by hand
  after the first ping. The default daily period would alarm six days a week.
- The `WEEKLY_EMAILS` table stops a second send in the same week from any
  machine. Keep the PC's "Yield Portal - weekly email" task disabled anyway.

## Email sync: waiting on IT

`deploy/run_email_sync.sh` runs `match_emails.py --target snowflake --source graph --auto`,
reading the mailbox on the server through Graph (`graph_mail.py`). It's the same
parser and matcher as the PC, and the converted HTML parses into exactly the
same reports as Outlook's text (`tests/test_parsing_local.py`).

**Blocked until an app has Graph Mail.Read (Application)**, admin-consented and
scoped to the one mailbox it reads (ApplicationAccessPolicy or RBAC for
Applications). The basis tracker's app has Mail.Send only. Ask for a separate
app: scoping that one would also restrict whom it can send as. Then:

1. `.env`: `YIELD_MAILBOX`, and for a separate reading app
   `GRAPH_READ_CLIENT_ID` + `GRAPH_READ_CLIENT_SECRET` (Kolten pastes the
   secret himself). If IT adds Mail.Read to the basis tracker's app instead,
   nothing more: `graph_mail` falls back to its `GRAPH_*` through
   `GRAPH_ENV_FILE`. The tenant comes the same way.
2. Dry run: `.venv/bin/python match_emails.py --target snowflake --source graph`.
   Every email should come back "already applied", with the same count as the
   PC's run.
3. Cron: `*/30 * * * * /opt/alerting/cron-alert "Yield Portal email sync" "/opt/yield-portal/logs/email_sync_*.log" /opt/yield-portal/deploy/run_email_sync.sh`,
   plus a healthchecks.io check with a 30-minute period.
4. On the PC: `Disable-ScheduledTask -TaskName "Yield Portal - email sync"`.
