"""One receipt ledger and resumable PCL cache across explicit name searches."""
import time

from .common import RocError, fingerprint, write_json
from .index import build_index
from .pacer import collect_index
from .search import search_plan, search_type, indexed_parties


def collect_searches(session, config, store, progress=None):
    plan = search_plan(config)
    rows = {}
    completed = 0
    spent_at_start = store.spent
    delay = max(1, config.get('requestDelaySeconds', 5))

    def save():
        write_json(store.root / 'pcl-partial-records.json', list(rows.values()))
        write_json(store.root / 'search-progress.json', {'queries': plan, 'completedQueries': completed,
                   'totalQueries': len(plan), 'complete': completed == len(plan)})

    save()
    for index, query in enumerate(plan):
        def report(stage, message, **values):
            if progress:
                # Show total new spend across all names, not just the current one.
                values['newSearchChargesCents'] = store.spent - spent_at_start
                progress(stage, f"Name search {index + 1}/{len(plan)}: {query['name']}. " + message,
                         **values, completedQueries=completed, totalQueries=len(plan))
        try:
            store.checkpoint()
            if index and store.cached('pcl', {'criteria': query['criteria'], 'page': 0}) is None:
                time.sleep(delay)
            results = collect_index(session, query['criteria'], store, delay=delay, progress=report, spent_at_start=spent_at_start)
            for case in build_index(results):
                if search_type(config) == 'litigant':
                    indexed_parties(case)
            for row in results:
                # Authentic unmodified replies remain in responses/. This field
                # records provenance only in ROC's combined copy of the index.
                original = {k: v for k, v in row.items() if k != '_rocSearchNames'}
                key = fingerprint(original)
                target = rows.setdefault(key, original | {'_rocSearchNames': []})
                if query['name'] not in target['_rocSearchNames']:
                    target['_rocSearchNames'].append(query['name'])
            completed += 1
            save()
        except RocError as exc:
            save()
            raise RocError(f'Search stopped after {completed}/{len(plan)} name searches. {exc} '
                           'Saved pages will be reused when you resume.') from exc
    return list(rows.values())
