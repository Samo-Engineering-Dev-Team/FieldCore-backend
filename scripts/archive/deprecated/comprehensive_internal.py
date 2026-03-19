"""Deprecated: internal comprehensive checks disabled for Supabase migration.

This module previously performed service-level checks that required direct DB
interaction. It is disabled while migrating the database backend to Supabase.
"""

import sys


def main():
    print("comprehensive_internal: disabled (DB tests removed for Supabase migration)")


if __name__ == '__main__':
    main()
    sys.exit(0)
