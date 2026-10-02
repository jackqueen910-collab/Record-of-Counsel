"""Explicit search targets. Older configurations remain attorney searches."""
from html import unescape
from .common import RocError, clean

LITIGANT_STAGE = 'Litigant searches currently support the case index and exports. Docket enrichment and Document Grabber are not available for litigants yet.'


def search_type(config):
    kind = config.get('searchType', 'attorney')
    if kind not in ('attorney', 'litigant'):
        raise RocError('Choose an attorney or litigant search.')
    return kind


def subject(config):
    kind = search_type(config)
    value = config.get('lawyer' if kind == 'attorney' else 'litigant')
    if not isinstance(value, dict):
        raise RocError('Missing search name.')
    first, last = value.get('firstName', ''), value.get('lastName', '')
    if not isinstance(first, str) or not isinstance(last, str) or len(first) > 100 or len(last) > (100 if kind == 'attorney' else 200):
        raise RocError('Enter valid search names.')
    if not last.strip() or kind == 'attorney' and not first.strip():
        raise RocError("Enter the lawyer's first and last names." if kind == 'attorney' else 'Enter a last name or entity name.')
    aliases = value.get('aliases', [])
    if not isinstance(aliases, list) or len(aliases) > 30 or any(not isinstance(a, str) or not a.strip() or len(a) > 200 for a in aliases):
        raise RocError('Aliases must be a list of full names.')
    if kind == 'litigant' and aliases:
        raise RocError('Litigant aliases are not supported in this search step.')
    additional = value.get('additionalNames', [])
    if not isinstance(additional, list) or len(additional) > 30:
        raise RocError('Add at most 30 additional names.')
    names = []
    for item in additional:
        if not isinstance(item, dict) or set(item) - {'firstName', 'lastName'}:
            raise RocError('Additional names need separate first and last/entity name fields.')
        parsed = subject({'searchType': kind, ('lawyer' if kind == 'attorney' else 'litigant'): item})
        names.append({k: parsed[k] for k in ('firstName', 'lastName')})
    return {'firstName': first.strip(), 'lastName': last.strip(), 'aliases': list(dict.fromkeys(a.strip() for a in aliases)),
            'additionalNames': names}


def subject_name(config):
    name = subject(config)
    return ' '.join(filter(None, (name['firstName'], name['lastName'])))


def counsel_aliases(config):
    if search_type(config) != 'attorney':
        return []
    target = subject(config)
    return list(dict.fromkeys([subject_name(config), *[' '.join((n['firstName'], n['lastName'])) for n in target['additionalNames']], *target['aliases']]))


def search_criteria(config, name=None):
    name = subject(config) if name is None else name
    result = {'lastName': name['lastName'], 'partyType': 'aty' if search_type(config) == 'attorney' else 'pty'}
    if name['firstName']:
        result['firstName'] = name['firstName']
    if config.get('search'):
        result['courtCase'] = config['search']
    return result


def search_plan(config):
    """Skip identical trimmed query names, without guessing other equivalences."""
    target = subject(config)
    found = {}
    for name in [target, *target['additionalNames']]:
        key = (name['firstName'], name['lastName'])
        found.setdefault(key, {'name': ' '.join(filter(None, key)), 'criteria': search_criteria(config, name)})
    return list(found.values())


def require_attorney(config):
    if search_type(config) != 'attorney':
        raise RocError(LITIGANT_STAGE)


def indexed_parties(case):
    """Keep returned names/role codes as evidence, without guessing side or identity."""
    found = {}
    for row in case['sourceRows']:
        if str(row.get('partyType', '')).lower() == 'aty':
            raise RocError('An attorney record appeared in a litigant search. Saved responses need review; no fallback search was submitted.')
        name = unescape(' '.join(filter(None, (clean(row.get(k)) for k in ('firstName', 'middleName', 'lastName', 'generation')))))
        role = row.get('partyRole', row.get('role', ''))
        if isinstance(role, list):
            role = '; '.join(str(v) for v in role)
        role = clean(role)
        if name:
            found[(name, role)] = {'name': name, 'role': role}
    return sorted(found.values(), key=lambda p: (p['name'].casefold(), p['role']))
