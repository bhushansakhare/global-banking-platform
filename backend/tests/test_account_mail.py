"""Synthetic account SMTP settings; all delivery is mocked."""
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

from sqlalchemy import select

from app.database.session import session_scope
from app.models.banking import BankAccount
from app.models.system import SystemSetting, AuditLog
from app.services.account_mail import mail_settings
from app.balances.service import record_snapshot, check_low_balance
from app.workers.worker import run_pending_once
from tests.test_monitoring import configure
from tests.test_automation_first import _hdfc_manual_account


def test_account_sender_encrypted_preserved_and_delivered(client, owner, monkeypatch):
    account = _hdfc_manual_account(client)
    path = f"/api/v1/accounts/{account['id']}/mail-settings"
    assert client.get(path).json()['configured'] is False
    body = dict(host='smtp.account.example.com', port=587, sender='sender@example.com',
                username='sender@example.com', password='synthetic-private-password', security='starttls')
    response = client.put(path, json=body)
    assert response.status_code == 200, response.text
    assert response.json()['password_saved'] is True
    assert 'synthetic-private-password' not in response.text
    body.pop('password')
    assert client.put(path, json=body).json()['password_saved'] is True
    with session_scope() as db:
        bank = db.get(BankAccount, uuid.UUID(account['id']))
        row = db.scalar(select(SystemSetting).where(SystemSetting.key == f'account_smtp:{bank.id}'))
        assert 'synthetic-private-password' not in str(row.value)
        assert mail_settings(db, bank).smtp_password == 'synthetic-private-password'
        logs = db.scalars(select(AuditLog).where(AuditLog.action == 'account.mail_settings.update')).all()
        assert logs and all('synthetic-private-password' not in str(log.after) for log in logs)
    assert configure(client, account).status_code == 200
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    smtp.send_message.return_value = {}
    factory = MagicMock(return_value=smtp)
    monkeypatch.setattr('app.notifications.email.smtplib.SMTP', factory)
    with session_scope() as db:
        bank = db.get(BankAccount, uuid.UUID(account['id']))
        record_snapshot(db, account=bank, balance_type='available', amount=Decimal('100'),
                        currency='INR', as_of=datetime.now(UTC), source='provider')
        check_low_balance(db, bank)
    assert run_pending_once() == 1
    assert factory.call_args.args[:2] == ('smtp.account.example.com', 587)
    smtp.login.assert_called_once_with('sender@example.com', 'synthetic-private-password')
    assert smtp.send_message.call_args.args[0]['From'] == 'sender@example.com'
    assert smtp.send_message.call_args.kwargs['to_addrs'] == ['account-alert@example.com']


def test_account_sender_isolation_validation_and_identity_change(client, owner):
    first = _hdfc_manual_account(client)
    response = client.post('/api/v1/accounts', json={'nickname': 'Second', 'institution_id': first['institution']['id'],
        'country_code': 'IN', 'currency': 'INR', 'account_type': 'savings',
        'account_number': '50100987654321'})
    assert response.status_code == 201, response.text
    second = response.json()
    path = f"/api/v1/accounts/{first['id']}/mail-settings"
    body = dict(host='smtp.first.example.com', sender='first@example.com', username='first', password='private')
    assert client.put(path, json=body).status_code == 200
    assert client.get(f"/api/v1/accounts/{second['id']}/mail-settings").json()['configured'] is False
    assert client.get(f'/api/v1/accounts/{uuid.uuid4()}/mail-settings').status_code == 404
    assert client.put(path, json={**body, 'security': 'none'}).status_code == 422
    assert client.put(path, json={**body, 'sender': 'invalid'}).status_code == 422
    body.pop('password')
    assert client.put(path, json={**body, 'host': 'smtp.changed.example.com'}).json()['password_saved'] is False
