"""Finzola contracts and integration with mocked HTTP; never contacts a provider."""
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from app.database.session import session_scope
from app.integrations.finzola import transport
from app.integrations.finzola.contracts import MappingInput, amount_minor, expectation
from app.models.finzola import FinzolaDelivery
from app.models.system import SystemSetting
from app.models.transactions import BookEntry, Transaction
from app.workers.worker import run_pending_once
from tests.test_automation_first import _hdfc_manual_account

ROOT = '/api/v1/integrations/finzola'


def configure(client):
    response = client.put(ROOT + '/custom-api', json={'api_key': 'gateway-private-key', 'domains': ['expenses', 'finance']})
    assert response.status_code == 200, response.text
    assert 'gateway-private-key' not in response.text


def mock_gateway(monkeypatch, rows, finance=None):
    monkeypatch.setattr(transport, 'fetch', lambda domain, key: {'data': rows if domain == 'expenses' else finance or [],
        'meta': {'domain': domain, 'count': len(rows if domain == 'expenses' else finance or [])}})


def expense(**changes):
    return {'id': 'exp-001', 'org_id': 'organization-one', 'status': 'approved', 'currency': 'INR',
            'net_amount': '200.00', 'due_date': '2026-01-02', 'invoice_number': 'INV001', 'title': 'Vendor payment', **changes}


def mapping(client, account):
    response = client.put(ROOT + '/custom-api/pipeline/mapping', json={'external_org_id': 'organization-one',
        'account_id': account['id'], 'amount_units': 'major'})
    assert response.status_code == 200, response.text


def fetch(client):
    response = client.post(ROOT + '/custom-api/fetch')
    assert response.status_code == 202, response.text
    run_pending_once()


@pytest.mark.parametrize('value,currency,units,expected', [('1.25','INR','major',125),('500','JPY','major',500),('1.001','BHD','major',1001),('100','INR','minor',100)])
def test_exact_money(value, currency, units, expected):
    assert amount_minor(value, currency, units) == expected


@pytest.mark.parametrize('value', ['0','-1','1.001','NaN','Infinity',True,'9007199254740992'])
def test_invalid_money(value):
    with pytest.raises(ValueError):
        amount_minor(value, 'INR', 'major')


def test_policy_blocks_ambiguity_currency_and_synthetic():
    body = MappingInput(external_org_id='organization-one', account_id=uuid.uuid4(), amount_units='major',
                        date_field='payment_date', reference_field='transaction_reference')
    m = SimpleNamespace(external_org_id=body.external_org_id, options=body.model_dump())
    with pytest.raises(ValueError, match='AMBIGUOUS_FINANCE'):
        expectation(expense(), [{'expense_id':'exp-001'}, {'expense_id':'exp-001'}], m, 'INR')
    with pytest.raises(ValueError, match='currency'):
        expectation(expense(currency='USD'), [], m, 'INR')
    with pytest.raises(ValueError, match='TEST_ONLY'):
        expectation(expense(is_test=True), [], m, 'INR')


def test_fetch_retains_previous_failed_domain_and_replaces_empty_snapshot(client, owner, monkeypatch):
    configure(client)
    mock_gateway(monkeypatch, [expense()])
    fetch(client)
    assert client.get(ROOT+'/custom-api/data/expenses').json()['total'] == 1
    def partial(domain, key):
        if domain == 'expenses':
            raise transport.RemoteError('Finzola returned HTTP 503', transient=True)
        return {'data': [], 'meta': {'domain': domain, 'count': 0}}
    monkeypatch.setattr(transport, 'fetch', partial)
    fetch(client)
    data = client.get(ROOT+'/custom-api/data/expenses').json()
    assert data['total'] == 1 and data['state']['error']
    mock_gateway(monkeypatch, [])
    fetch(client)
    assert client.get(ROOT+'/custom-api/data/expenses').json()['total'] == 0
    with session_scope() as db:
        row = db.scalar(select(SystemSetting).where(SystemSetting.key == 'finzola_gateway'))
        assert 'gateway-private-key' not in str(row.value)


def test_expectations_do_not_create_bank_transactions_and_changed_source_is_held(client, owner, monkeypatch):
    account = _hdfc_manual_account(client)
    configure(client)
    mapping(client, account)
    mock_gateway(monkeypatch, [expense()])
    fetch(client)
    state = client.get(ROOT+'/custom-api/pipeline').json()['items'][0]
    assert state['status'] == 'awaiting_bank_evidence'
    with session_scope() as db:
        assert db.scalar(select(Transaction)) is None
        book = db.scalar(select(BookEntry).where(BookEntry.source == 'finzola'))
        assert str(book.amount) == '-200.0000'
    assert client.get('/api/v1/book-entries').json()['total'] == 0  # Expectations do not become the user's ledger.
    mock_gateway(monkeypatch, [expense(net_amount='250')])
    fetch(client)
    state = client.get(ROOT+'/custom-api/pipeline').json()['items'][0]
    assert state['status'] == 'requires_attention' and 'SOURCE_CHANGED' in state['reason']
    with session_scope() as db:
        assert str(db.scalar(select(BookEntry)).amount) == '-200.0000'


def bank_statement(client, account):
    content = b'Date,Description,Reference,Debit,Credit,Balance\n2026-01-01,Opening balance,,,,1000\n2026-01-02,Vendor payment,INV001,200,,800\n2026-01-31,Closing balance,,,,800\n'
    response = client.post('/api/v1/statements/upload', data={'account_id': account['id']},
        files={'file':('bank.csv', content, 'text/csv')})
    assert response.status_code == 201, response.text
    return response.json()['statement']


def verified_destination(client, monkeypatch):
    assert client.put(ROOT+'/outbound',json={'external_org_id':'organization-one','webhook_key':'organization-private-key'}).status_code == 200
    sender = Mock(side_effect=lambda key,payload:{'ok':True,'received':payload['requestId']})
    monkeypatch.setattr(transport,'send',sender)
    assert client.post(ROOT+'/outbound/organization-one/test').status_code == 202
    run_pending_once()
    assert client.get(ROOT+'/outbound').json()['destinations'][0]['verified'] is True
    return sender


def test_reviewed_evidence_comparison_and_acknowledged_delivery(client, owner, monkeypatch):
    account = _hdfc_manual_account(client)
    configure(client)
    mapping(client, account)
    statement = bank_statement(client, account)
    mock_gateway(monkeypatch, [expense()])
    fetch(client)
    assert client.get(ROOT+'/custom-api/pipeline').json()['items'][0]['status'] == 'awaiting_bank_evidence'
    sender = verified_destination(client, monkeypatch)
    response=client.put(ROOT+'/outbound',json={'external_org_id':'organization-one','auto_delivery':True})
    assert response.status_code == 200, response.text
    assert client.post(ROOT+f"/evidence/{statement['id']}/review",json={'reason':'Checked every row against original bank export'}).status_code == 200
    run_pending_once()
    data=client.get(ROOT+'/custom-api/pipeline').json()['items'][0]
    assert data['status'] == 'reconciled', data
    delivery=client.get(ROOT+'/outbound').json()['deliveries'][0]
    assert delivery['status'] == 'delivered' and delivery['acknowledgement']['received'] == delivery['request_id']
    assert sender.call_args.args[1]['eventType'] == 'reconciliation.updated'
    assert sender.call_args.args[1]['paymentStatus'] == 'Bank comparison: reconciled'
    fetch(client)
    assert len(client.get(ROOT+'/outbound').json()['deliveries']) == 1


def test_manual_debit_send_works_with_auto_off_and_key_rotation_blocks_pending(client, owner, monkeypatch):
    account = _hdfc_manual_account(client)
    statement = bank_statement(client, account)
    assert client.post(ROOT+f"/evidence/{statement['id']}/review",json={'reason':'Reviewed original export'}).status_code == 200
    sender = verified_destination(client, monkeypatch)
    with session_scope() as db:
        transaction_id=str(db.scalar(select(Transaction)).id)
    body={'source_id':transaction_id,'external_org_id':'organization-one'}
    assert client.post(ROOT+'/outbound/transactions/preview',json=body).status_code == 200
    response=client.post(ROOT+'/outbound/transactions/send',json=body)
    assert response.status_code == 202, response.text
    delivery_id=response.json()['id']
    assert client.post(ROOT+'/outbound/transactions/send',json=body).json()['id'] == delivery_id
    client.put(ROOT+'/outbound',json={'external_org_id':'organization-one','webhook_key':'rotated-private-key'})
    run_pending_once()
    assert client.get(ROOT+'/outbound').json()['deliveries'][0]['status'] == 'superseded'
    assert sender.call_count == 1  # The no-write test only; old pending expense was not sent.


def test_wrong_acknowledgement_never_verifies(client, owner, monkeypatch):
    client.put(ROOT+'/outbound',json={'external_org_id':'organization-one','webhook_key':'organization-private-key'})
    monkeypatch.setattr(transport,'request_json',lambda *a,**k:{'ok':True,'received':'WRONG'})
    client.post(ROOT+'/outbound/organization-one/test')
    run_pending_once()
    assert client.get(ROOT+'/outbound').json()['destinations'][0]['verified'] is False


@pytest.mark.parametrize('body', [
    {'data': [], 'meta': {'domain':'WRONG','count':0}},
    {'data': [expense()], 'meta': {'domain':'expenses','count':0}},
    {'data': [expense(),expense()], 'meta': {'domain':'expenses','count':2}},
    {'data': [], 'meta': {'domain':'expenses','count':False}},
])
def test_bad_gateway_envelopes_are_rejected(body):
    from app.integrations.finzola.gateway import validate_envelope
    with pytest.raises(transport.RemoteError):
        validate_envelope(body, 'expenses')


def prepare_manual_send(client, monkeypatch):
    account = _hdfc_manual_account(client)
    statement = bank_statement(client, account)
    client.post(ROOT+f"/evidence/{statement['id']}/review",json={'reason':'Reviewed original bank export'})
    verified_destination(client, monkeypatch)
    with session_scope() as db:
        tx=db.scalar(select(Transaction))
        source_id=str(tx.id)
    response=client.post(ROOT+'/outbound/transactions/send',json={'source_id':source_id,'external_org_id':'organization-one'})
    assert response.status_code == 202, response.text
    return response.json()['id'], account


def test_transient_delivery_retries_but_does_not_mark_bank_failed(client, owner, monkeypatch):
    delivery_id, account=prepare_manual_send(client, monkeypatch)
    monkeypatch.setattr(transport,'send',Mock(side_effect=transport.RemoteError('Temporary receiver failure',transient=True)))
    run_pending_once()
    result=client.get(ROOT+'/outbound').json()['deliveries'][0]
    assert result['status']=='retry_pending' and result['attempts']==1
    assert client.get(f"/api/v1/accounts/{account['id']}").json()['last_sync_error'] is None
    monkeypatch.setattr(transport,'send',lambda key,payload:{'ok':True,'received':payload['requestId']})
    assert client.post(ROOT+f'/outbound/deliveries/{delivery_id}/retry').status_code==202
    run_pending_once()
    result=client.get(ROOT+'/outbound').json()['deliveries'][0]
    assert result['status']=='delivered' and result['attempts']==2


def test_retry_limit_and_crash_status_are_visible(client, owner, monkeypatch):
    delivery_id,_=prepare_manual_send(client, monkeypatch)
    monkeypatch.setattr(transport,'send',Mock(side_effect=transport.RemoteError('Unavailable',transient=True)))
    for attempt in range(4):
        run_pending_once()
        result=client.get(ROOT+'/outbound').json()['deliveries'][0]
        if attempt<3:
            assert result['status']=='retry_pending'
            client.post(ROOT+f'/outbound/deliveries/{delivery_id}/retry')
    assert result['status']=='failed_permanent' and result['attempts']==4
    assert client.post(ROOT+f'/outbound/deliveries/{delivery_id}/retry').status_code==409


def test_tenant_isolation_and_shared_organization_keys_are_rejected(client, owner, monkeypatch):
    from app.core.security import hash_password
    from app.models.identity import Organization,User
    from app.services.auth import login
    from app.services.seed import bootstrap_tenant,system_role
    from tests.conftest import PASSWORD
    account=_hdfc_manual_account(client)
    configure(client)
    mapping(client,account)
    mock_gateway(monkeypatch,[expense()])
    fetch(client)
    item_id=client.get(ROOT+'/custom-api/pipeline').json()['items'][0]['id']
    verified_destination(client,monkeypatch)
    with session_scope() as db:
        org=Organization(name='Other company',slug='other-finzola-company')
        db.add(org)
        db.flush()
        db.add(User(tenant_id=org.id,email='other-finance@example.com',full_name='Other Owner',
            password_hash=hash_password(PASSWORD),role_id=system_role(db,'owner').id))
        bootstrap_tenant(db,org.id)
    with session_scope() as db:
        token=login(db,email='other-finance@example.com',password=PASSWORD,ip=None,ua=None).access_token
    headers={'Authorization':f'Bearer {token}'}
    assert client.get(ROOT+'/custom-api',headers=headers).json()['configured'] is False
    assert client.get(ROOT+'/custom-api/data/expenses',headers=headers).json()['total']==0
    assert client.get(ROOT+'/custom-api/pipeline',headers=headers).json()['items']==[]
    assert client.get(ROOT+'/outbound',headers=headers).json()['destinations']==[]
    assert client.post(ROOT+f'/custom-api/pipeline/items/{item_id}/review',headers=headers,json={'reason':'Must be denied'}).status_code==404
    assert client.put(ROOT+'/outbound',headers=headers,json={'external_org_id':'different-org','webhook_key':'organization-private-key'}).status_code==409


def test_revoked_delivery_authorizer_blocks_network_send(client, owner, monkeypatch):
    from app.models.identity import User
    delivery_id,_=prepare_manual_send(client,monkeypatch)
    sender=Mock()
    monkeypatch.setattr(transport,'send',sender)
    with session_scope() as db:
        delivery=db.get(FinzolaDelivery,uuid.UUID(delivery_id))
        db.get(User,delivery.authorized_by).is_active=False
    run_pending_once()
    assert not sender.called
    with session_scope() as db:
        assert db.get(FinzolaDelivery,uuid.UUID(delivery_id)).status=='superseded'


def test_withdrawn_expense_retains_financial_history_but_blocks_new_comparison(client, owner, monkeypatch):
    account=_hdfc_manual_account(client)
    configure(client)
    mapping(client,account)
    mock_gateway(monkeypatch,[expense()])
    fetch(client)
    mock_gateway(monkeypatch,[])
    fetch(client)
    assert client.get(ROOT+'/custom-api/pipeline').json()['items'][0]['status']=='source_unavailable'
    with session_scope() as db:
        book=db.scalar(select(BookEntry))
        assert str(book.amount)=='-200.0000' and book.raw_data['finzola_validation']=='source_unavailable'


def test_gateway_key_replacement_invalidates_cache_and_control_characters_are_rejected(client, owner, monkeypatch):
    configure(client)
    mock_gateway(monkeypatch,[expense()])
    fetch(client)
    assert client.get(ROOT+'/custom-api/data/expenses').json()['total']==1
    configure(client)  # Even submitting the same key explicitly resets cached access.
    assert client.get(ROOT+'/custom-api/data/expenses').json()['total']==0
    response=client.put(ROOT+'/custom-api',json={'api_key':'private\r\nInjected: value','domains':['expenses']})
    assert response.status_code==422 and 'Injected:' not in response.text


def test_scheduled_fetch_uses_existing_master_switch_and_does_not_overlap(client, owner, monkeypatch):
    from datetime import UTC, datetime, timedelta
    from app.models.automation import AutomationJob
    from app.scheduler.scheduler import tick
    configure(client)
    client.put(ROOT+'/custom-api',json={'domains':['expenses','finance'],'polling_enabled':True,'interval_minutes':5})
    client.post('/api/v1/automation/master',json={'enabled':True})
    with session_scope() as db:
        row=db.scalar(select(SystemSetting).where(SystemSetting.key=='finzola_gateway'))
        row.value={**row.value,'next_due':(datetime.now(UTC)-timedelta(minutes=1)).isoformat()}
    tick()
    tick()
    with session_scope() as db:
        jobs=db.scalars(select(AutomationJob).where(AutomationJob.job_type=='finzola_fetch')).all()
        assert len(jobs)==1 and jobs[0].trigger=='schedule'


def test_source_change_cannot_overwrite_matched_financial_links(client, owner, monkeypatch):
    account=_hdfc_manual_account(client)
    configure(client)
    mapping(client,account)
    statement=bank_statement(client,account)
    client.post(ROOT+f"/evidence/{statement['id']}/review",json={'reason':'Original figures checked'})
    mock_gateway(monkeypatch,[expense()])
    fetch(client)
    item=client.get(ROOT+'/custom-api/pipeline').json()['items'][0]
    assert item['status']=='reconciled'
    mock_gateway(monkeypatch,[expense(net_amount='250')])
    fetch(client)
    response=client.post(ROOT+f"/custom-api/pipeline/items/{item['id']}/review",json={
        'reason':'Try replacing a previously matched amount','decision':'replace_unmatched'})
    assert response.status_code==409
    with session_scope() as db:
        assert str(db.scalar(select(BookEntry).where(BookEntry.source=='finzola')).amount)=='-200.0000'


def test_delivery_concurrency_slots_defer_without_consuming_send_attempts(client, owner, monkeypatch):
    from datetime import UTC,datetime
    from sqlalchemy import text
    from app.database.session import get_engine
    from app.integrations.finzola.outbound import DELIVERY_SLOTS
    from app.models.automation import AutomationJob
    delivery_id,_=prepare_manual_send(client,monkeypatch)
    sender=Mock(side_effect=lambda key,payload:{'ok':True,'received':payload['requestId']})
    monkeypatch.setattr(transport,'send',sender)
    with get_engine().connect() as lock:
        try:
            for key in DELIVERY_SLOTS:
                lock.execute(text('SELECT pg_advisory_lock(:k)'),{'k':key})
            lock.commit()
            run_pending_once(limit=1)
            assert not sender.called
            with session_scope() as db:
                delivery=db.get(FinzolaDelivery,uuid.UUID(delivery_id))
                job=db.get(AutomationJob,delivery.job_id)
                assert delivery.attempts==0 and job.attempts==0 and job.status=='queued'
                job.run_after=datetime.now(UTC)
        finally:
            for key in DELIVERY_SLOTS:
                lock.execute(text('SELECT pg_advisory_unlock(:k)'),{'k':key})
            lock.commit()
    run_pending_once()
    assert sender.call_count==1


def test_changed_payload_hash_blocks_network_delivery(client, owner, monkeypatch):
    delivery_id,_=prepare_manual_send(client,monkeypatch)
    sender=Mock()
    monkeypatch.setattr(transport,'send',sender)
    with session_scope() as db:
        delivery=db.get(FinzolaDelivery,uuid.UUID(delivery_id))
        delivery.payload={**delivery.payload,'amount':9999}
    run_pending_once()
    assert not sender.called
    assert client.get(ROOT+'/outbound').json()['deliveries'][0]['status']=='superseded'
