#!/usr/bin/env bash
# One-time setup for an org: makes the sign-in key, creates the External Client App in the org, and (with the
# GitHub CLI) fills in this repo's secrets and variables. Run it from the repo, signed in to the org with the
# Salesforce CLI:
#
#   setup/setup.sh <org alias> <backup user's username> [owner/repo]
#   setup/setup.sh myorg backup@acme.com me/acme-metadata-backup
#
# The backup user: a user of its own on System Administrator (README). This gives it the Metadata Backup permission set
# (API Only User, Password Never Expires), and the app lets in only users holding that set.
# Needs sf, openssl and Python; gh (signed in) to set the repo's settings, otherwise it prints them for you to enter.
# The key is made in setup/out/ (gitignored) and deleted once the repo has it.
# APP_NAME sets the app's API name (default Metadata_Backup); SF_CLIENT_ID skips the question for its consumer key.
set -euo pipefail

usage='usage: setup.sh <org alias> <backup username> [owner/repo]'
org=${1:?$usage}
username=${2:?$usage}
repo=${3:-}
app=${APP_NAME:-Metadata_Backup}
here=$(cd "$(dirname "$0")" && pwd)
out="$here/out"
mkdir -p "$out"
# python3 on Mac and Linux; on Windows "python3" can be a Store stub, so try it before trusting it.
py=python3; $py -c 1 2>/dev/null || py=python
command -v "$py" > /dev/null || { echo "Needs Python (python3 or python)." >&2; exit 1; }
command -v openssl > /dev/null || { echo "Needs openssl, to make the key." >&2; exit 1; }
# The first record's fields, tab-separated (blank if none): query "<SOQL>" Field Field.Sub...
query() {
  local soql=$1; shift
  sf data query -o "$org" -q "$soql" --json | $py -c "
import json, sys
recs = json.load(sys.stdin)['result']['records']
def get(r, path):
    for p in path.split('.'):
        r = (r or {}).get(p)
    return '' if r is None else str(r)
print('\t'.join(get(recs[0], f) for f in sys.argv[1:]) if recs else '')" "$@"
}

instance=$($py -c "import json,sys; print(json.load(sys.stdin)['result']['instanceUrl'])" \
  <<< "$(sf org display -o "$org" --json)")
case "$username" in *\'*|*\\*) echo "That username has a quote or backslash in it." >&2; exit 1 ;; esac
IFS=$'\t' read -r profile email active <<< "$(query "SELECT Profile.Name, Email, IsActive FROM User WHERE Username = '$username'" Profile.Name Email IsActive)"
[ -n "$profile" ] || { echo "No user $username in this org." >&2; exit 1; }
[ "$active" = True ] || echo "Note: $username isn't active; it can't sign in until it is."
me=$($py -c "import json,sys; print(json.load(sys.stdin)['result']['username'])" <<< "$(sf org display -o "$org" --json)")
if [ "$(printf '%s' "$me" | tr '[:upper:]' '[:lower:]')" = "$(printf '%s' "$username" | tr '[:upper:]' '[:lower:]')" ]; then
  echo "The backup user is you ($me): API Only User would lock you out of Salesforce's screens. Use a user of its own." >&2
  exit 1
fi
[ "$profile" = "System Administrator" ] || echo "Note: $username is on $profile; the README recommends System Administrator."
if [ "$(query "SELECT IsSandbox FROM Organization" IsSandbox)" = True ]; then
  login_url=https://test.salesforce.com
else
  login_url=https://login.salesforce.com
fi
echo "Org: $instance. Backup user: $username (profile $profile)."

if [ -f "$out/server.key" ]; then
  echo "Reusing the key in $out (delete that folder for a new one)."
else
  # MSYS2_ARG_CONV_EXCL: on Git Bash for Windows, stops "/CN=..." being turned into a file path.
  MSYS2_ARG_CONV_EXCL="/CN" openssl req -x509 -newkey rsa:2048 -nodes -days 1095 \
    -keyout "$out/server.key" -out "$out/server.crt" -subj "/CN=salesforce-metadata-backup"
  echo "Made a key and a certificate (good for 3 years): $out"
fi
[ -f "$out/server.crt" ] || { echo "$out has a key but no server.crt: delete the folder and run again." >&2; exit 1; }
cert_expires=$(openssl x509 -enddate -noout -in "$out/server.crt" | $py -c \
  "import sys,datetime; s=sys.stdin.read().split('=',1)[1].strip(); print(datetime.datetime.strptime(' '.join(s.split()), '%b %d %H:%M:%S %Y %Z').date())")

# The app, deployed from a throwaway project so it never lands in this repo's force-app. An External Client App:
# new connected apps can't be created any more (Salesforce, Spring '26).
proj=$(mktemp -d)
d="$proj/force-app/main/default"
mkdir -p "$d/permissionsets" "$d/externalClientApps" "$d/extlClntAppOauthSettings" "$d/extlClntAppGlobalOauthSets" \
  "$d/extlClntAppOauthPolicies" "$d/extlClntAppPolicies"
echo '{"packageDirectories":[{"path":"force-app","default":true}]}' > "$proj/sfdx-project.json"
x() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }
ns='xmlns="http://soap.sforce.com/2006/04/metadata"'
# Only holders of this set can sign in through the app; API Only User keeps the backup user out of the screens.
cat > "$d/permissionsets/Metadata_Backup.permissionset-meta.xml" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<PermissionSet $ns>
    <description>The metadata backup's user only. Holders can sign in with the backup's key.</description>
    <hasActivationRequired>false</hasActivationRequired>
    <label>Metadata Backup</label>
    <userPermissions><enabled>true</enabled><name>ApiUserOnly</name></userPermissions>
    <userPermissions><enabled>true</enabled><name>PasswordNeverExpires</name></userPermissions>
</PermissionSet>
EOF
cat > "$d/externalClientApps/$app.eca-meta.xml" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<ExternalClientApplication $ns>
    <contactEmail>$(x "$email")</contactEmail>
    <description>Signs the nightly GitHub Actions metadata backup in, by JWT. Retrieves only.</description>
    <distributionState>Local</distributionState>
    <isProtected>false</isProtected>
    <label>Metadata Backup</label>
</ExternalClientApplication>
EOF
cat > "$d/extlClntAppOauthSettings/${app}_oauth.ecaOauth-meta.xml" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<ExtlClntAppOauthSettings $ns>
    <!-- JWT sign-in refuses an app without RefreshToken: "refresh_token scope is required". -->
    <commaSeparatedOauthScopes>Api, RefreshToken</commaSeparatedOauthScopes>
    <externalClientApplication>$app</externalClientApplication>
    <label>${app}_oauth</label>
</ExtlClntAppOauthSettings>
EOF
cat > "$d/extlClntAppGlobalOauthSets/${app}_glbloauth.ecaGlblOauth-meta.xml" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<ExtlClntAppGlobalOauthSettings $ns>
    <callbackUrl>http://localhost:1717/OauthRedirect</callbackUrl>
    <certificate>$(cat "$out/server.crt")</certificate>
    <externalClientApplication>$app</externalClientApplication>
    <isConsumerSecretOptional>true</isConsumerSecretOptional>
    <!-- Off: JWT-based access tokens are refused by the Metadata API ("SOAP API does not support JWT-based access
         tokens"). JWT sign-in works without them. -->
    <isNamedUserJwtEnabled>false</isNamedUserJwtEnabled>
    <label>${app}_glbloauth</label>
</ExtlClntAppGlobalOauthSettings>
EOF
cat > "$d/extlClntAppOauthPolicies/${app}_oauthPlcy.ecaOauthPlcy-meta.xml" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<ExtlClntAppOauthConfigurablePolicies $ns>
    <commaSeparatedPermissionSet>Metadata_Backup</commaSeparatedPermissionSet>
    <externalClientApplication>$app</externalClientApplication>
    <!-- GitHub's runners have no fixed IP, so the org's IP ranges can't apply; the certificate is the lock. -->
    <ipRelaxationPolicyType>Bypass</ipRelaxationPolicyType>
    <label>${app}_oauthPlcy</label>
    <permittedUsersPolicyType>AdminApprovedPreAuthorized</permittedUsersPolicyType>
</ExtlClntAppOauthConfigurablePolicies>
EOF
cat > "$d/extlClntAppPolicies/${app}_plcy.ecaPlcy-meta.xml" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<ExtlClntAppConfigurablePolicies $ns>
    <externalClientApplication>$app</externalClientApplication>
    <isEnabled>true</isEnabled>
    <isOauthPluginEnabled>true</isOauthPluginEnabled>
    <label>${app}_plcy</label>
</ExtlClntAppConfigurablePolicies>
EOF
(cd "$proj" && sf project deploy start -o "$org" --source-dir force-app --wait 10 > /dev/null)
echo "External Client App Metadata Backup ($app) is in the org."
if [ -z "$(query "SELECT Id FROM PermissionSetAssignment WHERE PermissionSet.Name = 'Metadata_Backup' AND Assignee.Username = '$username'" Id)" ]; then
  sf org assign permset -o "$org" --name Metadata_Backup --on-behalf-of "$username" > /dev/null
fi
echo "Permission set Metadata Backup is assigned to $username."

# The consumer key (not a secret) comes back on a retrieve. If it doesn't, Setup shows it after an email code.
# Don't turn on the org setting that also hands apps' consumer secrets to metadata retrieves: this backup's too.
client_id=${SF_CLIENT_ID:-}
if [ -z "$client_id" ]; then
  (cd "$proj" && sf project retrieve start -o "$org" -m "ExtlClntAppGlobalOauthSettings:${app}_glbloauth" > /dev/null) \
    || true
  client_id=$(sed -n 's:.*<consumerKey>\(.*\)</consumerKey>.*:\1:p' "$d/extlClntAppGlobalOauthSets/${app}_glbloauth.ecaGlblOauth-meta.xml" 2>/dev/null || true)
fi
rm -rf "$proj"
if [ -z "$client_id" ]; then
  echo
  echo "Copy the app's consumer key: $instance/lightning/setup/ManageExternalClientApplication/home"
  echo "  Metadata Backup > Settings > OAuth Settings > Consumer Key and Secret (Salesforce emails you a code)."
  read -r -p "Paste the consumer key: " client_id < /dev/tty
fi
[ -n "$client_id" ] || { echo "No consumer key; nothing was set." >&2; exit 1; }

if [ -n "$repo" ] && command -v gh > /dev/null; then
  gh secret set SF_JWT_KEY -R "$repo" < "$out/server.key"
  printf '%s' "$client_id" | gh secret set SF_CLIENT_ID -R "$repo"
  gh variable set SF_USERNAME -R "$repo" --body "$username"
  gh variable set SF_LOGIN_URL -R "$repo" --body "$login_url"
  gh variable set CERT_EXPIRES -R "$repo" --body "$cert_expires"
  rm -rf "$out"
  echo "Set SF_JWT_KEY, SF_CLIENT_ID, SF_USERNAME, SF_LOGIN_URL and CERT_EXPIRES on $repo, and deleted the key here."
else
  echo
  echo "In the repo: Settings > Secrets and variables > Actions"
  echo "  Secret   SF_JWT_KEY    the contents of $out/server.key"
  echo "  Secret   SF_CLIENT_ID  $client_id"
  echo "  Variable SF_USERNAME   $username"
  echo "  Variable SF_LOGIN_URL  $login_url"
  echo "  Variable CERT_EXPIRES  $cert_expires"
  echo "Then delete $out: the repo is the only place the key needs to be."
fi
echo
echo "Next: the email settings (README), then Actions > Metadata backup > Run workflow."
echo "A new app can take a few minutes before it accepts sign-ins."
