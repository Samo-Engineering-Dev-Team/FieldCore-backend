"""Deprecated: DB smoke script removed for Supabase migration.

This script previously performed direct DB writes and presence checks.
It has been disabled while the project migrates database backends to Supabase.
"""

import sys


def main():
    print("smoke_noc_online: disabled (DB tests removed for Supabase migration)")


if __name__ == "__main__":
    main()
    sys.exit(0)
