"""The phone remote pairs over TLS, with a certificate made on this machine.

It served plain HTTP on every interface, and the per-pairing secret that
encrypts its commands reached the phone in the clear at login. A certificate is
now generated once, privately, and has to be one TLS will actually load --
a broken one would stop the dashboard starting at all.
"""
import os
import ssl
import stat

from dashboard import server


def test_a_certificate_is_made_once_and_loads(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "CERT_DIR", tmp_path / "certs")
    monkeypatch.setattr(server, "CERT_KEY", tmp_path / "certs" / "dashboard.key")
    monkeypatch.setattr(server, "CERT_FILE", tmp_path / "certs" / "dashboard.crt")
    assert server.ensure_certificate()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(server.CERT_FILE, server.CERT_KEY)
    # The private key is readable by its owner alone.
    assert stat.S_IMODE(os.stat(server.CERT_KEY).st_mode) == 0o600
    before = server.CERT_KEY.read_bytes()
    assert server.ensure_certificate()
    assert server.CERT_KEY.read_bytes() == before      # made once, not per start
