"""Small browser projections; source/export records remain complete."""
from .common import fingerprint
from .name_rules import party_key, snapshot


def data_revision(folder, output, config, rules):
    def stamp(path):
        try:
            stat = path.stat()
            return stat.st_mtime_ns, stat.st_size
        except FileNotFoundError:
            return None
    return fingerprint({'version': 1, 'output': str(output.relative_to(folder)),
        'files': [stamp(output / 'evidence.json'), stamp(folder / 'pcl-records.json'),
                  stamp(folder / 'pcl-partial-records.json')],
        'config': config, 'rules': snapshot(rules)})


def compact_reports(reports):
    # The interface presents clients and filters by every party. Opponent report
    # groups and repeated case context belong in exports, not each UI response.
    lookup = {party_key(name): rule['label'] for rule in reports['nameRules']['rules'] for name in rule['names']}
    parties = [{key: row[key] for key in ('caseKey', 'name', 'partyRole', 'sourceNames',
                                         'relationship', 'matchedCounsel', 'defendantNumbers')} |
               {'displayName': lookup.get(party_key(row['name']), row['name'])}
               for row in reports['parties']]
    return {key: value for key, value in reports.items() if key not in ('defendants', 'plaintiffs', 'parties')} | {'partyDetails': parties}
