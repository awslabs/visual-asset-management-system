# SSL Certificate Bundle

This directory is for an optional Amazon CA certificate bundle used by the VAMS CLI subprocess.

## Why this is needed

Isaac Sim may override the `SSL_CERT_FILE` environment variable with its own certificate bundle that does not include Amazon's CA certificates. This causes SSL verification failures when the VAMS CLI downloads files from S3.

The CLI subprocess never inherits the `SSL_CERT_FILE` or `REQUESTS_CA_BUNDLE` value Isaac Sim sets. The extension names the operating system's CA bundle instead (`/etc/ssl/certs/ca-certificates.crt`, or `/etc/pki/tls/certs/ca-bundle.crt`). A certificate bundle placed here is merged after the system bundle into a file in a private temporary directory, written once per Isaac Sim session.

## Setup

1. Download the Amazon root CA certificates from: https://www.amazontrust.com/repository/
2. Concatenate the PEM files into a single file named `amazon-ca-bundle.pem`
3. Place the file in this directory

The resulting file should contain the following certificates (in PEM format):

-   Amazon Root CA 1
-   Amazon Root CA 2
-   Amazon Root CA 3
-   Amazon Root CA 4
-   Starfield Services Root Certificate Authority - G2

If you do not place a certificate bundle here, the CLI verifies against the system CA bundle, or, when neither is present, against its own default trust store.
