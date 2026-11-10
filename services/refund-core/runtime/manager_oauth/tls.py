"""Keep certificate verification working for UTF-8 workers on Windows."""
import atexit
import os
from pathlib import Path
import tempfile

_bundle_dir = None

def configure_ca_bundle():
    global _bundle_dir
    if os.name != "nt" or _bundle_dir is not None:
        return
    import certifi
    source = next((os.environ[k] for k in ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE") if os.environ.get(k)), certifi.where())
    if source.isascii():
        return
    # libcurl on Windows cannot open this non-ASCII CA path in UTF-8 mode.
    # Copy the exact trust bundle; never turn off peer or hostname verification.
    bundle_dir = tempfile.TemporaryDirectory(prefix="huixu-ca-")
    target = Path(bundle_dir.name) / "ca.pem"
    if not str(target).isascii():
        bundle_dir.cleanup()
        raise RuntimeError("TLS certificate cache requires an ASCII temp directory")
    try:
        target.write_bytes(Path(source).read_bytes())
    except BaseException:
        bundle_dir.cleanup()
        raise
    _bundle_dir = bundle_dir
    for key in ("SSL_CERT_FILE", "CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE"):
        os.environ[key] = str(target)
    atexit.register(bundle_dir.cleanup)
