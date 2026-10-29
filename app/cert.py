"""Make a self-signed HTTPS certificate so a phone on the same Wi-Fi can use
the camera (browsers only allow getUserMedia on HTTPS or localhost).

    python -m app.cert          -> data/cert.pem, data/key.pem for this PC's LAN IP"""

import datetime as dt
import ipaddress
import socket

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from . import config


def lan_ip() -> str:
    """This computer's address on the local network (no packet is actually sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())


def main(quiet: bool = False):
    ip = lan_ip()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "library-claim-agent")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now).not_valid_after(now + dt.timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address(ip)),
                                                        x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .sign(key, hashes.SHA256()))
    (config.DATA_DIR / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (config.DATA_DIR / "key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                                                serialization.NoEncryption()))
    if not quiet:
        print(f"Certificate written. On the phone open: https://{ip}:8443  (accept the warning once)")


if __name__ == "__main__":
    main()
