#!/usr/bin/env bash
# One-time setup for an org: makes the sign-in key, creates the connected app in the org, and (with the GitHub CLI)
# fills in this repo's secrets and variables. Run it from the repo, signed in to the org with the Salesforce CLI:
#
#   setup/setup.sh <org alias> "<backup user's profile>" [owner/repo]
#   setup/setup.sh myorg "System Administrator" me/acme-metadata-backup
#
# Needs sf and openssl; gh (signed in) to set the repo's settings, otherwise it prints them for you to enter.
# The private key is written to setup/out/server.key (gitignored). Put it in your password manager, then delete it.
set -euo pipefail

org=${1:?usage: setup.sh <org alias> "<profile>" [owner/repo]}
profile=${2:?usage: setup.sh <org alias> "<profile>" [owner/repo]}
repo=${3:-}
here=$(cd "$(dirname "$0")" && pwd)
out="$here/out"
mkdir -p "$out"
# python3 on Mac and Linux; on Windows "python3" can be a Store stub, so try it before trusting it.
py=python3; $py -c 1 2>/dev/null || py=python
json_field() { $py -c "import json,sys; print(json.load(sys.stdin)['result']['$1'])"; }

username=$(sf org display -o "$org" --json | json_field username)
instance=$(sf org display -o "$org" --json | json_field instanceUrl)
case "$instance" in
  *sandbox*|*.cs*) login_url=https://test.salesforce.com ;;
  *) login_url=https://login.salesforce.com ;;
esac
email=$(sf data query -o "$org" -q "SELECT Email FROM User WHERE Username = '$username'" --json   | $py -c "import json,sys; print(json.load(sys.stdin)['result']['records'][0]['Email'])")
echo "Org: $username ($instance)"

if [ ! -f "$out/server.key" ]; then
  # MSYS2_ARG_CONV_EXCL: on Git Bash for Windows, stops "/CN=..." being turned into a file path.
  MSYS2_ARG_CONV_EXCL="/CN" openssl req -x509 -newkey rsa:2048 -nodes -days 1095 \
    -keyout "$out/server.key" -out "$out/server.crt" -subj "/CN=salesforce-metadata-backup" 2>/dev/null
  echo "Made a key and a certificate (good for 3 years): $out"
fi

# The connected app, deployed from a throwaway project so it never lands in this repo's force-app.
proj=$(mktemp -d)
mkdir -p "$proj/force-app/main/default/connectedApps"
echo '{"packageDirectories":[{"path":"force-app","default":true}],"sourceApiVersion":"64.0"}' > "$proj/sfdx-project.json"
cert=$(cat "$out/server.crt")
cat > "$proj/force-app/main/default/connectedApps/Metadata_Backup.connectedApp-meta.xml" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<ConnectedApp xmlns="http://soap.sforce.com/2006/04/metadata">
    <contactEmail>$email</contactEmail>
    <description>Signs the nightly GitHub Actions metadata backup in, by JWT. Retrieves only.</description>
    <label>Metadata Backup</label>
    <oauthConfig>
        <callbackUrl>http://localhost:1717/OauthRedirect</callbackUrl>
        <certificate>$cert</certificate>
        <isAdminApproved>true</isAdminApproved>
        <isConsumerSecretOptional>false</isConsumerSecretOptional>
        <scopes>Api</scopes>
        <scopes>RefreshToken</scopes>
    </oauthConfig>
    <oauthPolicy>
        <ipRelaxation>BYPASS</ipRelaxation>
        <refreshTokenPolicy>infinite</refreshTokenPolicy>
    </oauthPolicy>
    <profileName>$profile</profileName>
</ConnectedApp>
EOF
(cd "$proj" && sf project deploy start -o "$org" --source-dir force-app > /dev/null \
  && sf project retrieve start -o "$org" -m ConnectedApp:Metadata_Backup > /dev/null)
client_id=$(sed -n 's:.*<consumerKey>\(.*\)</consumerKey>.*:\1:p' "$proj/force-app/main/default/connectedApps/Metadata_Backup.connectedApp-meta.xml")
rm -rf "$proj"
echo "Connected app Metadata Backup is in the org."

if [ -n "$repo" ] && command -v gh > /dev/null; then
  gh secret set SF_JWT_KEY -R "$repo" < "$out/server.key"
  printf '%s' "$client_id" | gh secret set SF_CLIENT_ID -R "$repo"
  gh variable set SF_USERNAME -R "$repo" --body "$username"
  gh variable set SF_LOGIN_URL -R "$repo" --body "$login_url"
  echo "Set SF_JWT_KEY, SF_CLIENT_ID, SF_USERNAME and SF_LOGIN_URL on $repo."
else
  echo
  echo "In the repo: Settings > Secrets and variables > Actions"
  echo "  Secret   SF_JWT_KEY    the contents of $out/server.key"
  echo "  Secret   SF_CLIENT_ID  $client_id"
  echo "  Variable SF_USERNAME   $username"
  echo "  Variable SF_LOGIN_URL  $login_url"
fi
echo
echo "Next: the email settings (README), then Actions > Metadata backup > Run workflow."
echo "A new connected app can take a few minutes before it accepts sign-ins."
