from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from app.notifications.balance_template import balance_html


def test_html_email_escapes_user_content_and_distinguishes_source_time_and_target():
    account = SimpleNamespace(nickname='<img src=x onerror=alert(1)>', account_number_masked='XXXX0127',
        timezone='Asia/Kolkata', low_balance_threshold=Decimal('25000'))
    balance = SimpleNamespace(amount=Decimal('1250'), currency='INR', balance_type='available',
        source='browser', as_of=datetime(2026, 11, 20, 7, 0, tzinfo=UTC))
    html = balance_html(account, balance, below_target=True)
    assert '<img src=x' not in html and '&lt;img' in html
    assert 'INR 1,250.00' in html and 'INR 25,000.00' in html and 'INR 23,750.00' in html
    assert '12:30 PM IST' in html and 'Observed on your bank website' in html
    assert 'Segoe UI' in html and 'role="presentation"' in html
    assert '<script' not in html and 'https://' not in html
    balance.amount, balance.balance_type = Decimal('25000'), 'statement_closing'
    html = balance_html(account, balance, below_target=False)
    assert 'TARGET MET' in html and 'not a live balance' in html and 'BELOW YOUR TARGET' not in html
