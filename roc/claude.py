"""Explicit Anthropic Messages API calls. No tools, retries or provider fallback."""
import json
import math
import urllib.error
import urllib.request

from .common import RocError, fingerprint, read_json
from .document_ledger import ExpenseLedger
from .documents import SYSTEM, SCHEMA, classify_result, POLICY_VERSION
from .pacer import NoRedirect

MODELS = {'claude-sonnet-5-5': {'label': 'Claude Sonnet 5.5', 'input': 2, 'output': 10},
          'claude-opus-5-5': {'label': 'Claude Opus 5.5', 'input': 4, 'output': 20}}
# USD per million tokens, checked against Anthropic's official pricing 2026-10-01.
MAX_OUTPUT = 16384


def payload(source, model):
    if model not in MODELS:
        raise RocError('Choose Sonnet 5.5 or Opus 5.5.')
    evidence = {k: source[k] for k in ('caseKey', 'caseType', 'clients', 'warnings')}
    evidence['entries'] = [{k: e[k] for k in ('id', 'number', 'date', 'text')} for e in source['entries']]
    result = {'model': model, 'max_tokens': MAX_OUTPUT, 'system': SYSTEM,
              'messages': [{'role': 'user', 'content': json.dumps(evidence, ensure_ascii=False)}],
              'output_config': {'effort': 'medium', 'format': {'type': 'json_schema', 'schema': SCHEMA}}}
    if len(json.dumps(result).encode()) > 750_000:
        raise RocError('This docket exceeds the first version’s analysis size limit. No text was truncated or sent.')
    return result


def estimate(request):
    rates = MODELS[request['model']]
    # A deliberately generous local reservation, not a claimed token count.
    # Includes the schema and a protocol allowance; usage is checked afterward.
    input_bound = len(json.dumps(request).encode()) + 8192
    return math.ceil((input_bound * rates['input'] + MAX_OUTPUT * rates['output']) / 10000)


def request_claude(key, request):
    req = urllib.request.Request('https://api.anthropic.com/v1/messages', method='POST',
        data=json.dumps(request).encode(), headers={'Content-Type': 'application/json',
            'x-api-key': key, 'anthropic-version': '2023-06-01'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=120) as response:
            raw = response.read(5_000_001)
            if len(raw) > 5_000_000:
                raise RocError('Claude response exceeded the supported size. Request remains unresolved; no retry.')
            return raw.decode('utf-8')
    except urllib.error.HTTPError as exc:
        # Do not expose response bodies (may echo submitted content/credentials).
        raise RocError(f'Claude returned HTTP {exc.code}. No retry or model change. Check the AI ledger and Anthropic billing.') from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RocError('Claude connection failed. The request may have been processed; no automatic retry.') from None


def analysis_key(source, model):
    return fingerprint({'source': source, 'model': model, 'policy': POLICY_VERSION, 'prompt': SYSTEM, 'schema': SCHEMA})


def analyze(root, source, model, api_key, checkpoint=lambda: None, requester=None):
    ledger = ExpenseLedger(root, 'ai')
    key = analysis_key(source, model)
    path = root / 'responses' / (key + '.json')
    old = ledger.find(key)
    if old:
        if old['state'] != 'complete' or not path.is_file():
            raise RocError('Saved AI response is missing or unresolved; do not resubmit it.')
        response = read_json(path)
    else:
        if not api_key:
            raise RocError('The ROC account needs owner AI setup. No request submitted.')
        request = payload(source, model)
        checkpoint()
        t = ledger.reserve(key, estimate(request), {'model': model, 'caseKey': source['caseKey'],
                                                   'responseFile': str(path.relative_to(root))})
        raw = (requester or request_claude)(api_key, request)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix('.tmp'); temp.write_text(raw, encoding='utf-8'); temp.replace(path)
        try:
            response = json.loads(raw)
            usage = response['usage']
            counts = [usage['input_tokens'], usage['output_tokens']]
            if any(type(n) is not int or n < 0 for n in counts):
                raise ValueError()
            if usage.get('cache_creation_input_tokens', 0) or usage.get('cache_read_input_tokens', 0):
                raise ValueError()  # No caching was requested; unfamiliar billing stays pending.
            rates = MODELS[model]
            cost = math.ceil((counts[0]*rates['input'] + counts[1]*rates['output']) / 10000)
        except (ValueError, KeyError, TypeError):
            raise RocError('Claude usage is unreadable. Response saved; spending remains unresolved.') from None
        ledger.finish(t, cost, usage=usage, costBasis='Token usage at published standard rates, rounded up to cents')
    if response.get('stop_reason') != 'end_turn':
        raise RocError('Claude did not complete its classification. Usage saved; no automatic retry. Review the saved response.')
    try:
        text = ''.join(b['text'] for b in response['content'] if b['type'] == 'text')
        result = json.loads(text)
    except (ValueError, KeyError, TypeError):
        raise RocError('Claude returned unreadable classification JSON. Response saved; no automatic retry.') from None
    candidates = classify_result(result, source)
    return {'analysisId': key, 'model': model, 'source': source, 'candidates': candidates}
