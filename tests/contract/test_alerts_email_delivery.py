"""SMTP email delivery — smtplib mocked (no real network/SMTP server
needed), same pattern the OCR contract test uses for `subprocess.run`."""


import pytest

from services.alerts.email_delivery import SmtpConfig, _send_sync


def test_smtp_config_from_env_requires_smtp_host(monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    with pytest.raises(RuntimeError):
        SmtpConfig.from_env()


def test_smtp_config_from_env_reads_all_fields(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_PORT", "2525")
    monkeypatch.setenv("SMTP_USERNAME", "user")
    monkeypatch.setenv("SMTP_PASSWORD", "pass")
    monkeypatch.setenv("SMTP_FROM_ADDRESS", "noreply@example.test")
    config = SmtpConfig.from_env()
    assert config.host == "smtp.example.test"
    assert config.port == 2525
    assert config.username == "user"
    assert config.from_address == "noreply@example.test"


class _FakeSmtp:
    instances = []

    def __init__(self, host, port, timeout=10):
        self.host = host
        self.port = port
        self.started_tls = False
        self.logged_in_with = None
        self.sent_messages = []
        _FakeSmtp.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def starttls(self, context=None):
        self.started_tls = True

    def login(self, username, password):
        self.logged_in_with = (username, password)

    def send_message(self, message):
        self.sent_messages.append(message)


def test_send_sync_starts_tls_logs_in_and_sends(monkeypatch):
    _FakeSmtp.instances.clear()
    monkeypatch.setattr("smtplib.SMTP", _FakeSmtp)

    config = SmtpConfig(host="smtp.example.test", username="user", password="pass", use_tls=True)
    _send_sync(config, to_address="analyst@example.test", subject="[procintel] contract.created", body="amount: 1000")

    assert len(_FakeSmtp.instances) == 1
    smtp = _FakeSmtp.instances[0]
    assert smtp.started_tls is True
    assert smtp.logged_in_with == ("user", "pass")
    assert len(smtp.sent_messages) == 1
    assert smtp.sent_messages[0]["To"] == "analyst@example.test"
    assert smtp.sent_messages[0]["Subject"] == "[procintel] contract.created"


def test_send_sync_skips_tls_and_login_when_not_configured(monkeypatch):
    _FakeSmtp.instances.clear()
    monkeypatch.setattr("smtplib.SMTP", _FakeSmtp)

    config = SmtpConfig(host="smtp.example.test", use_tls=False)
    _send_sync(config, to_address="analyst@example.test", subject="subj", body="body")

    smtp = _FakeSmtp.instances[0]
    assert smtp.started_tls is False
    assert smtp.logged_in_with is None
