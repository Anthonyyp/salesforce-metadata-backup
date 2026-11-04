# Salesforce metadata backup

A nightly backup of a Salesforce org's setup into a GitHub repo, run by GitHub Actions. Every field, layout, flow,
Apex class, profile, permission set and setting, as Salesforce source, with a commit each night that something
changed and an email saying what. Setup only, not records.

> **Keep your copy private.** This template is public; the repo you make from it holds your org's whole setup,
> and that can include secrets: an API key someone put in a custom metadata record, for one. Use this template
> lets you pick Public: don't.

Free on GitHub's free plan: private repos get 2,000 Action minutes a month. A small org's run takes 3 to 5. Every
night pulls the whole org, so a big org's run takes about as long every night as on the first, up to an hour: about
all of the free minutes. To cut it, set `REPORTS` to `off`, or leave out more types (`EXCLUDED_METADATA`).

## What it does each night

1. **Signs in** to the org by JWT, through an External Client App, with a key kept in the repo's secrets.
2. **Lists everything in the org** into `manifest/package.xml` (`sf project generate manifest --from-org`), so
   something new in the org is backed up without anyone adding it.
3. **Pulls it** into `force-app/`, starting from empty so something deleted in the org shows as deleted.
4. **Commits what changed**, if anything. The repo always holds the whole current setup; its history holds every
   change, line by line.
5. **Reports the result** on the run's page in GitHub: how many files changed and which, with a link to the
   changes. With `EMAIL` on, Salesforce emails it too, from the org itself: no mail server or mailbox needed.
   When a run fails, GitHub emails you on its own (if your Actions notifications are on). That goes to the account
   that last edited the workflow's schedule, not to `MAIL_TO`. For a repo in an organization, Settings →
   Notifications → Custom routing sets which of that account's addresses it uses.
6. **Keeps the run's raw files** for 14 days on the run's page (Artifacts): Salesforce's answer to each pull, the
   listing lines of the CLI's log, and the list of changes. For looking into a bad night.

### What it handles that a plain retrieve doesn't

- **The 10,000-file limit.** One pull takes at most 10,000 files. `split_manifest.py` pulls in batches, but keeps
  everything profiles, permission sets and translations depend on in one batch: a profile pulled without the
  objects, fields and classes it covers comes back without those permissions. Only reports, dashboards, email
  templates, documents and letterheads, which nothing else lists, are split off. They're what grows.
- **The size limit.** A pull also stops at 39 MB zipped, so static resources and content assets, which can be
  megabytes each, go 10 to a batch. If batch 1 is still too big (its pull fails on size), leave types out with
  `EXCLUDED_METADATA`.
- **Noise.** `CleanDataService` (the built-in geocoding rules) comes back in a different order every pull, so it's
  left out by default; otherwise every night would show one file changed.
- **Blips.** Listing the org sends hundreds of calls at once, and one blip fails it, so it's tried 3 times, a minute
  apart. Nothing else is retried: the CLI already rides out network errors while a pull runs, and tomorrow's run is
  the retry for the rest.
- **A bad night.** Failed pulls, and types that fail to list (the CLI leaves those out without a word), stop the
  run before anything is committed. An item the org listed but didn't hand over is kept as it was, not recorded as
  deleted. What raises no error is the backup user losing access: Salesforce just shows it less, and the backup
  records the rest as deleted. So a night that deletes more than a fifth of the backup (`ALERT_DELETED_PERCENT`),
  or of one type with 10 files or more, or the last of any type, says WARNING in the email's subject and on the
  run's page. One that would delete more than half (`BLOCK_DELETED_PERCENT`) isn't committed, and with email on
  Salesforce emails that too. Once access is fixed, the next run puts the files back. With email off, warnings
  show only on the run's page.
- **New metadata.** The project has no API version, so the CLI uses the org's newest, and whatever Salesforce adds
  in a release is listed and backed up. The night after a release can show many files changed. For quiet release
  nights instead, pin one: `"sourceApiVersion": "66.0"` in `sfdx-project.json`. Then nothing newer is backed up
  until you raise it.

App credentials and certificates are never committed: `.gitignore` leaves out certificates, connected apps and
External Client Apps' OAuth settings, which hold consumer keys (and secrets, in an org that exposes them). Edit
`.gitignore` to back them up anyway.

Installed packages' parts are left out by default (they're the vendor's). Profiles and permission sets then come back
without their permissions on those packages' objects, fields and classes. Set `INCLUDE_PACKAGES` to `managed` for
complete profiles.

## Set it up for an org

Two ways: by hand in Setup, or the setup script. Same result. Either way you need `openssl` for the key (Git Bash
has it on Windows).

1. **Make a repo from this template** (Use this template → Create a new repository). **Private.**
2. **A backup user:** its own user, on the System Administrator profile ([The backup user](#the-backup-user)).
3. **The sign-in.** By hand or by script, below.
4. **Turn the email on** if you want it: `EMAIL` and `MAIL_TO` (below). Salesforce sends it, so the org's email
   deliverability must allow it: sandboxes start at *System email only*, which blocks it.
5. **Run it once:** Actions → Metadata backup → Run workflow. The first run commits everything. A new app can take a
   few minutes before it accepts sign-ins.

### By hand

1. **The key and certificate**, in a folder outside the repo:

   ```
   openssl req -x509 -newkey rsa:2048 -nodes -days 1095 -keyout server.key -out server.crt -subj "/CN=metadata-backup"
   openssl x509 -enddate -noout -in server.crt
   ```

   The second line prints the expiry date, for `CERT_EXPIRES`.
2. **The permission set.** Setup → Permission Sets → New: Label `Metadata Backup`. System Permissions: **API Only
   User** and **Password Never Expires**. Assign it to the backup user, and to no one else.
3. **The app.** Setup → App Manager → the arrow next to New App → **New External Client App**.
   - External Client App Name `Metadata Backup`; Contact Email yours; Distribution State **Local**.
   - **Enable OAuth.** Callback URL `http://localhost:1717/OauthRedirect`. Selected OAuth Scopes: *Manage user data
     via APIs (api)* and *Perform requests at any time (refresh_token, offline_access)*. JWT sign-in refuses an app
     without `refresh_token` ("refresh_token scope is required"), though it never uses one.
   - **Enable JWT Bearer Flow**, and upload `server.crt`. Leave *Issue JSON Web Token (JWT)-based access tokens*
     off: the Metadata API refuses those tokens.
   - Create.
4. **Its policies.** The app → Policies → Edit.
   - App Policies: move **Metadata Backup** to **Selected Permission Sets**.
   - OAuth Policies: Permitted Users **Admin approved users are pre-authorized**; IP Relaxation **Relax IP
     restrictions**. Save.
5. **Its consumer key.** The app → Settings → OAuth Settings → **Consumer Key and Secret** (Salesforce emails you a
   code). Copy the key; the secret isn't needed.
6. **The repo's settings** (below): `SF_JWT_KEY` (all of `server.key`), `SF_CLIENT_ID` (the consumer key),
   `SF_USERNAME`, `SF_LOGIN_URL`, `CERT_EXPIRES`. Then delete `server.key` and `server.crt`: the repo is the only
   place the key needs to be. If it's lost, make a new pair and upload the new certificate.

### By script

On a computer with the Salesforce CLI signed in to the org, `openssl`, Python, and optionally the GitHub CLI:

```
setup/setup.sh <org alias> <backup user's username> <owner/repo>
```

It does the same steps: makes the key, deploys the permission set and the app **Metadata Backup**, assigns the
permission set to the backup user, reads back the app's consumer key (or asks you to paste it from Setup), sets the repo's secrets and `SF_*` and `CERT_EXPIRES` variables,
and deletes the key. Without the GitHub CLI it prints them for you to enter. Read it first: it deploys to your org.

### About the app and the key

- **Why not a connected app:** since Spring '26, new connected apps can't be created without asking Salesforce
  Support. Existing ones keep working; an existing connected app with the same settings works the same.
- **The key reaches whoever holds the permission set.** Anyone with the private key can sign in, from anywhere, as
  any user with **Metadata Backup**: keep it on the backup user alone. GitHub's runners have no fixed IP address, so the app relaxes the org's IP
  restrictions; the certificate is what keeps others out. If the key leaks, disable the app at once, then make a new
  key (below).
- **Don't give the backup user *View External Client Apps Consumer Secrets in Metadata*,** and leave the org's
  setting that exposes apps' consumer secrets to the Metadata API off. The backup would then hold them.
- **Sandboxes:** a refreshed sandbox doesn't keep local External Client Apps. Set it up again after a refresh.

## Settings

Settings → Secrets and variables → Actions.

| Kind | Name | What |
|---|---|---|
| Secret | `SF_JWT_KEY` | The private key matching the app's certificate |
| Secret | `SF_CLIENT_ID` | The app's consumer key |
| Variable | `SF_USERNAME` | The Salesforce user it signs in as |
| Variable | `SF_LOGIN_URL` | `https://login.salesforce.com` (default), or `https://test.salesforce.com` for a sandbox |
| Variable | `ORG_LABEL` | The org's name in the email |
| Variable | `EMAIL` | `off` (default), `self` (sent from the backup user's address) or `org` (from `MAIL_FROM`) |
| Variable | `MAIL_TO` | Who gets the email; commas for more than one, up to 5 |
| Variable | `MAIL_FROM` | With `EMAIL` `org`: the organization-wide address it's sent from, verified in Setup → Organization-Wide Addresses (a no-reply works) |
| Variable | `COMMIT_NAME`, `COMMIT_EMAIL` | Who the nightly commits are by (default: GitHub Actions) |
| Variable | `REPORTS` | `on` (default) or `off`. Off leaves out reports and dashboards, which users change daily; report types stay |
| Variable | `INCLUDE_PACKAGES` | `managed`, `unlocked` or both, to back up installed packages' parts too (default: neither, and profiles lack those packages' permissions) |
| Variable | `ALERT_DELETED_PERCENT` | A night that deletes more than this share of the backup, or of one type (10 files or more), or the last of a type, says WARNING in the email and on the run's page (default 20; `off` for never) |
| Variable | `BLOCK_DELETED_PERCENT` | A night that would delete more than this share isn't committed, the run fails, and the email says why (default 50; `off` for never). Set it to `off` for one run if the org really lost that much |
| Variable | `CERT_EXPIRES` | The sign-in certificate's expiry date, `YYYY-MM-DD`. From 30 days before, the email and the run's page say to make a new one (default: blank, no check) |

GitHub won't save a blank variable: to turn a setting off, set it to `off`.

**Advanced**

| Kind | Name | What |
|---|---|---|
| Variable | `EXCLUDED_METADATA` | More types to leave out, by Metadata API name, spaces between, e.g. `EmailTemplate Document`. Added to the defaults (`CleanDataService`, and reports and dashboards with `REPORTS` `off`). |
| Variable | `BATCH_LIMIT` | Files per pull (default 9,000) |

Leaving a type out later deletes its files from the backup on the next run, which can trip `BLOCK_DELETED_PERCENT`:
set it to `off` for that run.

The schedule is the `cron` line at the top of `.github/workflows/backup.yml`: 11:00 UTC every day. The Salesforce
CLI version is pinned in the same file (`@salesforce/cli@2.156.5`); raise it now and then, and run the workflow by
hand once after.

## The backup user

A user of its own, on the **System Administrator** profile. It costs a full Salesforce license.

- **Why admin:** in testing, View All Data and Modify All Data on a lesser profile still missed objects, flows, auth
  providers and half of each profile, without an error. A clone of System Administrator may not get the permissions
  Salesforce adds in later releases, and the gap would be silent too.
- **What keeps it safe:** the **Metadata Backup** permission set. API Only User keeps it out of Salesforce's screens;
  Password Never Expires stops an expired password from breaking the sign-in; and the app lets in only users holding it.
- **Don't use yourself:** API Only User locks an admin out of the screens too. Never assign Metadata Backup to
  anyone but the backup user.
- **Other options:** a clone of System Administrator (check it against System Administrator after big releases), or
  View All Data and Modify All Data on a minimal profile, which misses things. The free Salesforce Integration
  license can't hold System Administrator.
- **Feature licenses are per user:** give it the permission set licenses and permission sets your admins have for
  features such as Data Cloud or Service Cloud, or those features' objects are left out.

Whatever the user can't see isn't backed up: private dashboards, for example, show in the email as items listed but
not handed over.

## Restoring

Everything here is ordinary Salesforce source. To put a piece back, deploy it:
`sf project deploy start --source-dir force-app/main/default/<path> -o <org>`. To see an older version, browse the
file's history on GitHub.

## Renewing the key

The certificate lasts 3 years; with `CERT_EXPIRES` set, the email reminds you 30 days ahead. Before it runs out,
run `setup/setup.sh` again (it updates the same app, the new key goes into the repo's secrets, and `CERT_EXPIRES` is
reset), or by hand: make a new pair, upload the new `server.crt` on the app (Settings → OAuth Settings → Edit), and
update `SF_JWT_KEY` and `CERT_EXPIRES`.
