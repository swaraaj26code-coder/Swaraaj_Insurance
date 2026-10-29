"""Network settings that make the app work on home and office networks alike.
Both are on by default and harmless on a normal home connection; set them to 0
in .env to switch them off.

USE_OS_TRUST=1  outgoing HTTPS also trusts the operating system's certificate
                store, so a TLS-inspecting office proxy (e.g. Zscaler) whose
                root certificate is installed in Windows is accepted. Done by
                writing certifi's bundle plus the OS certificates to
                data/ca-bundle.pem and pointing SSL_CERT_FILE at it (honoured
                by httpx and the Gemini SDK). Server-side TLS is untouched,
                so phone mode's HTTPS server keeps working.
FORCE_IPV4=1    resolve hosts to IPv4 only, for networks where IPv6 is
                blackholed and every connection would first time out on IPv6."""

import os
import socket
import ssl
from pathlib import Path

SERVER_AUTH = "1.3.6.1.5.5.7.3.1"


def _os_trust_bundle() -> Path | None:
    import certifi
    parts = [Path(certifi.where()).read_text(encoding="utf-8")]
    if hasattr(ssl, "enum_certificates"):  # Windows certificate store
        for store in ("ROOT", "CA"):
            for der, encoding, trust in ssl.enum_certificates(store):
                if encoding == "x509_asn" and (trust is True or SERVER_AUTH in trust):
                    parts.append(ssl.DER_cert_to_PEM_cert(der))
    else:  # Linux distributions keep a system bundle; macOS falls back to certifi alone
        for p in ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt"):
            if Path(p).exists():
                parts.append(Path(p).read_text(encoding="utf-8", errors="ignore"))
    out = Path(__file__).resolve().parent.parent / "data" / "ca-bundle.pem"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(parts), encoding="utf-8")
    return out


def apply():
    if os.getenv("USE_OS_TRUST", "1") == "1" and not os.getenv("SSL_CERT_FILE"):
        try:
            os.environ["SSL_CERT_FILE"] = str(_os_trust_bundle())
        except Exception:
            pass  # fall back to the default certificates
    if os.getenv("FORCE_IPV4", "1") == "1":
        original = socket.getaddrinfo

        def ipv4_first(host, *args, **kwargs):
            results = original(host, *args, **kwargs)
            v4 = [r for r in results if r[0] == socket.AF_INET]
            return v4 or results

        socket.getaddrinfo = ipv4_first
