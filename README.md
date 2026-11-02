# Salesforce metadata backup

A nightly backup of a Salesforce org's setup into a GitHub repo, run by GitHub Actions. Every field, layout, flow,
Apex class, profile, permission set and setting, as Salesforce source, with a commit each night that something
changed and an email saying what. Setup only, not records.

Free on GitHub's free plan: private repos get 2,000 Action minutes a month, and a run takes about 3 to 5.

## What it does each night

1. **Signs in** to the org by JWT, through a connected app, with a key kept in the repo's secrets.
2. **Lists everything in the org** into `manifest/package.xml` (`sf project generate manifest --from-org`), so
   something new in the org is backed up without anyone adding it.
3. **Pulls it** into `force-app/`, starting from empty so something deleted in the org shows as deleted.
4. **Commits what changed**, if anything. The repo always holds the whole current setup; its history holds every
   change, line by line.
5. **Reports the result** on the run's page in GitHub: how many files changed and which, with a link to the
   changes. With `EMAIL` on, Salesforce emails it too, from the org itself: no mail server or mailbox needed.
   When a run fails, GitHub emails the repo's owner on its own.

### Two things it handles that a plain retrieve doesn't

- **The 10,000-file limit.** One pull takes at most 10,000 files. `split_manifest.py` pulls in batches, but keeps
  everything profiles, permission sets and translations depend on in one batch: a profile pulled without the
  objects, fields and classes it covers comes back without those permissions. Only reports, dashboards, email
  templates, documents and letterheads, which nothing else lists, are split off. They're what grows.
- **Noise.** `CleanDataService` (the built-in geocoding rules) comes back in a different order every pull, so it's
  left out by default; otherwise every night would show one file changed.

## Set it up for an org

1. **Make a repo from this template** (Use this template → Create a new repository). Make it private: an org's
   setup is not something to publish.
2. **Run the setup script** on a computer with the Salesforce CLI signed in to the org, plus `openssl`, and
   optionally the GitHub CLI:

   ```
   setup/setup.sh <org alias> "<backup user's profile>" <owner/repo>
   ```

   It makes a key and certificate (good for 3 years), creates the connected app **Metadata Backup** in the org
   (certificate sign-in, scopes `api` and `refresh_token`, pre-authorized for that profile), and sets the repo's
   `SF_*` secrets and variables. Without the GitHub CLI it prints them for you to enter. Keep the private key from
   `setup/out/` in your password manager, then delete it there.

   No command line? Make the same connected app in Setup → App Manager → New Connected App (Enable OAuth Settings;
   Use digital signatures with the certificate; the two scopes; then Manage → Edit Policies → *Admin approved
   users are pre-authorized* and add the profile), and enter the settings below by hand.
3. **Turn the email on** if you want it: `EMAIL` and `MAIL_TO` (below). Salesforce sends it, so the org's email
   deliverability must allow it: sandboxes start at *System email only*, which blocks it.
4. **Run it once:** Actions → Metadata backup → Run workflow. The first run commits everything. A new connected app
   can take a few minutes before it accepts sign-ins.

## Settings

Settings → Secrets and variables → Actions.

| Kind | Name | What |
|---|---|---|
| Secret | `SF_JWT_KEY` | The private key matching the connected app's certificate |
| Secret | `SF_CLIENT_ID` | The connected app's consumer key |
| Variable | `SF_USERNAME` | The Salesforce user it signs in as |
| Variable | `SF_LOGIN_URL` | `https://login.salesforce.com` (default), or `https://test.salesforce.com` for a sandbox |
| Variable | `ORG_LABEL` | The org's name in the email |
| Variable | `EMAIL` | `off` (default), `self` (sent from the backup user's address) or `org` (from `MAIL_FROM`) |
| Variable | `MAIL_TO` | Who gets the email; commas for more than one |
| Variable | `MAIL_FROM` | With `EMAIL` `org`: the organization-wide address it's sent from, verified in Setup → Organization-Wide Addresses (a no-reply works) |
| Variable | `COMMIT_NAME`, `COMMIT_EMAIL` | Who the nightly commits are by (default: GitHub Actions) |
| Variable | `EXCLUDED_METADATA` | Types to leave out, spaces between (default `CleanDataService`); add `Report Dashboard` to skip those |
| Variable | `INCLUDE_PACKAGES` | `managed`, `unlocked` or both, to back up installed packages' parts too (default: neither) |
| Variable | `BATCH_LIMIT` | Files per pull (default 9,000) |

The schedule is the `cron` line at the top of `.github/workflows/backup.yml`: 11:00 UTC every day.

## The backup user

The user needs API Enabled, View Setup and Configuration, and Modify Metadata Through Metadata API Functions
(the Metadata API's retrieve needs it, though the backup never deploys anything). An admin works; a dedicated
integration user is cleaner. Whatever that user can't see isn't backed up: private dashboards, for example, show in
the email as items listed but not handed over.

## Restoring

Everything here is ordinary Salesforce source. To put a piece back, deploy it:
`sf project deploy start --source-dir force-app/main/default/<path> -o <org>`. To see an older version, browse the
file's history on GitHub.

## Renewing the key

The certificate lasts 3 years. Before it runs out, delete `setup/out/`, run `setup/setup.sh` again (it updates the
same connected app), and the new key goes into the repo's secrets.
