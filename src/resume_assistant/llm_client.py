'''Minimal stdlib HTTP client for Responses, Chat Completions, and Gemini APIs.

This module is intentionally isolated from job-description business logic:
it only knows how to build an HTTP request for the configured provider and
translate configuration/network/provider failures into sanitized exception
types. Exception messages never contain credentials, prompts, input text,
or provider response bodies.
'''

import json
import ipaddress
import math
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Dict, Literal, Mapping, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

ProviderName = Literal['responses', 'chat_completions', 'gemini']

VALID_PROVIDERS: Tuple[ProviderName, ...] = (
    'responses',
    'chat_completions',
    'gemini',
)
DEFAULT_PROVIDER: ProviderName = 'responses'

DEFAULT_BASE_URL = 'https://agentrouter.org/v1'
DEFAULT_MODEL = 'gpt-6.1-sol'
DEFAULT_CHAT_COMPLETIONS_BASE_URL = 'https://api.openai.com/v1'
DEFAULT_CHAT_COMPLETIONS_MODEL = 'gpt-4o-mini'
DEFAULT_GEMINI_BASE_URL = 'https://generativelanguage.googleapis.com/v1beta'
DEFAULT_GEMINI_MODEL = 'gemini-2.5-flash'
DEFAULT_TIMEOUT_SECONDS = 60.0

PROVIDER_ENV_VAR = 'LLM_PROVIDER'
API_KEY_ENV_VAR = 'AGENTROUTER_API_KEY'
BASE_URL_ENV_VAR = 'AGENTROUTER_BASE_URL'
MODEL_ENV_VAR = 'AGENTROUTER_MODEL'
TIMEOUT_ENV_VAR = 'AGENTROUTER_TIMEOUT_SECONDS'

LLM_API_KEY_ENV_VAR = 'LLM_API_KEY'
OPENAI_API_KEY_ENV_VAR = 'OPENAI_API_KEY'
LLM_BASE_URL_ENV_VAR = 'LLM_BASE_URL'
LLM_MODEL_ENV_VAR = 'LLM_MODEL'
LLM_TIMEOUT_ENV_VAR = 'LLM_TIMEOUT_SECONDS'
GEMINI_API_KEY_ENV_VAR = 'GEMINI_API_KEY'
GEMINI_BASE_URL_ENV_VAR = 'GEMINI_BASE_URL'
GEMINI_MODEL_ENV_VAR = 'GEMINI_MODEL'
COMMON_MODEL_ENV_VAR = 'MODEL'

USER_AGENT = 'resume-assistant/0.1'
_GEMINI_MODEL_PATTERN = re.compile(r'^[A-Za-z0-9._-]+$')


class LLMClientError(Exception):
    '''Base class for sanitized LLM client failures.'''


class LLMConfigurationError(LLMClientError):
    '''Controlled, content-free configuration error.'''


class LLMNetworkError(LLMClientError):
    '''Network-level failure; the message reports only the failure class.'''


class LLMProviderError(LLMClientError):
    '''Provider rejected or failed the request; only the HTTP status is reported.'''

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class LLMResponseFormatError(LLMClientError):
    '''Provider response could not be interpreted; contents are never included.'''


class LLMUnexpectedError(LLMClientError):
    '''Unexpected transport implementation failure, distinct from connection errors.'''

    def __init__(self, failure_type: str) -> None:
        self.failure_type = failure_type
        super().__init__(f'provider transport failed unexpectedly ({failure_type}).')


HttpPost = Callable[[str, Dict[str, str], Dict, float], Tuple[int, bytes]]


def _normalize_base_url(value: str) -> str:
    '''HTTPS by default; HTTP is restricted to numeric loopback test servers.'''
    if not isinstance(value, str):
        raise LLMConfigurationError('AgentRouter base URL must be a URL.')
    value = value.strip()
    if not value or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        raise LLMConfigurationError('AgentRouter base URL contains invalid characters.')
    try:
        parts = urlsplit(value)
        host = parts.hostname
        port = parts.port
    except ValueError:
        raise LLMConfigurationError('AgentRouter base URL has an invalid host or port.') from None
    if parts.scheme not in ('https', 'http') or not host:
        raise LLMConfigurationError('AgentRouter base URL must have an http(s) scheme and hostname.')
    if parts.username is not None or parts.password is not None or '?' in value or '#' in value:
        raise LLMConfigurationError('AgentRouter base URL must not contain credentials, queries, or fragments.')
    if (port is not None and not 1 <= port <= 65535) or parts.netloc.endswith(':'):
        raise LLMConfigurationError('AgentRouter base URL has an invalid port.')
    address = None
    try:
        address = ipaddress.ip_address(host)
        normalized_host = address.compressed
    except ValueError:
        try:
            normalized_host = host.encode('idna').decode('ascii').lower()
        except UnicodeError:
            raise LLMConfigurationError('AgentRouter base URL has an invalid hostname.') from None
        labels = normalized_host.rstrip('.').split('.')
        if (
            len(normalized_host) > 253
            or normalized_host.replace('.', '').isdigit()
            or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels)
        ):
            raise LLMConfigurationError('AgentRouter base URL has an invalid hostname.')
    if '%' in host or (parts.scheme == 'http' and (address is None or not address.is_loopback)):
        raise LLMConfigurationError('AgentRouter requires HTTPS except for numeric loopback HTTP servers.')
    if '//' in parts.path:
        raise LLMConfigurationError('AgentRouter base URL has an invalid API base path.')
    path = parts.path.rstrip('/')
    if path:
        components = path[1:].split('/')
        if (
            not path.startswith('/')
            or any(component in ('', '.', '..') or not re.fullmatch(r'[A-Za-z0-9._~-]+', component) for component in components)
            or components[-1] == 'responses'
            or components[-2:] == ['chat', 'completions']
        ):
            raise LLMConfigurationError('AgentRouter base URL must contain a valid API base path, not an endpoint.')
    authority = f'[{normalized_host}]' if address is not None and address.version == 6 else normalized_host
    if port is not None:
        authority += f':{port}'
    return urlunsplit((parts.scheme, authority, path, '', ''))


def _default_base_url_for(provider: ProviderName) -> str:
    return {
        'responses': DEFAULT_BASE_URL,
        'chat_completions': DEFAULT_CHAT_COMPLETIONS_BASE_URL,
        'gemini': DEFAULT_GEMINI_BASE_URL,
    }[provider]


def _default_model_for(provider: ProviderName) -> str:
    return {
        'responses': DEFAULT_MODEL,
        'chat_completions': DEFAULT_CHAT_COMPLETIONS_MODEL,
        'gemini': DEFAULT_GEMINI_MODEL,
    }[provider]


def _missing_key_error_for(provider: ProviderName) -> str:
    env_name = {
        'responses': API_KEY_ENV_VAR,
        'chat_completions': LLM_API_KEY_ENV_VAR,
        'gemini': GEMINI_API_KEY_ENV_VAR,
    }[provider]
    return f'{env_name} is not configured.'


def _first_present(env: Mapping[str, str], *names: str, default: str = '') -> str:
    for name in names:
        if name in env:
            return env[name]
    return default


@dataclass(frozen=True)
class LLMConfig:
    '''Runtime settings for Responses, Chat Completions, and Gemini APIs.'''

    api_key: str = field(repr=False)
    base_url: Optional[str] = None
    model: Optional[str] = None
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    provider: ProviderName = DEFAULT_PROVIDER

    def __post_init__(self) -> None:
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise LLMConfigurationError(
                'LLM provider must be one of: responses, chat_completions, gemini.'
            )
        provider = self.provider.strip().lower()
        if provider not in VALID_PROVIDERS:
            raise LLMConfigurationError(
                'LLM provider must be one of: responses, chat_completions, gemini.'
            )
        if not isinstance(self.api_key, str) or not self.api_key.strip():
            raise LLMConfigurationError(_missing_key_error_for(provider))
        key = self.api_key.strip()
        if not key.isascii() or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in key):
            raise LLMConfigurationError('AgentRouter API key has invalid characters.')

        raw_model = _default_model_for(provider) if self.model is None else self.model
        if not isinstance(raw_model, str) or not raw_model.strip():
            raise LLMConfigurationError('AgentRouter model must not be empty.')
        model = raw_model.strip()
        if not model.isascii() or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in model):
            raise LLMConfigurationError('AgentRouter model has invalid characters.')
        if provider == 'gemini' and (
            model in ('.', '..') or not _GEMINI_MODEL_PATTERN.fullmatch(model)
        ):
            raise LLMConfigurationError('Gemini model name contains invalid characters.')

        raw_base_url = _default_base_url_for(provider) if self.base_url is None else self.base_url

        if type(self.timeout_seconds) not in (int, float):
            raise LLMConfigurationError('AgentRouter timeout must be a finite positive number.')
        try:
            timeout = float(self.timeout_seconds)
        except (ValueError, OverflowError):
            raise LLMConfigurationError('AgentRouter timeout must be a finite positive number.') from None
        if not math.isfinite(timeout) or timeout <= 0:
            raise LLMConfigurationError('AgentRouter timeout must be a finite positive number.')

        object.__setattr__(self, 'provider', provider)
        object.__setattr__(self, 'api_key', key)
        object.__setattr__(self, 'model', model)
        object.__setattr__(self, 'base_url', _normalize_base_url(raw_base_url))
        object.__setattr__(self, 'timeout_seconds', timeout)

    @property
    def endpoint(self) -> str:
        if self.provider == 'chat_completions':
            return f'{self.base_url}/chat/completions'
        if self.provider == 'gemini':
            return f'{self.base_url}/models/{self.model}:generateContent'
        return f'{self.base_url}/responses'

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> 'LLMConfig':
        env = os.environ if environ is None else environ
        raw_provider = env.get(PROVIDER_ENV_VAR)
        if raw_provider is None:
            provider: ProviderName = DEFAULT_PROVIDER
        elif not isinstance(raw_provider, str) or not raw_provider.strip():
            raise LLMConfigurationError(
                'LLM provider must be one of: responses, chat_completions, gemini.'
            )
        else:
            normalized_provider = raw_provider.strip().lower()
            if normalized_provider not in VALID_PROVIDERS:
                raise LLMConfigurationError(
                    'LLM provider must be one of: responses, chat_completions, gemini.'
                )
            provider = normalized_provider  # type: ignore[assignment]

        if provider == 'responses':
            api_key = env.get(API_KEY_ENV_VAR, '')
            base_url = env.get(BASE_URL_ENV_VAR, DEFAULT_BASE_URL)
            model = _first_present(env, MODEL_ENV_VAR, COMMON_MODEL_ENV_VAR, default=DEFAULT_MODEL)
            raw_timeout = env.get(TIMEOUT_ENV_VAR, DEFAULT_TIMEOUT_SECONDS)
        elif provider == 'chat_completions':
            api_key = _first_present(
                env, LLM_API_KEY_ENV_VAR, OPENAI_API_KEY_ENV_VAR, API_KEY_ENV_VAR, default='',
            )
            base_url = _first_present(
                env, LLM_BASE_URL_ENV_VAR, BASE_URL_ENV_VAR,
                default=DEFAULT_CHAT_COMPLETIONS_BASE_URL,
            )
            model = _first_present(
                env, LLM_MODEL_ENV_VAR, COMMON_MODEL_ENV_VAR,
                default=DEFAULT_CHAT_COMPLETIONS_MODEL,
            )
            raw_timeout = _first_present(
                env, LLM_TIMEOUT_ENV_VAR, TIMEOUT_ENV_VAR,
                default=str(DEFAULT_TIMEOUT_SECONDS),
            )
        else:
            api_key = _first_present(env, GEMINI_API_KEY_ENV_VAR, LLM_API_KEY_ENV_VAR, default='')
            base_url = _first_present(env, GEMINI_BASE_URL_ENV_VAR, default=DEFAULT_GEMINI_BASE_URL)
            model = _first_present(
                env, GEMINI_MODEL_ENV_VAR, LLM_MODEL_ENV_VAR, COMMON_MODEL_ENV_VAR,
                default=DEFAULT_GEMINI_MODEL,
            )
            raw_timeout = _first_present(
                env, LLM_TIMEOUT_ENV_VAR, TIMEOUT_ENV_VAR,
                default=str(DEFAULT_TIMEOUT_SECONDS),
            )

        try:
            timeout_seconds = float(raw_timeout)
        except (ValueError, TypeError, OverflowError):
            raise LLMConfigurationError('AgentRouter timeout must be a finite positive number.') from None

        return cls(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout_seconds=timeout_seconds,
            provider=provider,
        )


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        # Reject all redirects, including same-origin redirects. Never convert
        # POST to GET or construct a destination request carrying credentials.
        raise urllib.error.HTTPError(request.full_url, code, 'API redirects are not permitted.', headers, fp)


def _urllib_post(url: str, headers: Dict[str, str], payload: Dict, timeout: float) -> Tuple[int, bytes]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers=headers,
        method='POST',
    )
    opener = urllib.request.build_opener(_RejectRedirects())
    with opener.open(request, timeout=timeout) as response:
        return response.getcode(), response.read()


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key.')
        result[key] = value
    return result


def _reject_json_constant(value):
    raise ValueError('Non-standard JSON constant.')


class AgentRouterClient:
    '''Calls the configured Responses, Chat Completions, or Gemini HTTP API.'''

    def __init__(self, config: LLMConfig, http_post: Optional[HttpPost] = None) -> None:
        self._config = config
        self._http_post = http_post or _urllib_post

    @property
    def model(self) -> str:
        return self._config.model

    def _build_request(self, *, instructions: str, input_text: str) -> Tuple[str, Dict[str, str], Dict]:
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'User-Agent': USER_AGENT,
        }
        if self._config.provider == 'chat_completions':
            headers['Authorization'] = f'Bearer {self._config.api_key}'
            payload: Dict = {
                'model': self._config.model,
                'messages': [
                    {'role': 'system', 'content': instructions},
                    {'role': 'user', 'content': input_text},
                ],
                'stream': False,
            }
        elif self._config.provider == 'gemini':
            headers['x-goog-api-key'] = self._config.api_key
            payload = {
                'system_instruction': {'parts': [{'text': instructions}]},
                'contents': [{'role': 'user', 'parts': [{'text': input_text}]}],
            }
        else:
            headers['Authorization'] = f'Bearer {self._config.api_key}'
            payload = {
                'model': self._config.model,
                'instructions': instructions,
                'input': input_text,
                'stream': False,
            }
        return self._config.endpoint, headers, payload

    def generate(self, *, instructions: str, input_text: str) -> str:
        '''Send instructions plus input text; return the model output text.

        Only the caller-supplied instructions and input text are placed in the
        request payload. Tuning parameters are omitted; their live-provider
        compatibility has not been verified.
        '''
        url, headers, payload = self._build_request(
            instructions=instructions, input_text=input_text
        )
        try:
            status, body = self._http_post(url, headers, payload, self._config.timeout_seconds)
        except LLMClientError:
            raise
        except urllib.error.HTTPError as error:
            code = error.code
            error.close()
            if type(code) is not int:
                raise LLMProviderError('provider request failed with an invalid HTTP status.') from None
            raise LLMProviderError(f'provider request failed with HTTP {code}.', status_code=code) from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise LLMNetworkError(f'provider request failed ({type(error).__name__}).') from None
        except Exception as error:  # noqa: BLE001 - sanitize unexpected transport failures
            raise LLMUnexpectedError(type(error).__name__) from None

        if type(status) is not int:
            raise LLMResponseFormatError('provider returned an invalid HTTP status.')
        if not 200 <= status < 300:
            raise LLMProviderError(f'provider request failed with HTTP {status}.', status_code=status)

        if not isinstance(body, bytes):
            raise LLMResponseFormatError('provider returned a malformed JSON response.')
        try:
            document = json.loads(
                body.decode('utf-8'), object_pairs_hook=_unique_json_object,
                parse_constant=_reject_json_constant,
            )
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise LLMResponseFormatError('provider returned a malformed JSON response.') from None
        if not isinstance(document, dict):
            raise LLMResponseFormatError('provider returned an unexpected response shape.')

        if self._config.provider == 'chat_completions':
            return self._parse_chat_completions_document(document)
        if self._config.provider == 'gemini':
            return self._parse_gemini_document(document)
        return self._parse_responses_document(document)

    def _parse_responses_document(self, document: Dict) -> str:
        if not {'status', 'error', 'incomplete_details', 'output'} <= document.keys():
            raise LLMResponseFormatError('provider response is missing required envelope fields.')
        if document.get('error') is not None:
            # Provider message text is deliberately not surfaced: it may echo
            # request contents.
            raise LLMProviderError('provider reported an error for the request.')
        if not isinstance(document.get('status'), str):
            raise LLMResponseFormatError('provider response has no valid completion status.')
        if document['status'] != 'completed' or document.get('incomplete_details') is not None:
            raise LLMProviderError('provider response was not fully completed.')
        text = self._extract_output_text(document)
        if not text:
            raise LLMResponseFormatError('provider response contained no output text.')
        return text

    @staticmethod
    def _parse_chat_completions_document(document: Dict) -> str:
        if document.get('error') is not None:
            raise LLMProviderError('provider reported an error for the request.')
        choices = document.get('choices')
        if not isinstance(choices, list) or not choices:
            raise LLMResponseFormatError('provider returned an unexpected choices structure.')
        choice = choices[0]
        if not isinstance(choice, dict):
            raise LLMResponseFormatError('provider returned an unexpected choice structure.')
        message = choice.get('message')
        if not isinstance(message, dict):
            raise LLMResponseFormatError('provider returned an unexpected message structure.')
        if message.get('refusal') is not None:
            raise LLMProviderError('provider refused the request.')
        finish_reason = choice.get('finish_reason')
        if not isinstance(finish_reason, str):
            raise LLMResponseFormatError('provider response has no valid finish reason.')
        if finish_reason == 'content_filter':
            raise LLMProviderError('provider refused the request.')
        if finish_reason != 'stop':
            raise LLMProviderError('provider response was not fully completed.')
        if 'role' in message and message.get('role') != 'assistant':
            raise LLMResponseFormatError('provider returned an unexpected message role.')
        content = message.get('content')
        if not isinstance(content, str) or not content.strip():
            raise LLMResponseFormatError('provider response contained no output text.')
        return content

    @staticmethod
    def _parse_gemini_document(document: Dict) -> str:
        if document.get('error') is not None:
            raise LLMProviderError('provider reported an error for the request.')
        prompt_feedback = document.get('promptFeedback')
        if prompt_feedback is not None:
            if not isinstance(prompt_feedback, dict):
                raise LLMResponseFormatError('provider returned an unexpected prompt feedback structure.')
            if prompt_feedback.get('blockReason') is not None:
                raise LLMProviderError('provider blocked the request.')
        candidates = document.get('candidates')
        if not isinstance(candidates, list) or not candidates:
            raise LLMResponseFormatError('provider returned an unexpected candidates structure.')
        candidate = candidates[0]
        if not isinstance(candidate, dict):
            raise LLMResponseFormatError('provider returned an unexpected candidate structure.')
        finish_reason = candidate.get('finishReason')
        if not isinstance(finish_reason, str):
            raise LLMResponseFormatError('provider response has no valid finish reason.')
        if finish_reason != 'STOP':
            raise LLMProviderError('provider response was not fully completed.')
        content = candidate.get('content')
        if not isinstance(content, dict):
            raise LLMResponseFormatError('provider returned an unexpected content structure.')
        parts = content.get('parts')
        if not isinstance(parts, list) or not parts:
            raise LLMResponseFormatError('provider returned an unexpected content parts structure.')
        fragments = []
        for part in parts:
            if not isinstance(part, dict):
                raise LLMResponseFormatError('provider returned an unexpected content part structure.')
            if part.get('thought') is True:
                thought_text = part.get('text')
                if thought_text is not None and not isinstance(thought_text, str):
                    raise LLMResponseFormatError('provider returned an unexpected thought part structure.')
                continue
            value = part.get('text')
            if not isinstance(value, str) or not value.strip():
                raise LLMResponseFormatError('provider returned an invalid output text fragment.')
            fragments.append(value)
        text = ''.join(fragments)
        if not text.strip():
            raise LLMResponseFormatError('provider response contained no output text.')
        return text

    @staticmethod
    def _extract_output_text(document: Dict) -> str:
        output = document.get('output')
        if not isinstance(output, list) or not output:
            raise LLMResponseFormatError('provider returned an unexpected output structure.')
        fragments = []
        message_count = 0
        for item in output:
            if not isinstance(item, dict):
                raise LLMResponseFormatError('provider returned an unexpected output structure.')
            if item.get('type') == 'reasoning':
                summary = item.get('summary')
                if not isinstance(summary, list) or item.get('status') not in (None, 'completed'):
                    raise LLMResponseFormatError('provider returned an unexpected reasoning structure.')
                for fragment in summary:
                    if (
                        not isinstance(fragment, dict)
                        or fragment.get('type') != 'summary_text'
                        or not isinstance(fragment.get('text'), str)
                        or not fragment['text'].strip()
                    ):
                        raise LLMResponseFormatError('provider returned an unexpected reasoning structure.')
                content = item.get('content')
                if content is not None:
                    if not isinstance(content, list):
                        raise LLMResponseFormatError('provider returned an unexpected reasoning structure.')
                    for fragment in content:
                        if (
                            not isinstance(fragment, dict)
                            or fragment.get('type') != 'reasoning_text'
                            or not isinstance(fragment.get('text'), str)
                            or not fragment['text'].strip()
                        ):
                            raise LLMResponseFormatError('provider returned an unexpected reasoning structure.')
                continue
            if item.get('type') != 'message':
                raise LLMResponseFormatError('provider returned an unexpected output item type.')
            message_count += 1
            if message_count != 1 or item.get('role') != 'assistant':
                raise LLMResponseFormatError('provider must return one completed assistant message.')
            if item.get('status') != 'completed':
                raise LLMProviderError('provider assistant message was not completed.')
            content = item.get('content')
            if not isinstance(content, list) or not content:
                raise LLMResponseFormatError('provider returned an unexpected message structure.')
            for fragment in content:
                if isinstance(fragment, dict) and fragment.get('type') == 'refusal':
                    raise LLMProviderError('provider refused the request.')
                if not isinstance(fragment, dict) or fragment.get('type') != 'output_text':
                    raise LLMResponseFormatError('provider returned an unexpected content structure.')
                value = fragment.get('text')
                if not isinstance(value, str) or not value.strip():
                    raise LLMResponseFormatError('provider returned an invalid output text fragment.')
                fragments.append(value)
        return ''.join(fragments)


LLMClient = AgentRouterClient
