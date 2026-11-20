"""Synthetic account-owner statement capture; no real bank sessions or documents."""
import uuid
from unittest.mock import Mock, MagicMock
import pytest
from sqlalchemy import select

from app.automation import queue
from app.database.session import session_scope
from app.integrations.banks.profiles import HDFC
from app.models.automation import AutomationJob
from app.models.banking import BankAccount
from app.models.system import SystemSetting
from app.integrations.banks.session_review import run_command, masked
from app.workers.worker import run_pending_once
from tests.test_automation_first import _hdfc_manual_account


def test_financial_controls_are_rejected_in_statement_capture(client, owner, monkeypatch):
    account = _hdfc_manual_account(client)
    with session_scope() as db:
        bank = db.get(BankAccount, uuid.UUID(account['id']))
        job, _ = queue.enqueue(db, tenant_id=bank.tenant_id, account_id=bank.id, job_type='open_bank_login')
        command = SystemSetting(tenant_id=bank.tenant_id, key=f'browser_review:{job.id}',
                                value={'status': 'pending', 'action': 'click', 'label': 'Send Money'})
        db.add(command)
        db.flush()
        page = Mock(url='https://now.hdfc.bank.in/retail-app/my-accounts/download-statements')
        monkeypatch.setattr('app.integrations.banks.profiles.validate_url', lambda *a, **kw: None)
        assert run_command(db, job, page, HDFC, 'XXXX0127') == []
        assert command.value['status'] == 'failed'
        page.get_by_text.assert_not_called()
    assert masked('Account 50100123450127') == 'Account XXXX0127'


@pytest.mark.parametrize('matches', [True, False])
def test_statement_download_requires_matching_account_before_file_capture(client, owner, monkeypatch, tmp_path, matches):
    from tests.test_browser_banking import CSV
    account = _hdfc_manual_account(client)
    file = tmp_path / 'synthetic.csv'
    file.write_bytes(CSV)
    with session_scope() as db:
        bank = db.get(BankAccount, uuid.UUID(account['id']))
        job, _ = queue.enqueue(db, tenant_id=bank.tenant_id, account_id=bank.id, job_type='open_bank_login')
        command = SystemSetting(tenant_id=bank.tenant_id, key=f'browser_review:{job.id}', value={
            'status': 'pending', 'action': 'download', 'label': 'Excel', 'account_selector': '#selected-account'})
        db.add(command)
        db.flush()
        page = MagicMock(url='https://now.hdfc.bank.in/retail-app/my-accounts/download-statements')
        identity = page.locator.return_value
        identity.count.return_value = 1
        identity.inner_text.return_value = 'Savings **** **01 27 Balance 1250' if matches else 'Savings XXXX9999 Balance 50127.00'
        identity.evaluate.return_value = False
        target = page.get_by_text.return_value.and_.return_value
        target.count.return_value = 1
        target.inner_text.return_value = 'Excel'
        target.get_attribute.return_value = ''
        download = page.expect_download.return_value.__enter__.return_value.value
        download.failure.return_value = None
        download.path.return_value = str(file)
        download.suggested_filename = 'synthetic.csv'
        monkeypatch.setattr('app.integrations.banks.profiles.validate_url', lambda *a, **kw: None)
        files = run_command(db, job, page, HDFC, 'XXXX0127')
        if matches:
            assert files == [('bank-statement.csv', CSV)]
            assert command.value['status'] == 'succeeded'
        else:
            assert files == [] and command.value['status'] == 'failed'
            page.expect_download.assert_not_called()


def test_observed_balance_uses_only_matching_account_selector(client, owner):
    from app.automation.browser_jobs import capture_visible_balance
    from app.balances.service import latest_balances
    account = _hdfc_manual_account(client)
    with session_scope() as db:
        bank = db.get(BankAccount, uuid.UUID(account['id']))
        data = {'controls': [{'parent_class': 'bb-account-selector ng-select',
            'text': 'Account number: Savings **** **01 27\nBalance:\n₹1,250.00\nAvailable Balance'}]}
        assert capture_visible_balance(db, bank, data)
        balances = latest_balances(db, bank.tenant_id, [bank.id])
        assert len(balances) == 1 and str(balances[0].amount) == '1250.0000'
        assert balances[0].verification_status == 'bank_page_observed'
        assert not capture_visible_balance(db, bank, {'controls': [{'parent_class': 'bb-account-selector',
            'text': 'Savings XXXX9999 Balance: ₹50,127.00 Available Balance'}]})


def test_bank_month_selector_uses_native_select_option(client, owner, monkeypatch):
    account = _hdfc_manual_account(client)
    with session_scope() as db:
        bank = db.get(BankAccount, uuid.UUID(account['id']))
        job, _ = queue.enqueue(db, tenant_id=bank.tenant_id, account_id=bank.id, job_type='open_bank_login')
        command = SystemSetting(tenant_id=bank.tenant_id, key=f'browser_review:{job.id}', value={
            'status': 'pending', 'action': 'select_period', 'label': 'November 2026', 'selector': '#period'})
        db.add(command)
        db.flush()
        page = Mock(url='https://now.hdfc.bank.in/retail-app/my-accounts/download-statements')
        page.locator.return_value.count.return_value = 1
        page.locator.return_value.evaluate.return_value = True
        monkeypatch.setattr('app.integrations.banks.profiles.validate_url', lambda *a, **kw: None)
        assert run_command(db, job, page, HDFC, 'XXXX0127') == []
        assert command.value['status'] == 'succeeded'
        page.locator.return_value.select_option.assert_called_once_with(label='November 2026')


@pytest.mark.parametrize('scheduled', [False, True])
def test_balance_screen_flow_saves_private_image_and_alert_without_downloading_a_statement(client, owner, monkeypatch, scheduled):
    import fitz
    from decimal import Decimal
    from app.automation import browser_jobs
    from app.core.config import get_settings
    from app.integrations.banks.browser_engine import BankObservationCaptured
    from app.models.system import Notification
    from app.balances.service import latest_balances
    account = _hdfc_manual_account(client)
    with fitz.open() as doc:
        png = doc.new_page(width=20, height=20).get_pixmap().tobytes('png')
    page = MagicMock(url='https://now.hdfc.bank.in/retail-app/my-accounts/download-statements')
    identity, other = MagicMock(), MagicMock()
    identity.count.return_value = 1
    identity.inner_text.return_value = 'Account number: Savings **** **01 27\nBalance: ₹1,250.00\nAvailable Balance'
    identity.evaluate.return_value = False
    other.count.return_value = 0
    page.locator.side_effect = lambda selector: identity if selector == 'ng-select.bb-account-selector' else other
    page.screenshot.return_value = png
    monkeypatch.setenv('BROWSER_AUTOMATION_ENABLED', 'true')
    get_settings.cache_clear()
    monkeypatch.setattr('app.integrations.banks.profiles.validate_url', lambda *a, **kw: None)
    def retrieve(*args, **kwargs):
        try:
            kwargs['session_control'](page)
        except BankObservationCaptured:
            return []
        raise AssertionError('Observation should finish without a statement download')
    monkeypatch.setattr(browser_jobs, 'retrieve', retrieve)
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    smtp.send_message.return_value = {}
    monkeypatch.setattr('app.notifications.email.smtplib.SMTP', lambda *a, **kw: smtp)
    try:
        with session_scope() as db:
            bank = db.get(BankAccount, uuid.UUID(account['id']))
            bank.low_balance_threshold = Decimal('2000')
            db.add(SystemSetting(tenant_id=bank.tenant_id, key=f'bank_statement_flow:{bank.id}', value={
                'verified': True, 'purpose': 'bank_observation', 'profile_key': 'hdfc_bank', 'steps': [
                    {'action': 'capture_observation', 'account_selector': 'ng-select.bb-account-selector'}]}))
        if scheduled:
            from app.services.account_mail import MailUpdate, save_mail
            from app.services.monitoring import MonitoringSettings, save_settings
            with session_scope() as db:
                bank = db.get(BankAccount, uuid.UUID(account['id']))
                bank.browser_profile_key = 'hdfc_bank'
                save_settings(db, bank, MonitoringSettings(bank_login_method='qr', email_enabled=True,
                    recipients=['alerts@example.com']), None)
                save_mail(db, bank, MailUpdate(host='smtp.example.com', sender='sender@example.com'), None)
                job, _ = browser_jobs.enqueue_saved_login(db, bank, None, trigger='schedule', idempotency_key='scheduled-observation')
                job_id = job.id
        else:
            response = client.post(f"/api/v1/accounts/{account['id']}/browser/start", json={
                'profile_key': 'hdfc_bank', 'login_only': True, 'qr_login': True})
            assert response.status_code == 202, response.text
            job_id = uuid.UUID(response.json()['job_id'])
        run_pending_once(limit=1)
        with session_scope() as db:
            job = db.get(AutomationJob, job_id)
            assert job.status == 'succeeded', job.error_message
            assert job.result['source'] == 'bank_page_observed' and job.result['observation_captured']
            bank = db.get(BankAccount, uuid.UUID(account['id']))
            assert latest_balances(db, bank.tenant_id, [bank.id])[0].amount == Decimal('1250')
            assert db.scalar(select(Notification).where(Notification.type == 'low_balance')) is not None
        if scheduled:
            assert run_pending_once() == 1
            message = smtp.send_message.call_args.args[0]
            assert message.get_body(preferencelist=('html',)) is not None
            assert 'INR 1,250.00' in message.get_body(preferencelist=('html',)).get_content()
            assert smtp.send_message.call_args.kwargs['to_addrs'] == ['alerts@example.com']
            monitoring = client.get(f"/api/v1/accounts/{account['id']}/monitoring").json()
            assert monitoring['latest_email']['sent'] is True
        observation = client.get(f"/api/v1/accounts/{account['id']}/bank-observation")
        assert observation.status_code == 200 and observation.json()['below_target'] is True
        assert observation.json()['screenshot_available'] is True
        assert 'image_path' not in observation.json()
        image = client.get(f"/api/v1/accounts/{account['id']}/bank-observation/screenshot")
        assert image.status_code == 200 and image.content == png
        assert image.headers['cache-control'] == 'no-store'
        assert client.get('/api/v1/statements').json()['total'] == 0
        assert client.get(f'/api/v1/accounts/{uuid.uuid4()}/bank-observation/screenshot').status_code == 404
        page.expect_download.assert_not_called()
        client.headers.pop('Authorization', None)
        assert client.get(f"/api/v1/accounts/{account['id']}/bank-observation/screenshot").status_code == 401
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize('text,expected', [('Available Balance ₹1,250.00', '1250.0000'),
    ('Available Balance -₹1,250.00', '-1250.0000'), ('Available Balance ₹-1,250.00', '-1250.0000')])
def test_observed_rupee_balances_preserve_sign(text, expected):
    from app.integrations.banks.session_review import observed_available_amount
    assert str(observed_available_amount(text)) == expected


def test_requested_statement_capture_uses_existing_pipeline_without_fake_live_balance(client, owner, monkeypatch):
    from app.automation import browser_jobs
    from app.core.config import get_settings
    from tests.test_browser_banking import CSV
    account = _hdfc_manual_account(client)
    monkeypatch.setenv('BROWSER_AUTOMATION_ENABLED', 'true')
    get_settings.cache_clear()
    monkeypatch.setattr(browser_jobs, 'retrieve', lambda *a, **kw: [('statement.csv', CSV)])
    try:
        response = client.post(f"/api/v1/accounts/{account['id']}/browser/start", json={
            'profile_key': 'hdfc_bank', 'login_only': True, 'qr_login': True})
        assert response.status_code == 202
        with session_scope() as db:
            job = db.get(AutomationJob, uuid.UUID(response.json()['job_id']))
            job.payload = {**job.payload, 'statement_review': True}
        run_pending_once()
        with session_scope() as db:
            job = db.get(AutomationJob, uuid.UUID(response.json()['job_id']))
            assert job.status == 'succeeded', job.error_message
            assert job.result['data_retrieved'] is True and job.result['live_balance'] is False
            assert db.get(BankAccount, uuid.UUID(account['id'])).connection_health == 'not_applicable'
        assert client.get('/api/v1/statements').json()['total'] == 1
    finally:
        get_settings.cache_clear()


def test_successful_login_gets_statement_time_instead_of_expiring_original_login_timer(monkeypatch):
    from types import SimpleNamespace
    from datetime import date
    from app.integrations.banks import browser_engine
    page = Mock(url='https://now.hdfc.bank.in/retail-app/my-accounts/download-statements')
    page.is_closed.return_value = False
    page.title.return_value = 'HDFC'
    logout, statement = Mock(), Mock()
    logout.count.return_value = 1
    statement.count.return_value = 0
    page.get_by_role.side_effect = lambda *a, **kw: logout if kw.get('name') == 'Logout' else statement
    pw = MagicMock()
    pw.__enter__.return_value.chromium.launch.return_value.new_context.return_value.new_page.return_value = page
    monkeypatch.setattr('playwright.sync_api.sync_playwright', lambda: pw)
    monkeypatch.setattr(browser_engine, 'validate_url', lambda *a, **kw: None)
    monkeypatch.setattr(browser_engine, 'install_native_domain_guard', lambda *a, **kw: None)
    monkeypatch.setattr(browser_engine, 'open_bank_qr_login', lambda *a: True)
    monkeypatch.setattr(browser_engine, 'get_settings', lambda: SimpleNamespace(
        browser_automation_enabled=True, browser_channel='msedge', browser_login_timeout_seconds=600))
    times = iter([0, 590, 590, 591, 610])
    monkeypatch.setattr(browser_engine, 'time', SimpleNamespace(monotonic=lambda: next(times)))
    control = Mock(side_effect=[[], [('synthetic.csv', b'synthetic')]])
    files = browser_engine.retrieve(HDFC, customer_id='', masked_number='XXXX0127', password='', qr_login=True,
        login_only=True, date_from=date(2026, 1, 1), date_to=date(2026, 1, 31), progress=Mock(), session_control=control)
    assert files == [('synthetic.csv', b'synthetic')]
    assert control.call_count == 2
