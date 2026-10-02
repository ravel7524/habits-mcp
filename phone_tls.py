"""Local EC certificate identity; phones pin its SHA-256 DER fingerprint."""

import ipaddress
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


def ensure_certificate(directory: Path, bind: str) -> tuple[Path, Path, str]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    from .desktop_bridge import BridgeError, private_file

    certificate_path = directory / "desktop-cert.pem"
    key_path = directory / "desktop-key.pem"
    if certificate_path.is_symlink() or key_path.is_symlink():
        raise BridgeError("certificate files must not be symlinks")
    if certificate_path.exists() != key_path.exists():
        raise BridgeError("incomplete TLS identity; existing files were preserved")
    now = datetime.now(timezone.utc)
    if certificate_path.exists():
        certificate = x509.load_pem_x509_certificate(certificate_path.read_bytes())
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        expected = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        actual = certificate.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        if not isinstance(key, ec.EllipticCurvePrivateKey) or expected != actual:
            raise BridgeError("TLS certificate/key mismatch; existing files were preserved")
        names = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        if ipaddress.ip_address(bind) not in names.get_values_for_type(x509.IPAddress):
            raise BridgeError("the saved certificate does not cover this phone-bind IP; use the original IP or a fresh dedicated data directory and re-pair")
        if not certificate.not_valid_before_utc <= now < certificate.not_valid_after_utc:
            raise BridgeError("the saved certificate is expired or not yet valid; use a fresh dedicated data directory and re-pair")
        for path in (certificate_path, key_path):
            os.chmod(path, 0o600)
    else:
        key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Habits private desktop bridge")])
        ips = {ipaddress.ip_address(bind), ipaddress.ip_address("127.0.0.1"), ipaddress.ip_address("::1"), ipaddress.ip_address("10.0.2.2")}
        certificate = (
            x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=365))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"), *(x509.IPAddress(ip) for ip in sorted(ips, key=str))]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(key, hashes.SHA256())
        )
        private_file(key_path, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        private_file(certificate_path, certificate.public_bytes(serialization.Encoding.PEM))
    return certificate_path, key_path, certificate.fingerprint(hashes.SHA256()).hex()
