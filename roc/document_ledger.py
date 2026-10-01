"""Separate, durable AI/document allowances. Ambiguous requests never replay."""
from .common import RocError, read_json, write_json, now


class ExpenseLedger:
    def __init__(self, root, kind):
        self.root, self.kind = root, kind
        self.path = root / (kind + '-ledger.json')
        self.data = read_json(self.path) if self.path.exists() else {'transactions': [], 'limitCents': 0}

    @property
    def spent(self):
        return sum(t.get('chargedCents', 0) for t in self.data['transactions'])

    def save(self):
        write_json(self.path, self.data)

    def check(self):
        if self.data.get('stoppedReason') or any(t['state'] == 'pending' for t in self.data['transactions']):
            raise RocError(f'An unresolved {self.kind} request blocks further spending. Inspect its saved response and account billing; do not retry it.')

    def approve(self, cents):
        self.check()
        if type(cents) is not int or cents < 0:
            raise RocError('Enter a nonnegative fresh spending cap.')
        self.data.update(limitCents=self.spent + cents, approvedUtc=now())
        self.save()

    def find(self, key):
        return next((t for t in self.data['transactions'] if t['key'] == key and t['state'] != 'not-submitted'), None)

    def reserve(self, key, amount, metadata):
        self.check()
        if self.find(key):
            raise RocError('Duplicate request refused. Use the saved result.')
        if self.spent + amount > self.data['limitCents']:
            raise RocError(f'{self.kind.title()} spending cap reached. No request submitted.')
        t = {'key': key, 'state': 'pending', 'reservedCents': amount, 'startedUtc': now(), **metadata}
        self.data['transactions'].append(t)
        self.save()
        return t

    def finish(self, t, amount, **extra):
        if type(amount) is not int or amount < 0:
            raise RocError('Invalid cost; request remains pending.')
        t.update(state='complete', chargedCents=amount, completedUtc=now(), **extra)
        if amount > t['reservedCents']:
            self.data['stoppedReason'] = 'Reported cost exceeded the reservation. Check billing before continuing.'
        self.save()
        self.check()

    def not_submitted(self, t):
        t.update(state='not-submitted', chargedCents=0)
        self.save()

    def public(self):
        return {'spentCents': self.spent, 'limitCents': self.data['limitCents'],
                'pendingCount': sum(t['state'] == 'pending' for t in self.data['transactions']),
                'stoppedReason': self.data.get('stoppedReason', ''), 'transactions': self.data['transactions']}
