'''Offline protocol/configuration tests; real loopback transport tests are separate.'''

import json
import urllib.error

import pytest

from resume_assistant.llm_client import (
    API_KEY_ENV_VAR,
    BASE_URL_ENV_VAR,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_SECONDS,
    MODEL_ENV_VAR,
    TIMEOUT_ENV_VAR,
    USER_AGENT,
    AgentRouterClient,
    LLMClientError,
    LLMConfig,
    LLMConfigurationError,
    LLMNetworkError,
    LLMProviderError,
    LLMResponseFormatError,
    LLMUnexpectedError,
)

SECRET_MARKER = 'SYNTHETIC_SECRET_MARKER_7a3f91'
API_KEY = 'sk-ar-test-key-not-real'


def _config(**overrides):
    base = {
        'api_key': API_KEY,
        'base_url': 'https://agentrouter.test/v1',
        'model': 'gpt-6.1-sol',
        'timeout_seconds': 30.0,
    }
    base.update(overrides)
    return LLMConfig(**base)


def _ok_document(text='{"ok": true}'):
    return {
        'status': 'completed',
        'error': None,
        'incomplete_details': None,
        'output': [
            {'type': 'reasoning', 'summary': []},
            {'type': 'message', 'role': 'assistant', 'status': 'completed', 'content': [
                {'type': 'output_text', 'text': text},
            ]},
        ]
    }


def _ok_body(text='{"ok": true}'):
    return json.dumps(_ok_document(text)).encode('utf-8')


class RecordingTransport:
    def __init__(self, status=200, body=None, error=None):
        self.status = status
        self.body = _ok_body() if body is None else body
        self.error = error
        self.calls = []

    def __call__(self, url, headers, payload, timeout):
        self.calls.append({
            'url': url,
            'headers': dict(headers),
            'payload': payload,
            'timeout': timeout,
        })
        if self.error is not None:
            raise self.error
        return self.status, self.body


# ---------------------------------------------------------------------------
# LLMConfig.from_env
# ---------------------------------------------------------------------------


def test_from_env_missing_api_key_raises_controlled_error():
    with pytest.raises(LLMConfigurationError) as failure:
        LLMConfig.from_env({})
    assert API_KEY_ENV_VAR in str(failure.value)
    assert SECRET_MARKER not in str(failure.value)


def test_from_env_blank_api_key_raises_controlled_error():
    with pytest.raises(LLMConfigurationError):
        LLMConfig.from_env({API_KEY_ENV_VAR: '   '})


def test_from_env_applies_documented_defaults():
    config = LLMConfig.from_env({API_KEY_ENV_VAR: API_KEY})
    assert config.api_key == API_KEY
    assert config.base_url == DEFAULT_BASE_URL
    assert config.model == DEFAULT_MODEL
    assert config.timeout_seconds == DEFAULT_TIMEOUT_SECONDS


def test_from_env_trims_base_url_trailing_slash():
    config = LLMConfig.from_env({
        API_KEY_ENV_VAR: API_KEY,
        BASE_URL_ENV_VAR: 'https://example.test/v1/',
    })
    assert config.base_url == 'https://example.test/v1'


def test_from_env_rejects_non_http_base_url():
    with pytest.raises(LLMConfigurationError):
        LLMConfig.from_env({API_KEY_ENV_VAR: API_KEY, BASE_URL_ENV_VAR: 'ftp://example.test'})


def test_from_env_rejects_blank_model_override():
    with pytest.raises(LLMConfigurationError):
        LLMConfig.from_env({API_KEY_ENV_VAR: API_KEY, MODEL_ENV_VAR: '  '})


@pytest.mark.parametrize('timeout_raw', ['abc', '0', '-5'])
def test_from_env_rejects_invalid_timeout(timeout_raw):
    with pytest.raises(LLMConfigurationError):
        LLMConfig.from_env({API_KEY_ENV_VAR: API_KEY, TIMEOUT_ENV_VAR: timeout_raw})


def test_from_env_accepts_numeric_timeout():
    config = LLMConfig.from_env({API_KEY_ENV_VAR: API_KEY, TIMEOUT_ENV_VAR: '2.5'})
    assert config.timeout_seconds == 2.5


def test_configuration_error_never_contains_api_key():
    with pytest.raises(LLMConfigurationError) as failure:
        LLMConfig.from_env({API_KEY_ENV_VAR: API_KEY, TIMEOUT_ENV_VAR: 'not-a-number'})
    assert API_KEY not in str(failure.value)


# ---------------------------------------------------------------------------
# AgentRouterClient.generate request shape
# ---------------------------------------------------------------------------


def test_generate_posts_documented_request_shape():
    transport = RecordingTransport()
    client = AgentRouterClient(_config(), http_post=transport)

    result = client.generate(instructions='INSTRUCTIONS', input_text='JOB TEXT')

    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call['url'] == 'https://agentrouter.test/v1/responses'
    assert call['payload'] == {
        'model': 'gpt-6.1-sol',
        'instructions': 'INSTRUCTIONS',
        'input': 'JOB TEXT',
        'stream': False,
    }
    # The integration intentionally sends no unverified tuning parameters.
    assert 'temperature' not in call['payload']
    assert 'top_p' not in call['payload']
    assert 'max_output_tokens' not in call['payload']
    assert call['headers']['Authorization'] == f'Bearer {API_KEY}'
    assert call['headers']['Content-Type'] == 'application/json'
    assert call['headers']['User-Agent'] == USER_AGENT
    assert call['timeout'] == 30.0
    assert result == '{"ok": true}'


def test_generate_joins_output_text_fragments():
    document = _ok_document()
    document['output'] = [
        {'type': 'message', 'role': 'assistant', 'status': 'completed', 'content': [
            {'type': 'output_text', 'text': 'part-one '},
            {'type': 'output_text', 'text': 'part-two'},
            {'type': 'output_text', 'text': ' part-three'},
        ]},
    ]
    transport = RecordingTransport(body=json.dumps(document).encode('utf-8'))
    client = AgentRouterClient(_config(), http_post=transport)
    assert client.generate(instructions='i', input_text='j') == 'part-one part-two part-three'


# ---------------------------------------------------------------------------
# Failure paths (sanitized)
# ---------------------------------------------------------------------------


def test_non_success_status_raises_provider_error_without_body_content():
    transport = RecordingTransport(
        status=503,
        body=f'upstream failure {SECRET_MARKER}'.encode('utf-8'),
    )
    client = AgentRouterClient(_config(), http_post=transport)
    with pytest.raises(LLMProviderError) as failure:
        client.generate(instructions='i', input_text='j')
    assert '503' in str(failure.value)
    assert SECRET_MARKER not in str(failure.value)


def test_http_error_mapped_to_provider_error_with_status_only():
    transport = RecordingTransport(error=urllib.error.HTTPError(
        'https://agentrouter.test/v1/responses', 401, SECRET_MARKER, None, None,
    ))
    client = AgentRouterClient(_config(), http_post=transport)
    with pytest.raises(LLMProviderError) as failure:
        client.generate(instructions='i', input_text='j')
    assert '401' in str(failure.value)
    assert SECRET_MARKER not in str(failure.value)
    assert failure.value.__cause__ is None


@pytest.mark.parametrize('error, expected_name', [
    (urllib.error.URLError(SECRET_MARKER), 'URLError'),
    (TimeoutError(SECRET_MARKER), 'TimeoutError'),
    (OSError(SECRET_MARKER), 'OSError'),
])
def test_transport_failures_map_to_network_error(error, expected_name):
    transport = RecordingTransport(error=error)
    client = AgentRouterClient(_config(), http_post=transport)
    with pytest.raises(LLMNetworkError) as failure:
        client.generate(instructions='i', input_text='j')
    assert expected_name in str(failure.value)
    assert SECRET_MARKER not in str(failure.value)
    assert failure.value.__cause__ is None


def test_unexpected_transport_exception_is_sanitized():
    transport = RecordingTransport(error=RuntimeError(SECRET_MARKER))
    client = AgentRouterClient(_config(), http_post=transport)
    with pytest.raises(LLMUnexpectedError) as failure:
        client.generate(instructions='i', input_text='j')
    assert 'RuntimeError' in str(failure.value)
    assert failure.value.failure_type == 'RuntimeError'
    assert not isinstance(failure.value, LLMNetworkError)
    assert SECRET_MARKER not in str(failure.value)


def test_malformed_json_body_raises_response_format_error():
    transport = RecordingTransport(body=f'not json {SECRET_MARKER}'.encode('utf-8'))
    client = AgentRouterClient(_config(), http_post=transport)
    with pytest.raises(LLMResponseFormatError) as failure:
        client.generate(instructions='i', input_text='j')
    assert SECRET_MARKER not in str(failure.value)


def test_non_utf8_body_raises_response_format_error():
    transport = RecordingTransport(body=b'\xff\xfe\x00')
    client = AgentRouterClient(_config(), http_post=transport)
    with pytest.raises(LLMResponseFormatError):
        client.generate(instructions='i', input_text='j')


def test_non_object_body_raises_response_format_error():
    transport = RecordingTransport(body=json.dumps([SECRET_MARKER]).encode('utf-8'))
    client = AgentRouterClient(_config(), http_post=transport)
    with pytest.raises(LLMResponseFormatError) as failure:
        client.generate(instructions='i', input_text='j')
    assert SECRET_MARKER not in str(failure.value)


def test_provider_error_payload_is_not_surfaced():
    document = _ok_document()
    document['error'] = {'message': SECRET_MARKER}
    body = json.dumps(document).encode('utf-8')
    transport = RecordingTransport(body=body)
    client = AgentRouterClient(_config(), http_post=transport)
    with pytest.raises(LLMProviderError) as failure:
        client.generate(instructions='i', input_text='j')
    assert SECRET_MARKER not in str(failure.value)


@pytest.mark.parametrize('output', [
    [],
    [{'type': 'message', 'role': 'assistant', 'status': 'completed', 'content': [
        {'type': 'text', 'text': 'wrong fragment type'},
    ]}],
    [{'type': 'message', 'role': 'assistant', 'status': 'completed', 'content': [
        {'type': 'output_text', 'text': ''},
    ]}],
    'not-a-list',
])
def test_responses_without_output_text_raise_response_format_error(output):
    document = _ok_document()
    document['output'] = output
    transport = RecordingTransport(body=json.dumps(document).encode('utf-8'))
    client = AgentRouterClient(_config(), http_post=transport)
    with pytest.raises(LLMResponseFormatError):
        client.generate(instructions='i', input_text='j')


def test_client_error_from_transport_passes_through_unwrapped():
    sentinel = LLMConfigurationError('pre-configured controlled error')
    transport = RecordingTransport(error=sentinel)
    client = AgentRouterClient(_config(), http_post=transport)
    with pytest.raises(LLMConfigurationError) as failure:
        client.generate(instructions='i', input_text='j')
    assert failure.value is sentinel


def test_error_hierarchy_is_llm_client_error():
    for error_type in (
        LLMConfigurationError, LLMNetworkError, LLMProviderError, LLMResponseFormatError, LLMUnexpectedError,
    ):
        assert issubclass(error_type, LLMClientError)


# Mission 3.1.1 — structural configuration and completed-response validation.

@pytest.mark.parametrize('base_url', [
    'https://', 'https:///v1', 'ftp://example.test/v1', 'file:///v1',
    'http://example.test/v1', 'http://192.0.2.1/v1',
    'https://user:password@example.test/v1', 'https://user@example.test/v1',
    'https://@example.test/v1',
    'https://example.test/v1?route=test', 'https://example.test/v1?',
    'https://example.test/v1#fragment', 'https://example.test/v1#',
    'https://bad host.test/v1', 'https://bad_host.test/v1',
    'https://-bad.test/v1', 'https://bad-.test/v1', 'https://bad..test/v1',
    'https://999.999.999.999/v1', 'https://127.000.0.1/v1',
    'https://example.test:0/v1', 'https://example.test:-1/v1',
    'https://example.test:65536/v1', 'https://example.test:port/v1',
    'https://example.test:/v1', 'https://[::1/v1', 'https://[fe80::1%25zone]/v1',
    'https://example.test/v1/../other', 'https://example.test/v1/./other',
    'https://example.test/v1//other', 'https://example.test/v1//',
    'https://example.test/v1/%2e%2e', 'https://example.test/v1/responses',
    'https://example.test/v1\\other', 'https://example.test/\nv1',
], ids=lambda value: 'invalid-url-' + str(len(value)))
def test_invalid_base_urls_rejected_in_both_config_entry_points(base_url):
    for build in (
        lambda: LLMConfig(api_key=API_KEY, base_url=base_url),
        lambda: LLMConfig.from_env({API_KEY_ENV_VAR: API_KEY, BASE_URL_ENV_VAR: base_url}),
    ):
        with pytest.raises(LLMConfigurationError) as failure:
            build()
        assert API_KEY not in str(failure.value)
        assert base_url not in str(failure.value)


@pytest.mark.parametrize('base_url, expected', [
    ('https://EXAMPLE.test/v1/', 'https://example.test/v1/responses'),
    ('https://example.test/gateway/v1', 'https://example.test/gateway/v1/responses'),
    ('https://example.test:8443/v1', 'https://example.test:8443/v1/responses'),
    ('https://example.test', 'https://example.test/responses'),
    ('http://127.0.0.1:8080/v1', 'http://127.0.0.1:8080/v1/responses'),
    ('http://[::1]:8080/v1/', 'http://[::1]:8080/v1/responses'),
])
def test_valid_endpoint_construction(base_url, expected):
    transport = RecordingTransport()
    config = LLMConfig(api_key=API_KEY, base_url=base_url)
    AgentRouterClient(config, http_post=transport).generate(instructions='i', input_text='j')
    assert config.endpoint == expected
    assert transport.calls[0]['url'] == expected


@pytest.mark.parametrize('value', ['nan', 'NaN', 'inf', '-inf', 'Infinity', '1e309', ''])
def test_nonfinite_or_blank_environment_timeout_is_rejected(value):
    with pytest.raises(LLMConfigurationError, match='finite positive'):
        LLMConfig.from_env({API_KEY_ENV_VAR: API_KEY, TIMEOUT_ENV_VAR: value})


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf'), 0, -1, True, '30', 2 ** 2048],
                         ids=['nan', 'infinity', 'negative-infinity', 'zero', 'negative', 'boolean', 'string', 'overflowing-integer'])
def test_constructor_timeout_invariants(value):
    with pytest.raises(LLMConfigurationError, match='finite positive'):
        LLMConfig(api_key=API_KEY, timeout_seconds=value)


@pytest.mark.parametrize('field, value', [
    ('api_key', ''), ('api_key', '  '), ('api_key', 'token\ninjected'),
    ('model', ''), ('model', '  '), ('model', 'model\ninjected'),
])
def test_config_rejects_blank_or_control_character_values(field, value):
    kwargs = {'api_key': API_KEY, field: value}
    with pytest.raises(LLMConfigurationError):
        LLMConfig(**kwargs)


def test_config_repr_does_not_expose_credentials():
    assert API_KEY not in repr(_config())


@pytest.mark.parametrize('status', ['incomplete', 'failed', 'cancelled', 'in_progress', 'queued'])
def test_non_completed_top_level_status_rejected(status):
    document = _ok_document(SECRET_MARKER)
    document['status'] = status
    client = AgentRouterClient(_config(), http_post=RecordingTransport(body=json.dumps(document).encode()))
    with pytest.raises(LLMProviderError, match='not fully completed') as failure:
        client.generate(instructions='i', input_text='j')
    assert SECRET_MARKER not in str(failure.value)


@pytest.mark.parametrize('field', ['error', 'incomplete_details'])
@pytest.mark.parametrize('value', [{'message': SECRET_MARKER}, {}, False, 0, ''])
def test_non_null_failure_details_rejected_even_when_falsy(field, value):
    document = _ok_document()
    document[field] = value
    client = AgentRouterClient(_config(), http_post=RecordingTransport(body=json.dumps(document).encode()))
    with pytest.raises(LLMProviderError) as failure:
        client.generate(instructions='i', input_text='j')
    assert SECRET_MARKER not in str(failure.value)


@pytest.mark.parametrize('mixed', [False, True])
def test_refusal_content_is_never_salvaged(mixed):
    document = _ok_document()
    content = document['output'][1]['content']
    if not mixed:
        content.clear()
    content.append({'type': 'refusal', 'refusal': SECRET_MARKER})
    client = AgentRouterClient(_config(), http_post=RecordingTransport(body=json.dumps(document).encode()))
    with pytest.raises(LLMProviderError, match='refused') as failure:
        client.generate(instructions='i', input_text='j')
    assert SECRET_MARKER not in str(failure.value)


@pytest.mark.parametrize('case', [
    'missing_status', 'missing_error', 'missing_incomplete_details', 'nontext_status',
    'output_mapping', 'output_empty', 'nondict_output', 'unknown_tool',
    'message_not_completed', 'message_wrong_role', 'content_mapping', 'content_empty',
    'nondict_fragment', 'unknown_fragment', 'nontext_fragment', 'blank_fragment',
    'mixed_invalid_fragment', 'second_message', 'summary_mapping',
    'summary_invalid_fragment', 'reasoning_content_mapping', 'reasoning_content_invalid',
    'reasoning_incomplete', 'reasoning_only',
])
def test_malformed_or_partial_envelope_fails_closed(case):
    document = _ok_document(SECRET_MARKER)
    message = document['output'][1]
    reasoning = document['output'][0]
    if case.startswith('missing_'):
        document.pop(case.removeprefix('missing_'))
    elif case == 'nontext_status':
        document['status'] = {'message': SECRET_MARKER}
    elif case == 'output_mapping':
        document['output'] = {'message': SECRET_MARKER}
    elif case == 'output_empty':
        document['output'] = []
    elif case == 'nondict_output':
        document['output'].append(SECRET_MARKER)
    elif case == 'unknown_tool':
        document['output'].append({'type': 'function_call', 'arguments': SECRET_MARKER})
    elif case == 'message_not_completed':
        message['status'] = 'incomplete'
    elif case == 'message_wrong_role':
        message['role'] = 'user'
    elif case == 'content_mapping':
        message['content'] = {'text': SECRET_MARKER}
    elif case == 'content_empty':
        message['content'] = []
    elif case == 'nondict_fragment':
        message['content'] = [SECRET_MARKER]
    elif case == 'unknown_fragment':
        message['content'][0]['type'] = 'text'
    elif case == 'nontext_fragment':
        message['content'][0]['text'] = {'message': SECRET_MARKER}
    elif case == 'blank_fragment':
        message['content'][0]['text'] = ' \n\t'
    elif case == 'mixed_invalid_fragment':
        message['content'].append({'type': 'output_text', 'text': 123})
    elif case == 'second_message':
        document['output'].append(dict(message))
    elif case == 'summary_mapping':
        reasoning['summary'] = {'text': SECRET_MARKER}
    elif case == 'summary_invalid_fragment':
        reasoning['summary'] = [{'type': 'refusal', 'text': SECRET_MARKER}]
    elif case == 'reasoning_content_mapping':
        reasoning['content'] = {'text': SECRET_MARKER}
    elif case == 'reasoning_content_invalid':
        reasoning['content'] = [{'type': 'output_text', 'text': SECRET_MARKER}]
    elif case == 'reasoning_incomplete':
        reasoning['status'] = 'in_progress'
    elif case == 'reasoning_only':
        document['output'] = [reasoning]
    else:
        raise AssertionError('Unknown synthetic case')
    client = AgentRouterClient(_config(), http_post=RecordingTransport(body=json.dumps(document).encode()))
    with pytest.raises(LLMClientError) as failure:
        client.generate(instructions='i', input_text='j')
    assert SECRET_MARKER not in str(failure.value)
    assert API_KEY not in str(failure.value)


def test_completed_reasoning_metadata_is_not_returned_as_analysis_text():
    document = _ok_document()
    document['output'][0]['summary'] = [{'type': 'summary_text', 'text': SECRET_MARKER}]
    document['output'][0]['content'] = [{'type': 'reasoning_text', 'text': SECRET_MARKER}]
    client = AgentRouterClient(_config(), http_post=RecordingTransport(body=json.dumps(document).encode()))
    assert client.generate(instructions='i', input_text='j') == '{"ok": true}'


def test_duplicate_provider_json_keys_are_rejected():
    payload = json.dumps(_ok_document())
    duplicate = '{"status":"failed",' + payload[1:]
    client = AgentRouterClient(_config(), http_post=RecordingTransport(body=duplicate.encode()))
    with pytest.raises(LLMResponseFormatError, match='malformed JSON'):
        client.generate(instructions='i', input_text='j')


@pytest.mark.parametrize('constant', ['NaN', 'Infinity', '-Infinity'])
def test_nonstandard_provider_json_constants_are_rejected(constant):
    payload = json.dumps(_ok_document())
    invalid = '{"unused_metadata":' + constant + ',' + payload[1:]
    client = AgentRouterClient(_config(), http_post=RecordingTransport(body=invalid.encode()))
    with pytest.raises(LLMResponseFormatError, match='malformed JSON'):
        client.generate(instructions='i', input_text='j')


@pytest.mark.parametrize('status', [SECRET_MARKER, True, None])
def test_non_integer_transport_status_is_sanitized(status):
    client = AgentRouterClient(_config(), http_post=RecordingTransport(status=status))
    with pytest.raises(LLMResponseFormatError, match='invalid HTTP status') as failure:
        client.generate(instructions='i', input_text='j')
    assert SECRET_MARKER not in str(failure.value)
