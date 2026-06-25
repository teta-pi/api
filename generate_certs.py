"""
Generate TETA+PI P-256 certificate chain for C2PA signing.

Produces:
  certs/root_ca.key.pem       — Root CA private key (keep secret)
  certs/root_ca.cert.pem      — Root CA self-signed certificate
  certs/signing.key.pem       — Signing key (used by API for countersigning)
  certs/signing.cert.pem      — Signing certificate (issued by Root CA)
  certs/chain.cert.pem        — Full chain: signing + root (for C2PA manifest)

Run once:  python generate_certs.py
Re-run to rotate (old certs become invalid — update .env and restart API).
"""

import datetime
import os
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID

CERTS_DIR = Path(__file__).parent / "certs"
CERTS_DIR.mkdir(exist_ok=True)

NOW = datetime.datetime.now(datetime.timezone.utc)


def _gen_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def _save_key(key: ec.EllipticCurvePrivateKey, path: Path) -> None:
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    os.chmod(path, 0o600)
    print(f"  wrote {path}")


def _save_cert(cert: x509.Certificate, path: Path) -> None:
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    print(f"  wrote {path}")


# ── Root CA ───────────────────────────────────────────────────────────────────

root_key = _gen_key()
root_name = x509.Name([
    x509.NameAttribute(NameOID.COMMON_NAME, "TETA+PI Root CA"),
    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "TETA+PI Trust Infrastructure"),
    x509.NameAttribute(NameOID.COUNTRY_NAME, "DE"),
])

root_cert = (
    x509.CertificateBuilder()
    .subject_name(root_name)
    .issuer_name(root_name)
    .public_key(root_key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(NOW)
    .not_valid_after(NOW + datetime.timedelta(days=365 * 10))  # 10 years
    .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
    .add_extension(
        x509.KeyUsage(
            digital_signature=True, key_cert_sign=True, crl_sign=True,
            content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False,
            encipher_only=False, decipher_only=False,
        ),
        critical=True,
    )
    .add_extension(
        x509.SubjectKeyIdentifier.from_public_key(root_key.public_key()),
        critical=False,
    )
    .sign(root_key, hashes.SHA256())
)

print("Root CA:")
_save_key(root_key, CERTS_DIR / "root_ca.key.pem")
_save_cert(root_cert, CERTS_DIR / "root_ca.cert.pem")

# ── Signing Certificate (issued by Root CA) ───────────────────────────────────

signing_key = _gen_key()
signing_name = x509.Name([
    x509.NameAttribute(NameOID.COMMON_NAME, "TETA+PI Content Signing"),
    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "TETA+PI Trust Infrastructure"),
    x509.NameAttribute(NameOID.COUNTRY_NAME, "DE"),
])

signing_cert = (
    x509.CertificateBuilder()
    .subject_name(signing_name)
    .issuer_name(root_name)
    .public_key(signing_key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(NOW)
    .not_valid_after(NOW + datetime.timedelta(days=365 * 2))  # 2 years
    .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    .add_extension(
        x509.KeyUsage(
            digital_signature=True, content_commitment=True,
            key_cert_sign=False, crl_sign=False,
            key_encipherment=False, data_encipherment=False,
            key_agreement=False, encipher_only=False, decipher_only=False,
        ),
        critical=True,
    )
    .add_extension(
        x509.ExtendedKeyUsage([
            ExtendedKeyUsageOID.CODE_SIGNING,
            x509.ObjectIdentifier("1.3.6.1.4.1.19406.1.1.2.1"),  # id-c2pa-sign
        ]),
        critical=False,
    )
    .add_extension(
        x509.SubjectKeyIdentifier.from_public_key(signing_key.public_key()),
        critical=False,
    )
    .add_extension(
        x509.AuthorityKeyIdentifier.from_issuer_public_key(root_key.public_key()),
        critical=False,
    )
    .sign(root_key, hashes.SHA256())
)

print("\nSigning Certificate:")
_save_key(signing_key, CERTS_DIR / "signing.key.pem")
_save_cert(signing_cert, CERTS_DIR / "signing.cert.pem")

# ── Full chain (signing → root) ───────────────────────────────────────────────

chain_path = CERTS_DIR / "chain.cert.pem"
chain_pem = (
    signing_cert.public_bytes(serialization.Encoding.PEM)
    + root_cert.public_bytes(serialization.Encoding.PEM)
)
chain_path.write_bytes(chain_pem)
print(f"\nChain:  {chain_path}")

# ── Print .env snippet ────────────────────────────────────────────────────────

signing_key_pem = signing_key.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()

signing_cert_pem = signing_cert.public_bytes(serialization.Encoding.PEM).decode()
root_cert_pem = root_cert.public_bytes(serialization.Encoding.PEM).decode()

print("\n\n─── Add to /opt/tetapi/api/.env on the server ───────────────────────────")
print(f'C2PA_SIGNING_KEY_PEM="{signing_key_pem.strip()}"')
print(f'C2PA_SIGNING_CERT_PEM="{signing_cert_pem.strip()}"')
print(f'C2PA_ROOT_CA_PEM="{root_cert_pem.strip()}"')
print("─────────────────────────────────────────────────────────────────────────")
print("\nDone. Fingerprints:")
from cryptography.hazmat.primitives import hashes as _h
import hashlib
for label, cert in [("Root CA", root_cert), ("Signing", signing_cert)]:
    fp = hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
    print(f"  {label}: SHA-256={fp[:16]}…  serial={cert.serial_number}")
