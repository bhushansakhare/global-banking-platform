"""Real encrypted synthetic PDFs, account-scoped passwords, and the durable processing pipeline."""
import uuid

import fitz
from sqlalchemy import select

from app.database.session import session_scope
from app.models.automation import AutomationJob
from app.models.system import AuditLog, SystemSetting
from app.workers.worker import run_pending_once
from tests.test_automation_first import _hdfc_manual_account


def encrypted_pdf(password='pdf-secret', month=1):
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 60), f'Bank statement\n01/{month:02d}/2026 Opening balance 1000.00\n'
            f'02/{month:02d}/2026 Bank fee -200.00 800.00\n28/{month:02d}/2026 Closing balance 800.00', fontsize=11)
        return doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw='synthetic-owner', user_pw=password)


def upload(client, account, content):
    response = client.post('/api/v1/statements/upload', data={'account_id': account['id']},
        files={'file': ('protected.pdf', content, 'application/pdf')})
    assert response.status_code == 201, response.text
    return response.json()['statement']


def test_unlock_remembers_only_for_same_account_and_continues_existing_and_future_statements(client, owner):
    first = _hdfc_manual_account(client)
    second_response = client.post('/api/v1/accounts', json={'institution_id': first['institution']['id'],
        'nickname': 'Other bank account', 'currency': 'INR', 'account_type': 'savings',
        'account_number': '50100987654321', 'country_code': 'IN'})
    assert second_response.status_code == 201
    second = second_response.json()
    original = encrypted_pdf()
    a = upload(client, first, original)
    waiting = upload(client, first, encrypted_pdf(month=2))
    foreign = upload(client, second, original)
    path = f"/api/v1/statements/{a['id']}"
    assert a['password_required'] is True and a['transaction_count'] is None
    assert client.get(path + '/preview').status_code == 422
    assert client.post(path + '/unlock', json={'password': 'WRONG'}).status_code == 422
    assert client.get(f"/api/v1/accounts/{first['id']}/statement-password").json()['password_saved'] is False
    with session_scope() as db:
        assert db.scalar(select(AutomationJob).where(AutomationJob.job_type == 'reconcile_account')) is None
    response = client.post(path + '/unlock', json={'password': 'pdf-secret'})
    assert response.status_code == 200, response.text
    assert response.json()['statements_queued'] == 1
    assert response.json()['statement']['arithmetic_valid'] is True
    assert 'pdf-secret' not in response.text
    run_pending_once()
    detail = client.get(path).json()
    assert detail['document_access']['preview_ready'] is True
    assert detail['document_access']['password_required'] is False
    assert detail['transaction_count'] == 1
    assert detail['latest_reconciliation'] is not None
    assert client.get(f"/api/v1/statements/{waiting['id']}").json()['transaction_count'] == 1
    # A future upload uses the saved account password automatically.
    future = upload(client, first, encrypted_pdf(month=3))
    assert future['transaction_count'] == 1 and future['arithmetic_valid'] is True
    # Shared original-file storage must never grant another account this password.
    assert client.get(f"/api/v1/statements/{foreign['id']}/preview").status_code == 422
    assert client.get(f"/api/v1/accounts/{second['id']}/statement-password").json()['password_saved'] is False
    preview = client.get(path + '/preview')
    assert preview.status_code == 200 and preview.headers['cache-control'] == 'no-store'
    with fitz.open(stream=preview.content, filetype='pdf') as doc:
        assert not doc.needs_pass and 'Bank fee' in doc[0].get_text()
    assert client.get(path + '/screenshot').content.startswith(b'\x89PNG')
    assert client.get(path + '/file').content == original
    with session_scope() as db:
        rows = db.scalars(select(SystemSetting).where(SystemSetting.key.startswith('statement_pdf_password:'))).all()
        assert rows and all('pdf-secret' not in str(row.value) for row in rows)
        logs = db.scalars(select(AuditLog).where(AuditLog.action == 'statement.password.save')).all()
        assert logs and all('pdf-secret' not in str(log.after) for log in logs)
        assert all('pdf-secret' not in str(job.payload) for job in db.scalars(select(AutomationJob)))


def test_changed_account_password_preserves_old_previews_and_forget_revokes_saved_access(client, owner):
    account = _hdfc_manual_account(client)
    old = upload(client, account, encrypted_pdf())
    old_path = f"/api/v1/statements/{old['id']}"
    assert client.post(old_path + '/unlock', json={'password': 'pdf-secret'}).status_code == 200
    new = upload(client, account, encrypted_pdf('new-pdf-secret', month=4))
    new_path = f"/api/v1/statements/{new['id']}"
    assert new['password_required'] is True
    assert client.post(new_path + '/unlock', json={'password': 'new-pdf-secret'}).status_code == 200
    assert client.get(old_path + '/preview').status_code == 200
    assert client.get(new_path + '/preview').status_code == 200
    response = client.delete(f"/api/v1/accounts/{account['id']}/statement-password")
    assert response.status_code == 204
    assert client.get(old_path + '/preview').status_code == 422
    assert client.get(new_path + '/preview').status_code == 422
    assert client.get(old_path + '/file').status_code == 200
    assert client.post(f'/api/v1/statements/{uuid.uuid4()}/unlock', json={'password': 'private'}).status_code == 404


def test_reconciliation_rejects_statement_from_other_account(client, owner):
    first = _hdfc_manual_account(client)
    second = client.post('/api/v1/accounts', json={'institution_id': first['institution']['id'],
        'nickname': 'Second', 'currency': 'INR', 'account_type': 'savings',
        'account_number': '50100987654321', 'country_code': 'IN'}).json()
    statement = upload(client, first, encrypted_pdf())
    assert client.post(f"/api/v1/statements/{statement['id']}/unlock", json={'password': 'pdf-secret'}).status_code == 200
    response = client.post('/api/v1/reconciliation/run', json={'account_id': second['id'], 'statement_id': statement['id']})
    assert response.status_code == 422


def test_cash_book_import_automatically_reruns_matching_and_duplicate_import_does_not(client, owner):
    account = _hdfc_manual_account(client)
    statement = upload(client, account, encrypted_pdf())
    path = f"/api/v1/statements/{statement['id']}"
    assert client.post(path + '/unlock', json={'password': 'pdf-secret'}).status_code == 200
    run_pending_once()
    assert client.get(path).json()['latest_reconciliation']['has_book_side'] is False
    book = b'Date,Description,Amount\n2026-01-02,Bank fee,-200.00\n'
    response = client.post('/api/v1/book-entries/import', data={'account_id': account['id']},
        files={'file': ('book.csv', book, 'text/csv')})
    assert response.status_code == 200, response.text
    assert response.json()['created'] == 1 and response.json()['reconciliations_queued'] == 1
    run_pending_once()
    latest = client.get(path).json()['latest_reconciliation']
    assert latest['has_book_side'] is True and latest['exception_count'] == 0
    response = client.post('/api/v1/book-entries/import', data={'account_id': account['id']},
        files={'file': ('book.csv', book, 'text/csv')})
    assert response.json()['created'] == 0 and response.json()['reconciliations_queued'] == 0


def test_invalid_extraction_and_disabled_auto_reconcile_do_not_queue_reconciliation(client, owner):
    account = _hdfc_manual_account(client)
    csv = b'Date,Description,Debit,Credit,Balance\n2026-01-01,Opening balance,,,1000\n2026-01-02,Fee,200,,800\n2026-01-31,Closing balance,,,999\n'
    response = client.post('/api/v1/statements/upload', data={'account_id': account['id']},
        files={'file': ('invalid.csv', csv, 'text/csv')})
    assert response.status_code == 201 and response.json()['statement']['processing_status'] == 'needs_review'
    with session_scope() as db:
        assert db.scalar(select(AutomationJob).where(AutomationJob.job_type == 'reconcile_account')) is None
    assert client.put('/api/v1/automation/settings', json={'auto_reconcile': False}).status_code == 200
    response = client.post('/api/v1/statements/upload', data={'account_id': account['id']},
        files={'file': ('valid.csv', csv.replace(b',,,999', b',,,800'), 'text/csv')})
    assert response.json()['statement']['arithmetic_valid'] is True
    with session_scope() as db:
        assert db.scalar(select(AutomationJob).where(AutomationJob.job_type == 'reconcile_account')) is None
