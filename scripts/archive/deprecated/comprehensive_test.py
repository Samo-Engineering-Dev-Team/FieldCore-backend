"""Deprecated: comprehensive_test disabled for Supabase migration.

This script previously seeded users directly into the DB and exercised HTTP
endpoints as an integration test. It has been disabled while migrating the
database to Supabase. Keep this file as a placeholder for future migration
work where tests will be adapted to Supabase instead of direct DB writes.
"""

import sys


def main():
    print("comprehensive_test: disabled (DB tests removed for Supabase migration)")


if __name__ == '__main__':
    main()
    sys.exit(0)
