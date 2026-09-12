#!/usr/bin/env bash
# Issues a server certificate for one hostname, signed by your existing
# internal CA, in the plain PEM cert+key format Caddy's `tls <cert> <key>`
# directive expects -- no format conversion needed, just copy the two
# output files to wherever your Caddy instance's config points.
#
# Run this from the directory holding your CA's cert+key (same CA you
# already used for the LDAP cert, islab-ca.crt). Defaults to ca.crt/ca.key
# in the current directory; override with CA_CERT/CA_KEY if yours are
# named differently.
#
# Usage:
#   ./issue-cert.sh dsh.islab.xxx
#   ./issue-cert.sh dsh.islab.xxx 3650          # custom validity in days (default 825)
#   CA_CERT=my-ca.pem CA_KEY=my-ca-key.pem ./issue-cert.sh dsh.islab.xxx

set -euo pipefail

HOSTNAME="${1:?Usage: ./issue-cert.sh <hostname> [days]}"
DAYS="${2:-825}"

CA_CERT="${CA_CERT:-ca.crt}"
CA_KEY="${CA_KEY:-ca.key}"

for f in "$CA_CERT" "$CA_KEY"; do
  if [[ ! -f "$f" ]]; then
    echo "Missing $f -- set CA_CERT/CA_KEY if your CA files are named differently." >&2
    exit 1
  fi
done

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

openssl genrsa -out "$WORKDIR/${HOSTNAME}.key" 2048 2>/dev/null

openssl req -new \
  -key "$WORKDIR/${HOSTNAME}.key" \
  -out "$WORKDIR/${HOSTNAME}.csr" \
  -subj "/CN=${HOSTNAME}"

# Modern browsers/clients validate against subjectAltName, not the
# deprecated CN-only approach -- a cert without this will fail hostname
# verification in current Chrome/Firefox even if the CA itself is trusted.
cat > "$WORKDIR/${HOSTNAME}.ext" <<EOF
subjectAltName = DNS:${HOSTNAME}
extendedKeyUsage = serverAuth
keyUsage = digitalSignature, keyEncipherment
EOF

openssl x509 -req \
  -in "$WORKDIR/${HOSTNAME}.csr" \
  -CA "$CA_CERT" -CAkey "$CA_KEY" -CAcreateserial \
  -out "${HOSTNAME}.crt" \
  -days "$DAYS" -sha256 \
  -extfile "$WORKDIR/${HOSTNAME}.ext"

cp "$WORKDIR/${HOSTNAME}.key" "${HOSTNAME}.key"
chmod 600 "${HOSTNAME}.key"

echo
echo "Issued: ${HOSTNAME}.crt + ${HOSTNAME}.key (valid ${DAYS} days)"
echo "Copy both files to wherever your Caddy instance's config expects them."
echo
echo "Verifying against the CA and checking the SAN entry:"
openssl verify -CAfile "$CA_CERT" "${HOSTNAME}.crt"
openssl x509 -in "${HOSTNAME}.crt" -noout -ext subjectAltName
