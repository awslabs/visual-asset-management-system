# The one step of the EDH Workstations add-on's root key sync Run Command document, run on the EDH
# controller. The deployment builds the document from these lines: comment and blank lines are dropped,
# __EDH_ROOT_KEY_FILE__ becomes the key file of the configured cluster, and Run Command substitutes the
# document's only parameter, publicKey (base64 characters only, by its allowedPattern).
#
# It prints the EDH root key encrypted to that public key, never the key itself: RSAES-OAEP with SHA-256
# as both the OAEP digest and the MGF1 digest, the algorithm AWS KMS names RSAES_OAEP_SHA_256, so only
# a kms:Decrypt by the sync function recovers it. The public key and the ciphertext are written to a
# private directory (mode 0700, files 0600) that is removed on exit; the output is the base64 ciphertext.
set -eu
umask 077
workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT
printf '%s' '{{ publicKey }}' | base64 -d > "$workdir/public.der"
chmod 600 "$workdir/public.der"
openssl pkeyutl -encrypt -pubin -keyform DER -inkey "$workdir/public.der" -pkeyopt rsa_padding_mode:oaep -pkeyopt rsa_oaep_md:sha256 -pkeyopt rsa_mgf1_md:sha256 -in '__EDH_ROOT_KEY_FILE__' -out "$workdir/root-key.enc"
base64 -w 0 "$workdir/root-key.enc"
