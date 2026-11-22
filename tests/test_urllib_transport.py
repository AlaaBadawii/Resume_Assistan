'''Real urllib integration against local synthetic HTTP/TLS servers only.'''

import json
import shutil
import ssl
import subprocess
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from resume_assistant.llm_client import AgentRouterClient, LLMConfig, LLMNetworkError, LLMProviderError

TEST_TOKEN = 'synthetic-local-test-token'
TEST_INPUT = 'SYNTHETIC_LOCAL_JOB_INPUT'
ERROR_MARKER = 'SYNTHETIC_REDIRECT_BODY_MARKER'


@contextmanager
def _server(*, location=None, status=302, tls=None, delay=0):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            requests.append({
                'method': self.command, 'path': self.path,
                'authorization': self.headers.get('Authorization'),
                'content_type': self.headers.get('Content-Type'), 'body': body,
            })
            if delay:
                time.sleep(delay)
            if location is not None:
                code, payload = status, ERROR_MARKER.encode()
            else:
                code = 200
                payload = json.dumps({
                    'status': 'completed', 'error': None, 'incomplete_details': None,
                    'output': [{'type': 'message', 'role': 'assistant', 'status': 'completed',
                                'content': [{'type': 'output_text', 'text': '{"synthetic":true}'}]}],
                }).encode()
            self.send_response(code)
            if location is not None:
                self.send_header('Location', location)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass  # normal client disconnect after a rejected redirect/timeout

        do_POST = handle_request
        do_GET = handle_request

        def log_message(self, format, *args):
            pass  # test server does not log request headers or source text

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    if tls is not None:
        server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture(autouse=True)
def loopback_environment(monkeypatch):
    # A developer's outbound proxy must never turn these local tests into
    # external traffic. No actual provider URL is used anywhere in this file.
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('NO_PROXY', '127.0.0.1,localhost,::1')
    monkeypatch.setenv('no_proxy', '127.0.0.1,localhost,::1')


@pytest.mark.parametrize('status', [301, 302, 303, 307, 308])
def test_actual_transport_rejects_cross_host_redirect_without_forwarding_token(status):
    with _server() as (destination_port, destination_requests):
        location = f'http://localhost:{destination_port}/capture'
        with _server(location=location, status=status) as (source_port, source_requests):
            client = AgentRouterClient(LLMConfig(
                api_key=TEST_TOKEN, base_url=f'http://127.0.0.1:{source_port}/v1',
            ))
            with pytest.raises(LLMProviderError) as failure:
                client.generate(instructions='synthetic trusted instructions', input_text=TEST_INPUT)
            assert str(status) in str(failure.value)
            assert TEST_TOKEN not in str(failure.value)
            assert TEST_INPUT not in str(failure.value)
            assert ERROR_MARKER not in str(failure.value)
            assert len(source_requests) == 1
            assert source_requests[0]['method'] == 'POST'
            assert source_requests[0]['authorization'] == 'Bearer ' + TEST_TOKEN
            assert destination_requests == []


def test_actual_transport_also_rejects_same_origin_redirect():
    with _server(location='/capture') as (port, requests):
        client = AgentRouterClient(LLMConfig(api_key=TEST_TOKEN, base_url=f'http://127.0.0.1:{port}/v1'))
        with pytest.raises(LLMProviderError, match='HTTP 302'):
            client.generate(instructions='i', input_text=TEST_INPUT)
        assert len(requests) == 1
        assert requests[0]['method'] == 'POST'
        assert requests[0]['path'] == '/v1/responses'


@pytest.fixture(scope='module')
def synthetic_tls_certificate(tmp_path_factory):
    if shutil.which('openssl') is None:
        pytest.skip('openssl is required to generate a temporary local TLS certificate')
    directory = tmp_path_factory.mktemp('synthetic-local-tls')
    key, certificate = directory / 'test-key.pem', directory / 'test-certificate.pem'
    subprocess.run([
        'openssl', 'req', '-x509', '-nodes', '-newkey', 'rsa:2048',
        '-keyout', str(key), '-out', str(certificate), '-days', '1',
        '-subj', '/CN=localhost', '-addext', 'subjectAltName=IP:127.0.0.1,DNS:localhost',
        '-addext', 'basicConstraints=critical,CA:TRUE',
    ], check=True, capture_output=True)
    return key, certificate


def test_actual_https_transport_rejects_http_downgrade(
    synthetic_tls_certificate, monkeypatch,
):
    key, certificate = synthetic_tls_certificate
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(certificate, key)
    # Add a synthetic test CA through normal default SSL configuration;
    # production certificate verification is not disabled or overridden.
    monkeypatch.setenv('SSL_CERT_FILE', str(certificate))
    with _server() as (destination_port, destination_requests):
        location = f'http://127.0.0.1:{destination_port}/capture'
        with _server(location=location, tls=tls) as (source_port, source_requests):
            client = AgentRouterClient(LLMConfig(
                api_key=TEST_TOKEN, base_url=f'https://127.0.0.1:{source_port}/v1',
            ))
            with pytest.raises(LLMProviderError, match='HTTP 302') as failure:
                client.generate(instructions='i', input_text=TEST_INPUT)
            assert TEST_TOKEN not in str(failure.value)
            assert ERROR_MARKER not in str(failure.value)
            assert len(source_requests) == 1
            assert source_requests[0]['method'] == 'POST'
            assert destination_requests == []


def test_actual_https_transport_rejects_cross_host_https_redirect(
    synthetic_tls_certificate, monkeypatch,
):
    key, certificate = synthetic_tls_certificate
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(certificate, key)
    monkeypatch.setenv('SSL_CERT_FILE', str(certificate))
    with _server(tls=tls) as (destination_port, destination_requests):
        location = f'https://localhost:{destination_port}/capture'
        with _server(location=location, tls=tls) as (source_port, source_requests):
            client = AgentRouterClient(LLMConfig(
                api_key=TEST_TOKEN, base_url=f'https://127.0.0.1:{source_port}/v1',
            ))
            with pytest.raises(LLMProviderError, match='HTTP 302') as failure:
                client.generate(instructions='i', input_text=TEST_INPUT)
            assert TEST_TOKEN not in str(failure.value)
            assert len(source_requests) == 1
            assert source_requests[0]['method'] == 'POST'
            assert destination_requests == []


def test_actual_transport_posts_expected_json_and_returns_completed_output():
    with _server() as (port, requests):
        client = AgentRouterClient(LLMConfig(api_key=TEST_TOKEN, base_url=f'http://127.0.0.1:{port}/v1'))
        assert client.generate(instructions='trusted instructions', input_text=TEST_INPUT) == '{"synthetic":true}'
        assert len(requests) == 1
        assert requests[0]['content_type'] == 'application/json'
        assert json.loads(requests[0]['body']) == {
            'model': 'gpt-6.1-sol', 'instructions': 'trusted instructions',
            'input': TEST_INPUT, 'stream': False,
        }


def test_actual_transport_timeout_is_sanitized():
    with _server(delay=0.2) as (port, requests):
        client = AgentRouterClient(LLMConfig(
            api_key=TEST_TOKEN, base_url=f'http://127.0.0.1:{port}/v1', timeout_seconds=0.02,
        ))
        with pytest.raises(LLMNetworkError) as failure:
            client.generate(instructions='i', input_text=TEST_INPUT)
        assert TEST_TOKEN not in str(failure.value)
        assert TEST_INPUT not in str(failure.value)
