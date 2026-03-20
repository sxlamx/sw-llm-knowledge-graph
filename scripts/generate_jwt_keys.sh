#!/bin/bash
# Generate RS256 JWT key pair for production use
set -e

KEYS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../secrets" && pwd)"
mkdir -p "$KEYS_DIR"

echo "Generating RS256 key pair in $KEYS_DIR..."

openssl genrsa -out "$KEYS_DIR/jwt_private_key.pem" 2048
openssl rsa -pubout -in "$KEYS_DIR/jwt_private_key.pem" -out "$KEYS_DIR/jwt_public_key.pem"

echo "Private key: $KEYS_DIR/jwt_private_key.pem"
echo "Public key:  $KEYS_DIR/jwt_public_key.pem"
echo "Done."
