"""Synthetic statements and mocked SMTP only; never contacts a bank or sends real mail."""
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import fitz
import pytest
from sqlalchemy import select

from app.balances.service import check_low_balance, record_snapshot
from app.core.config import get_settings
from app.database.session import session_scope
from app.models.automation import AutomationJob
from app.models.banking import BankAccount
from app.models.system import Notification
from app.statements.pipeline import register_statement
from app.workers.worker import run_pending_once
from tests.test_automation_first import _hdfc_manual_account

CSV = b'Date,Description,Debit,Credit,Balance\n2026-01-01,Opening balance,,,1000.00\n2026-01-02,Fee,200.00,,800.00\n2026-01-31,Closing balance,,,800.00\n'


def configure(client, account, **changes):
    body = {"verification_wait_seconds": 120, "email_enabled": True,
            "recipients": ["account-alert@example.com"], "capture_statement_screenshot": True,
            "low_balance_threshold": "900"}
    return client.put(f"/api/v1/accounts/{account['id']}/monitoring", json={**body, **changes})


def test_monitoring_validation_and_clear_target(client, owner):
    account = _hdfc_manual_account(client)
    assert configure(client, account, verification_wait_seconds=59).status_code == 422
    assert configure(client, account, recipients=[]).status_code == 422
    assert configure(client, account, recipients=["invalid"]).status_code == 422
    assert configure(client, account, low_balance_threshold=None).status_code == 422
    assert configure(client, account).status_code == 200
    data = client.get(f"/api/v1/accounts/{account['id']}/monitoring").json()
    assert data['verification_wait_seconds'] == 120
    assert data['recipients'] == ['account-alert@example.com']
    assert 'smtp_password' not in data
    assert configure(client, account, low_balance_threshold=None, email_enabled=False).status_code == 200
    assert client.get(f"/api/v1/accounts/{account['id']}").json()['low_balance_threshold'] is None
    assert client.get(f"/api/v1/accounts/{uuid.uuid4()}/monitoring").status_code == 404


@pytest.mark.parametrize('closing,expected', [('800.00', 1), ('999.00', 0)])
def test_statement_validation_drives_deduplicated_alerts(client, owner, closing, expected):
    account = _hdfc_manual_account(client)
    assert configure(client, account).status_code == 200
    # The 999 closing figure does not reconcile with the 200 debit.
    content = CSV.replace(b'Closing balance,,,800.00', f'Closing balance,,,{closing}'.encode())
    result = client.post('/api/v1/statements/upload', data={'account_id': account['id']},
                         files={'file': ('statement.csv', content, 'text/csv')})
    assert result.status_code == 201, result.text
    with session_scope() as db:
        bank = db.get(BankAccount, uuid.UUID(account['id']))
        check_low_balance(db, bank)
        check_low_balance(db, bank)
        jobs = db.scalars(select(AutomationJob).where(AutomationJob.job_type == 'balance_email')).all()
        assert len(jobs) == expected
        notifications = db.scalars(select(Notification).where(Notification.type == 'low_balance')).all()
        assert len(notifications) == expected
    if not expected:
        statement_id = result.json()['statement']['id']
        assert client.post(f'/api/v1/statements/{statement_id}/confirm-extraction').status_code == 422


@pytest.mark.parametrize('amount,below_target', [('800', True), ('900', False), ('1200', False)])
def test_email_delivery_uses_only_account_recipient(client, owner, monkeypatch, amount, below_target):
    account = _hdfc_manual_account(client)
    assert configure(client, account).status_code == 200
    for key, value in {'SMTP_HOST': 'smtp.example.com', 'SMTP_FROM': 'banking@example.com',
                       'SMTP_USERNAME': 'test', 'SMTP_PASSWORD': 'synthetic-secret'}.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    smtp.send_message.return_value = {}
    monkeypatch.setattr('app.notifications.email.smtplib.SMTP', lambda *a, **kw: smtp)
    try:
        with session_scope() as db:
            bank = db.get(BankAccount, uuid.UUID(account['id']))
            record_snapshot(db, account=bank, balance_type='available', amount=Decimal(amount), currency='INR',
                            as_of=datetime.now(UTC), source='provider')
            check_low_balance(db, bank)
        assert run_pending_once() == 1
        message = smtp.send_message.call_args.args[0]
        assert smtp.send_message.call_args.kwargs['to_addrs'] == ['account-alert@example.com']
        assert f'{Decimal(amount):,.2f} INR' in message.get_content()
        assert ('below target' in message['Subject']) is below_target
        assert ('Status: TARGET MET' in message.get_content()) is not below_target
        assert 'synthetic-secret' not in message.as_string()
        smtp.starttls.assert_called_once()
        with session_scope() as db:
            job = db.scalar(select(AutomationJob).where(AutomationJob.job_type == 'balance_email'))
            assert job.status == 'succeeded' and job.result['email_sent'] is True
            assert job.result['balance_status'] == ('below_target' if below_target else 'target_met')
    finally:
        get_settings.cache_clear()


def test_newer_recovered_balance_suppresses_queued_email(client, owner, monkeypatch):
    account = _hdfc_manual_account(client)
    assert configure(client, account).status_code == 200
    smtp = MagicMock()
    monkeypatch.setattr('app.notifications.email.smtplib.SMTP', smtp)
    with session_scope() as db:
        bank = db.get(BankAccount, uuid.UUID(account['id']))
        now = datetime.now(UTC)
        record_snapshot(db, account=bank, balance_type='available', amount=Decimal('800'), currency='INR', as_of=now, source='provider')
        check_low_balance(db, bank)
        record_snapshot(db, account=bank, balance_type='available', amount=Decimal('1200'), currency='INR',
                        as_of=now + timedelta(seconds=1), source='provider')
    run_pending_once()
    smtp.assert_not_called()
    with session_scope() as db:
        job = db.scalar(select(AutomationJob).where(AutomationJob.job_type == 'balance_email'))
        assert job.status == 'succeeded' and 'skipped' in job.result


def test_bulk_extraction_deduplicates_active_jobs(client, owner):
    account = _hdfc_manual_account(client)
    with session_scope() as db:
        bank = db.get(BankAccount, uuid.UUID(account['id']))
        register_statement(db, account=bank, content=CSV, filename='statement.csv', source='upload')
    first = client.post('/api/v1/statements/extract-pending', params={'account_id': account['id']})
    assert first.status_code == 202 and first.json()['jobs_queued'] == 1
    second = client.post('/api/v1/statements/extract-pending', params={'account_id': account['id']})
    assert second.json()['jobs_queued'] == 0 and second.json()['already_in_progress'] == 1
    run_pending_once()
    assert client.get('/api/v1/statements').json()['items'][0]['closing_balance'] == '800.00'


def test_pdf_page_images_are_scoped_and_bounds_checked(client, owner):
    account = _hdfc_manual_account(client)
    with fitz.open() as doc:
        for text in ('Synthetic statement page one', 'Synthetic statement page two'):
            doc.new_page().insert_text((30, 30), text)
        content = doc.tobytes()
    with session_scope() as db:
        bank = db.get(BankAccount, uuid.UUID(account['id']))
        statement, _ = register_statement(db, account=bank, content=content, filename='statement.pdf', source='upload')
        statement_id = statement.id
    path = f'/api/v1/statements/{statement_id}/screenshot'
    response = client.get(path, params={'page': 2})
    assert response.status_code == 200 and response.content.startswith(b'\x89PNG')
    assert response.headers['cache-control'] == 'no-store'
    assert client.get(path, params={'page': 3}).status_code == 404
    assert client.get(path, params={'source': 'bank'}).status_code == 404
    assert client.get(f'/api/v1/statements/{uuid.uuid4()}/screenshot').status_code == 404


def test_local_scanned_statement_ocr_requires_review(client, owner):
    account = _hdfc_manual_account(client)
    assert configure(client, account).status_code == 200
    with fitz.open() as original:
        page = original.new_page(width=1000, height=600)
        page.insert_text((40, 60), '01/01/2026 Opening balance 1000.00\n02/01/2026 Bank fee -200.00 800.00\n31/01/2026 Closing balance 800.00', fontsize=18)
        image = page.get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes('png')
        with fitz.open() as scan:
            page = scan.new_page(width=1000, height=600)
            page.insert_image(page.rect, stream=image)
            content = scan.tobytes()
    response = client.post('/api/v1/statements/upload', data={'account_id': account['id']},
        files={'file': ('scanned.pdf', content, 'application/pdf')})
    assert response.status_code == 201, response.text
    statement = response.json()['statement']
    assert statement['closing_balance'] == '800.00'
    assert statement['transaction_count'] == 1
    assert statement['processing_status'] == 'needs_review'
    assert Decimal(statement['parse_confidence']) < Decimal('0.8')
    with session_scope() as db:
        assert db.scalar(select(AutomationJob.id).where(AutomationJob.job_type == 'balance_email')) is None
    confirmed = client.post(f"/api/v1/statements/{statement['id']}/confirm-extraction")
    assert confirmed.status_code == 200 and confirmed.json()['processing_status'] == 'processed_with_warnings'
    with session_scope() as db:
        assert db.scalar(select(AutomationJob.id).where(AutomationJob.job_type == 'balance_email')) is not None


def test_browser_uses_account_wait_and_links_statement_screen(client, owner, monkeypatch):
    from app.automation import browser_jobs
    from tests.test_browser_banking import configure_account
    monkeypatch.setenv('BROWSER_AUTOMATION_ENABLED', 'true')
    get_settings.cache_clear()
    seen = []
    with fitz.open() as doc:
        image = doc.new_page(width=10, height=10).get_pixmap().tobytes('png')
    def fake_retrieve(profile, **kwargs):
        seen.append(kwargs)
        kwargs['on_statement_screenshot'](image)
        return [('statement.csv', CSV)]
    monkeypatch.setattr(browser_jobs, 'retrieve', fake_retrieve)
    try:
        account, job_id = configure_account(client)
        assert configure(client, account, verification_wait_seconds=180, email_enabled=False).status_code == 200
        run_pending_once()
        assert seen[0]['verification_wait_seconds'] == 180
        statement = client.get('/api/v1/statements').json()['items'][0]
        detail = client.get(f"/api/v1/statements/{statement['id']}").json()
        assert detail['bank_screenshot_available'] is True
        screenshot = client.get(f"/api/v1/statements/{statement['id']}/screenshot", params={'source': 'bank'})
        assert screenshot.status_code == 200 and screenshot.content == image
    finally:
        get_settings.cache_clear()


def test_missing_smtp_reports_failed_delivery_without_marking_bank_failed(client, owner, monkeypatch):
    account = _hdfc_manual_account(client)
    assert configure(client, account).status_code == 200
    monkeypatch.setenv('SMTP_HOST', '')
    monkeypatch.setenv('SMTP_FROM', '')
    get_settings.cache_clear()
    try:
        with session_scope() as db:
            bank = db.get(BankAccount, uuid.UUID(account['id']))
            record_snapshot(db, account=bank, balance_type='available', amount=Decimal('800'), currency='INR',
                            as_of=datetime.now(UTC), source='provider')
            check_low_balance(db, bank)
        run_pending_once()
        with session_scope() as db:
            job = db.scalar(select(AutomationJob).where(AutomationJob.job_type == 'balance_email'))
            assert job.status == 'failed' and job.result.get('email_sent') is not True
            bank = db.get(BankAccount, uuid.UUID(account['id']))
            assert bank.last_sync_error is None
    finally:
        get_settings.cache_clear()


def test_wrong_currency_statement_cannot_trigger_target_email(client, owner):
    account = _hdfc_manual_account(client)
    assert configure(client, account).status_code == 200
    result = client.post('/api/v1/statements/upload', data={'account_id': account['id']},
        files={'file': ('usd-statement.csv', b'Currency,USD\n' + CSV, 'text/csv')})
    assert result.status_code == 201, result.text
    statement = result.json()['statement']
    assert statement['processing_status'] == 'needs_review'
    assert statement['currency'] == 'USD'
    assert client.post(f"/api/v1/statements/{statement['id']}/confirm-extraction").status_code == 422
    with session_scope() as db:
        assert db.scalar(select(AutomationJob.id).where(AutomationJob.job_type == 'balance_email')) is None


def test_each_account_uses_its_own_target_and_recipient(client, owner, monkeypatch):
    first = _hdfc_manual_account(client)
    response = client.post('/api/v1/accounts', json={'institution_id': first['institution']['id'],
        'nickname': 'Second account', 'currency': 'INR', 'account_type': 'savings',
        'account_number': '50100987654321', 'country_code': 'IN'})
    assert response.status_code == 201, response.text
    second = response.json()
    assert configure(client, first, low_balance_threshold='900', recipients=['first@example.com']).status_code == 200
    assert configure(client, second, low_balance_threshold='1500', recipients=['second@example.com']).status_code == 200
    for key, value in {'SMTP_HOST': 'smtp.example.com', 'SMTP_FROM': 'banking@example.com'}.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    smtp.send_message.return_value = {}
    monkeypatch.setattr('app.notifications.email.smtplib.SMTP', lambda *a, **kw: smtp)
    try:
        with session_scope() as db:
            for account in (first, second):
                bank = db.get(BankAccount, uuid.UUID(account['id']))
                record_snapshot(db, account=bank, balance_type='available', amount=Decimal('1000'), currency='INR',
                                as_of=datetime.now(UTC), source='provider')
                check_low_balance(db, bank)
                check_low_balance(db, bank)  # same observation must not send twice
        assert run_pending_once() == 2
        deliveries = {call.kwargs['to_addrs'][0]: call.args[0] for call in smtp.send_message.call_args_list}
        assert set(deliveries) == {'first@example.com', 'second@example.com'}
        assert 'target met' in deliveries['first@example.com']['Subject']
        assert 'below target' in deliveries['second@example.com']['Subject']
        assert 'Target: 900.00 INR' in deliveries['first@example.com'].get_content()
        assert 'Target: 1,500.00 INR' in deliveries['second@example.com'].get_content()
    finally:
        get_settings.cache_clear()
