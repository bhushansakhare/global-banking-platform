"""Synthetic scheduler jobs only; no bank window or real email."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.database.session import session_scope
from app.models.automation import AutomationJob, AutomationSettings
from app.models.banking import BankAccount
from app.models.system import SystemSetting
from app.scheduler.scheduler import start_run, tick
from tests.test_automation_first import _hdfc_manual_account


@pytest.fixture
def browser_on(monkeypatch):
    monkeypatch.setenv('BROWSER_AUTOMATION_ENABLED', 'true')
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def setup_account(client, *, own_schedule=False, reviewed=True):
    account = _hdfc_manual_account(client)
    client.get('/api/v1/automation')
    with session_scope() as db:
        bank = db.get(BankAccount, uuid.UUID(account['id']))
        bank.browser_profile_key = 'hdfc_bank'
        bank.automation_enabled, bank.automation_paused = True, False
        db.add(SystemSetting(tenant_id=bank.tenant_id, key=f'account_monitoring:{bank.id}',
                             value={'bank_login_method': 'qr'}))
        if reviewed:
            db.add(SystemSetting(tenant_id=bank.tenant_id, key=f'bank_statement_flow:{bank.id}', value={
                'verified': True, 'purpose': 'bank_observation', 'profile_key': 'hdfc_bank',
                'steps': [{'action': 'capture_observation', 'account_selector': 'ng-select.bb-account-selector'}]}))
        settings = db.scalar(select(AutomationSettings).where(AutomationSettings.tenant_id == bank.tenant_id))
        settings.master_enabled, settings.paused = True, False
        settings.auto_generate_daily_report = False
        settings.next_run_at = datetime.now(UTC) + timedelta(hours=1) if own_schedule else datetime.now(UTC) - timedelta(minutes=1)
        if own_schedule:
            bank.sync_schedule, bank.timezone = '15 9 * * *', 'Asia/Kolkata'
            bank.next_sync_at = datetime.now(UTC) - timedelta(minutes=1)
    return account


@pytest.mark.parametrize('own_schedule', [False, True])
def test_scheduler_queues_reviewed_qr_flow_once_and_exposes_next_run(client, owner, browser_on, own_schedule):
    account = setup_account(client, own_schedule=own_schedule)
    tick()
    tick()
    with session_scope() as db:
        jobs = db.scalars(select(AutomationJob).where(AutomationJob.account_id == uuid.UUID(account['id']))).all()
        assert len(jobs) == 1
        job = jobs[0]
        assert job.job_type == 'open_bank_login' and job.trigger == 'schedule'
        assert job.payload['qr_login'] and job.payload['statement_review']
        assert not job.payload['user_requested_browser']
        # A future scheduled slot must not resume a waiting verification without a user.
        job.status, job.requires_user_action = 'awaiting_user', 'reauth'
        settings = db.scalar(select(AutomationSettings))
        start_run(db, settings, trigger='schedule', slot=datetime.now(UTC) + timedelta(days=1))
        assert job.status == 'awaiting_user'
    view = client.get(f"/api/v1/accounts/{account['id']}").json()['automation']
    assert view['status'] == 'waiting_for_user'


@pytest.mark.parametrize('reason', ['paused', 'disabled', 'unreviewed', 'browser_off', 'master_off'])
def test_scheduler_skips_accounts_that_are_not_enabled_for_reviewed_checks(client, owner, browser_on, monkeypatch, reason):
    account = setup_account(client, reviewed=reason != 'unreviewed')
    with session_scope() as db:
        bank = db.get(BankAccount, uuid.UUID(account['id']))
        if reason == 'paused':
            bank.automation_paused = True
        if reason == 'disabled':
            bank.automation_enabled = False
        if reason == 'master_off':
            db.scalar(select(AutomationSettings)).master_enabled = False
    if reason == 'browser_off':
        monkeypatch.setenv('BROWSER_AUTOMATION_ENABLED', 'false')
        get_settings.cache_clear()
    tick()
    with session_scope() as db:
        assert db.scalar(select(AutomationJob).where(AutomationJob.account_id == uuid.UUID(account['id']))) is None
