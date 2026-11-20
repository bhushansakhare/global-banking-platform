import json
from unittest.mock import Mock

import pytest

from app.integrations.finzola import transport


def fake_http(monkeypatch,status=200,body=None):
    monkeypatch.setattr(transport.socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('93.184.216.34',443))])
    monkeypatch.setattr(transport.socket,'create_connection',Mock())
    context=Mock()
    monkeypatch.setattr(transport.ssl,'create_default_context',lambda:context)
    connection=Mock()
    connection.getresponse.return_value.status=status
    connection.getresponse.return_value.read.return_value=json.dumps(body or {'ok':True,'received':'EXP-TEST'}).encode()
    monkeypatch.setattr(transport.http.client,'HTTPSConnection',lambda *a,**k:connection)
    return connection,context


@pytest.mark.parametrize('status,auth,transient',[(302,False,False),(401,True,False),(403,True,False),(429,False,True),(503,False,True)])
def test_transport_classifies_http_failures_without_redirecting(monkeypatch,status,auth,transient):
    connection,_=fake_http(monkeypatch,status)
    with pytest.raises(transport.RemoteError) as error:
        transport.send('private-key',{'requestId':'EXP-TEST','eventType':'test'})
    assert error.value.auth==auth and error.value.transient==transient
    assert 'private-key' not in str(error.value)
    assert connection.request.call_count==1


def test_transport_pins_public_ip_but_preserves_tls_hostname(monkeypatch):
    _,context=fake_http(monkeypatch)
    assert transport.send('private-key',{'requestId':'EXP-TEST','eventType':'test'})['received']=='EXP-TEST'
    assert context.wrap_socket.call_args.kwargs['server_hostname']=='ajlzldkyskdedjbeiazs.supabase.co'
    assert transport.socket.create_connection.call_args.args[0]==('93.184.216.34',443)


def test_private_resolution_and_oversized_or_invalid_json_are_rejected(monkeypatch):
    connection,_=fake_http(monkeypatch)
    monkeypatch.setattr(transport.socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('127.0.0.1',443))])
    with pytest.raises(transport.RemoteError,match='public'):
        transport.fetch('expenses','key')
    assert not transport.socket.create_connection.called
    connection,_=fake_http(monkeypatch)
    connection.getresponse.return_value.read.return_value=b'x'*(transport.MAX_BYTES+1)
    with pytest.raises(transport.RemoteError,match='size limit'):
        transport.fetch('expenses','key')
    connection.getresponse.return_value.read.return_value=b'not-json'
    with pytest.raises(transport.RemoteError,match='invalid JSON'):
        transport.fetch('expenses','key')
