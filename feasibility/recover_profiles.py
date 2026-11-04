"""Issue a short-lived pairing code for a locally stored household tenant."""
import argparse
import sys

from feasibility.sessions import Sessions
from feasibility.tenant_store import TenantStore


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name', required=True, help='Exact saved household profile name')
    parser.add_argument('--tenant', help='Tenant ID, if more than one tenant matches')
    args = parser.parse_args(argv)
    store = TenantStore()
    with store._transaction() as db:
        rows = db.execute('''SELECT DISTINCT t.id FROM profiles p JOIN tenants t ON t.id=p.tenant_id
            WHERE p.name=? AND t.state='active' AND (t.expires IS NULL OR t.expires>?) ORDER BY t.id''',
            (args.name, store.clock())).fetchall()
    matches = [row['id'] for row in rows]
    if args.tenant:
        matches = [item for item in matches if item == args.tenant]
    if not matches:
        print('No matching active household found in the configured data root.', file=sys.stderr)
        return 1
    if len(matches) != 1:
        print('Multiple tenants match; repeat with --tenant and one of these IDs:', file=sys.stderr)
        print('\n'.join(matches), file=sys.stderr)
        return 1
    value = Sessions(store).pair(matches[0])
    print('One-time code (expires in 5 minutes; grants access to the entire household):')
    print(value['code'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
