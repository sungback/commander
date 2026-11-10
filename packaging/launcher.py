import os
import sys
from pathlib import Path

# Ensure SSL certificate bundle is available on macOS packaged standalone apps
if sys.platform == "darwin" and "SSL_CERT_FILE" not in os.environ:
    try:
        import certifi

        os.environ["SSL_CERT_FILE"] = certifi.where()
    except (ImportError, OSError):
        if Path("/etc/ssl/cert.pem").is_file():
            os.environ["SSL_CERT_FILE"] = "/etc/ssl/cert.pem"

from commander.app import main

sys.exit(main())
