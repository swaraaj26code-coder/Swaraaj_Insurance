"""Start the app the easy way (used by the START scripts).

    python -m app.launch            opens http://127.0.0.1:8000 in your browser; the page also shows the
                                    phone address + QR code (HTTPS on your Wi-Fi, served at the same time)
    python -m app.launch --phone    same servers, but prints the phone address and QR code here instead of
                                    opening the browser

Asks for the Google API key if .env does not have one, runs the doctor the
first time (or when the key changes), then starts the server."""

import os
import re
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV, EXAMPLE = ROOT / ".env", ROOT / ".env.example"


def pause_and_exit(code: int):
    try:
        input("\nPress Enter to close this window...")
    except EOFError:
        pass
    sys.exit(code)


def read_key() -> str:
    m = re.search(r"^GOOGLE_API_KEY=(.*)$", ENV.read_text(encoding="utf-8"), re.M)
    return m.group(1).strip() if m else ""


def write_key(key: str):
    text = ENV.read_text(encoding="utf-8")
    if re.search(r"^GOOGLE_API_KEY=", text, re.M):
        text = re.sub(r"^GOOGLE_API_KEY=.*$", lambda _: f"GOOGLE_API_KEY={key}", text, flags=re.M)
    else:
        text = f"GOOGLE_API_KEY={key}\n" + text
    ENV.write_text(text, encoding="utf-8")


def ensure_key():
    if not ENV.exists():
        ENV.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    if read_key() not in ("", "replace-me"):
        return
    print("""
=====================================================================
 A free Google API key is needed (one time only).
   1. Open https://aistudio.google.com/apikey in your browser
   2. Sign in with a Google account
   3. Click "Create API key", then click the copy button next to it
=====================================================================""")
    try:
        key = input("Paste the key here and press Enter: ").strip().strip("'\"")
    except EOFError:
        key = ""
    if len(key) < 20 or " " in key:
        print("\nThat does not look like an API key. Run the start script again and paste the whole key.")
        pause_and_exit(1)
    write_key(key)
    print("Key saved in the .env file.\n")


def free_port(preferred: int) -> int:
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return preferred


def open_when_ready(url: str, health: str):
    import httpx
    for _ in range(60):
        try:
            if httpx.get(health, timeout=2, verify=False).status_code == 200:
                webbrowser.open(url)
                return
        except Exception:
            time.sleep(0.5)


def show_phone_instructions(url: str):
    print("\n=====================================================================")
    print(" PHONE MODE")
    print(f"   On your phone (same Wi-Fi as this computer) open:  {url}")
    print("   or scan this QR code with the phone camera:\n")
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.print_ascii(invert=True)
    except Exception:
        print("   (QR code could not be drawn in this window; type the address instead)")
    print("""
   The phone will warn "Your connection is not private". This is expected
   (the certificate was made on this computer). Tap Advanced -> Proceed.
   If Windows asks whether Python may use the network, tick "Private
   networks" and click Allow.
   Keep this window open while you use the app. Close it to stop.
=====================================================================\n""", flush=True)


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    phone = "--phone" in sys.argv
    ensure_key()
    from . import config, doctor  # imported after the key is in .env

    tested = config._tested_models()
    import asyncio
    # Re-test models when none are saved yet, or when last time a better one was only
    # temporarily unavailable (quota / busy) and we settled for a lesser model.
    ok = asyncio.run(doctor.main(skip_models=bool(tested) and not tested.get("retry_better")))
    if not ok:
        pause_and_exit(1)

    # Two servers, one app (shared sweeps): plain HTTP for this computer's browser, and
    # HTTPS on the Wi-Fi address for the phone (phones only allow the camera over HTTPS).
    import uvicorn
    from . import cert
    http_port = free_port(8000)
    computer_url = f"http://127.0.0.1:{http_port}"
    phone_url = ""
    try:
        cert.main(quiet=True)
        https_port = free_port(8443)
        phone_url = f"https://{cert.lan_ip()}:{https_port}"
        os.environ["PHONE_URL"] = phone_url   # shown on the computer's page with a QR code
    except Exception as exc:
        print(f"Phone access is not available ({exc}); the app still works on this computer.")
    from .server import app

    servers = [uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=http_port, log_level="warning"))]
    if phone_url:
        servers.append(uvicorn.Server(uvicorn.Config(
            app, host="0.0.0.0", port=https_port, log_level="warning",
            ssl_keyfile=str(config.DATA_DIR / "key.pem"), ssl_certfile=str(config.DATA_DIR / "cert.pem"))))

    if phone and phone_url:
        show_phone_instructions(phone_url)
    else:
        print(f"Starting... your browser will open {computer_url}")
        if phone_url:
            print(f"To use your phone camera instead, open {phone_url} on the phone (also shown on the page).")
        print("Keep this window open while you use the app. Close it to stop.\n", flush=True)
        threading.Thread(target=open_when_ready, args=(computer_url, f"{computer_url}/api/health"), daemon=True).start()

    async def serve_all():
        await asyncio.gather(*(s.serve() for s in servers))

    asyncio.run(serve_all())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
