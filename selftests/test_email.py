"""Email notifications against real SMTP servers: plain, STARTTLS, implicit TLS, login, and failures."""

from __future__ import annotations

import smtplib
import socket
import ssl
import time
from email.header import decode_header, make_header
from pathlib import Path

import pytest

from harness import make_summary
from servers import free_port
from ui_automation.config import EmailSettings
from ui_automation.reporting.email_notify import send_run_email


def settings(port: int, **kw) -> EmailSettings:
    base = dict(enabled=True, smtp_host="127.0.0.1", smtp_port=port, use_tls=False, from_addr="ci@example.com",
                to_addrs=("qa@example.com", "lead@example.com"), timeout_seconds=5)
    base.update(kw)
    return EmailSettings(**base)


@pytest.fixture
def summary(tmp_path: Path):
    return make_summary(tmp_path / "run", failures=2)


@pytest.fixture
def trust_cert(monkeypatch: pytest.MonkeyPatch, certs):
    monkeypatch.setenv("SSL_CERT_FILE", str(certs[0]))


def test_plain_delivery_with_report(smtp, summary) -> None:
    controller, sink = smtp()
    s, report = summary
    send_run_email(s, settings(controller.port), attachment=report)
    [(sender, rcpts, msg)] = sink.messages
    assert sender == "ci@example.com" and rcpts == ["qa@example.com", "lead@example.com"]
    subject = str(make_header(decode_header(msg["Subject"])))
    assert subject.startswith("[UI Automation] ❌ FAILED — 2 of 3 tests failed")
    body = next(p for p in msg.walk() if p.get_content_type() == "text/plain" and not p.get_filename())
    text = body.get_payload(decode=True).decode()
    assert "Broken 0 at step \"Assert heading 'Installation' is visible\"" in text
    [att] = [p for p in msg.walk() if p.get_filename()]
    assert att.get_content_type() == "text/html" and att.get_payload(decode=True) == report.read_bytes()


def test_starttls_with_trusted_certificate(smtp, summary, server_tls, trust_cert) -> None:
    controller, sink = smtp(starttls=server_tls)
    send_run_email(summary[0], settings(controller.port, use_tls=True), attachment=summary[1])
    assert len(sink.messages) == 1 and sink.tls == [True]


def test_starttls_refuses_untrusted_certificate(smtp, summary, server_tls) -> None:
    controller, sink = smtp(starttls=server_tls)
    with pytest.raises(ssl.SSLCertVerificationError):
        send_run_email(summary[0], settings(controller.port, use_tls=True))
    assert sink.messages == []


def test_server_requiring_starttls_rejects_plain(smtp, summary, server_tls) -> None:
    controller, sink = smtp(starttls=server_tls)
    with pytest.raises(smtplib.SMTPException):
        send_run_email(summary[0], settings(controller.port, use_tls=False))
    assert sink.messages == []


def test_implicit_tls_with_trusted_certificate(smtp, summary, server_tls, trust_cert) -> None:
    controller, sink = smtp(implicit_tls=server_tls)
    send_run_email(summary[0], settings(controller.port, use_ssl=True, use_tls=True))
    # (aiosmtpd only flags STARTTLS sessions; the refused-untrusted test below proves TLS is in use.)
    assert len(sink.messages) == 1


def test_implicit_tls_refuses_untrusted_certificate(smtp, summary, server_tls) -> None:
    controller, sink = smtp(implicit_tls=server_tls)
    with pytest.raises(ssl.SSLCertVerificationError):
        send_run_email(summary[0], settings(controller.port, use_ssl=True))
    assert sink.messages == []


def test_login_success(smtp, summary) -> None:
    controller, sink = smtp(login=("robot", "s3cret"))
    send_run_email(summary[0], settings(controller.port, smtp_user="robot", smtp_password="s3cret"))
    assert len(sink.messages) == 1


def test_wrong_password(smtp, summary) -> None:
    controller, sink = smtp(login=("robot", "s3cret"))
    # smtplib tries each AUTH mechanism; depending on the server this ends as an auth error or a hang-up.
    with pytest.raises((smtplib.SMTPAuthenticationError, smtplib.SMTPServerDisconnected)):
        send_run_email(summary[0], settings(controller.port, smtp_user="robot", smtp_password="wrong", timeout_seconds=2))
    assert sink.messages == []


def test_server_requires_login_but_none_configured(smtp, summary) -> None:
    controller, sink = smtp(login=("robot", "s3cret"))
    with pytest.raises(smtplib.SMTPException):
        send_run_email(summary[0], settings(controller.port))
    assert sink.messages == []


def test_one_rejected_recipient_is_reported_and_others_still_get_it(smtp, summary, capsys) -> None:
    controller, sink = smtp(reject={"lead@example.com"})
    send_run_email(summary[0], settings(controller.port))
    assert [rcpts for _, rcpts, _ in sink.messages] == [["qa@example.com"]]
    assert "lead@example.com" in capsys.readouterr().out


def test_all_recipients_rejected(smtp, summary) -> None:
    controller, sink = smtp(reject={"qa@example.com", "lead@example.com"})
    with pytest.raises(smtplib.SMTPRecipientsRefused):
        send_run_email(summary[0], settings(controller.port))


def test_server_down(summary) -> None:
    with pytest.raises(ConnectionRefusedError):
        send_run_email(summary[0], settings(free_port()))


def test_slow_server_times_out(smtp, summary) -> None:
    controller, sink = smtp(delay=10)
    start = time.monotonic()
    with pytest.raises((socket.timeout, TimeoutError, smtplib.SMTPServerDisconnected)):
        send_run_email(summary[0], settings(controller.port, timeout_seconds=1))
    assert time.monotonic() - start < 5


def test_large_report_arrives_intact(smtp, tmp_path: Path) -> None:
    controller, sink = smtp()
    s, report = make_summary(tmp_path / "big", failures=1)
    report.write_bytes(report.read_bytes() + b"<!--" + b"x" * 8_000_000 + b"-->")
    send_run_email(s, settings(controller.port), attachment=report)
    [att] = [p for p in sink.messages[0][2].walk() if p.get_filename()]
    assert att.get_payload(decode=True) == report.read_bytes()


@pytest.mark.parametrize(
    "overrides",
    [dict(enabled=False), dict(smtp_host=""), dict(to_addrs=())],
)
def test_nothing_sent_when_not_configured(smtp, summary, overrides) -> None:
    controller, sink = smtp()
    send_run_email(summary[0], settings(controller.port, **overrides))
    assert sink.messages == []


def test_unresolved_credentials_skip(smtp, summary, capsys) -> None:
    controller, sink = smtp()
    send_run_email(summary[0], settings(controller.port, smtp_user="${EMAIL_SMTP_USER}", smtp_password="x"))
    assert sink.messages == [] and "not resolved" in capsys.readouterr().out


def test_missing_sender_is_an_error(summary) -> None:
    with pytest.raises(ValueError, match="from_addr"):
        send_run_email(summary[0], settings(1, from_addr=""))


def test_report_bug_still_sends_a_fallback_report(smtp, tmp_path: Path, capsys) -> None:
    """If building the report fails (here: a corrupt summary.json), notifications still go out."""
    import json

    import yaml

    from ui_automation.config import load_settings
    from ui_automation.reporting.pipeline import notify_run

    controller, sink = smtp()
    s, _ = make_summary(tmp_path / "run", failures=1)
    data = s.to_dict()
    data["tests"][0]["videos"] = [12345]  # not a path: rendering this crashes
    (s.run_dir / "summary.json").write_text(json.dumps(data), encoding="utf-8")
    cfg = tmp_path / "s.yaml"
    cfg.write_text(yaml.safe_dump({"base_url": "http://127.0.0.1", "notifications": {"email": {
        "enabled": True, "smtp_host": "127.0.0.1", "smtp_port": controller.port, "use_tls": False,
        "from_addr": "a@b.c", "to_addrs": ["d@e.f"]}}}))
    report = notify_run(s.run_dir, load_settings(cfg))
    assert "sending a plain fallback report" in capsys.readouterr().out
    assert "could not be built" in report.read_text()
    [(_, _, msg)] = sink.messages
    assert any(p.get_filename() == "ui-test-report.html" for p in msg.walk())
