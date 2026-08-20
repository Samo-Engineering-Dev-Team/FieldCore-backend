"""Create `generators` rows for the units already visible in diesel reports.

Phase 1 of FINANCE_TECHNICIAN_IMPLEMENTATION_PLAN.md. Deliberately a script and
not a migration: it reads production report payloads and writes new rows derived
from them, which is data seeding rather than schema, and it must be reviewable
and re-runnable on its own schedule.

Existing diesel reports carry the generator as free-text `gen_no` inside
`reports.data.diesel_fillups[]` (see app/models/report_data.py). That JSON is
never rewritten — the additive-only constraint rules it out, and
DieselSiteHistory keeps reading it as-is. This script only makes sure a matching
`generators` row exists for each (site_id, gen_no) pair the history already
mentions, so the Finance Dashboard can resolve legacy fills to a real unit.

READ-ONLY against `reports`. Writes only INSERTs into `generators`, and only for
pairs that have no row yet — safe to re-run.

Dry run by default:
    uv run python scripts/seed_generators.py

Apply:
    uv run python scripts/seed_generators.py --apply
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import create_engine, text

from app.core import app_settings

# gen_no in the JSON is `str | int | None`. DieselHistoryEntry documents the
# read-side rule already in force: entries with no usable gen_no land in 1. This
# mirrors that rather than inventing a second interpretation, so the seeded rows
# line up with what the existing history view already reports.
FALLBACK_GEN_NO = 1

# Only 1 and 2 appear in practice (DieselHistoryEntry: "1 or 2"). A larger value
# is accepted if genuinely present, but anything unparseable or out of range is
# reported rather than silently coerced.
MAX_PLAUSIBLE_GEN_NO = 8


FIND_PAIRS_SQL = text(
    """
    SELECT DISTINCT
        fillup->>'site_id' AS site_id,
        fillup->>'gen_no'  AS gen_no
    FROM reports r
    CROSS JOIN LATERAL jsonb_array_elements(r.data->'diesel_fillups') AS fillup
    WHERE r.deleted_at IS NULL
      -- 'DIESEL', not 'diesel': reporttype is a native Postgres enum and
      -- SQLAlchemy maps enum members by NAME, so the stored label is uppercase
      -- (same convention as the ALTER TYPE in the Phase 1 migration).
      AND r.report_type = 'DIESEL'
      AND jsonb_typeof(r.data->'diesel_fillups') = 'array'
      AND fillup->>'site_id' IS NOT NULL
    """
)

EXISTING_SQL = text(
    """
    SELECT site_id, gen_no
    FROM generators
    WHERE deleted_at IS NULL
    """
)

LIVE_SITES_SQL = text("SELECT id FROM sites WHERE deleted_at IS NULL")

INSERT_SQL = text(
    """
    INSERT INTO generators (id, site_id, gen_no, label, is_active,
                            created_at, updated_at)
    VALUES (:id, :site_id, :gen_no, NULL, TRUE, now(), now())
    """
)


@dataclass(frozen=True)
class SeedUnit:
    site_id: UUID
    gen_no: int
    inferred: bool  # gen_no was absent or unparseable, so FALLBACK_GEN_NO was used


def _parse_gen_no(raw: str | None) -> tuple[int, bool]:
    """Return (gen_no, inferred). Mirrors DieselHistoryEntry's gen_no_inferred."""
    if raw is None or not raw.strip():
        return FALLBACK_GEN_NO, True
    try:
        value = int(float(raw.strip()))
    except (TypeError, ValueError):
        return FALLBACK_GEN_NO, True
    if value < 1 or value > MAX_PLAUSIBLE_GEN_NO:
        return FALLBACK_GEN_NO, True
    return value, False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the missing generator rows. Without this, only reports.",
    )
    args = parser.parse_args()

    engine = create_engine(app_settings.database_url)

    with engine.begin() as conn:
        live_sites = {row.id for row in conn.execute(LIVE_SITES_SQL)}
        existing = {(row.site_id, int(row.gen_no)) for row in conn.execute(EXISTING_SQL)}

        wanted: set[SeedUnit] = set()
        orphan_site_ids: set[str] = set()
        unparseable = 0

        for row in conn.execute(FIND_PAIRS_SQL):
            try:
                site_id = UUID(str(row.site_id))
            except (TypeError, ValueError):
                orphan_site_ids.add(str(row.site_id))
                continue
            if site_id not in live_sites:
                # A fill recorded against a site that no longer exists (or was
                # never a real site id). Reported, never invented — a generator
                # row needs a valid FK.
                orphan_site_ids.add(str(row.site_id))
                continue
            gen_no, inferred = _parse_gen_no(row.gen_no)
            if inferred:
                unparseable += 1
            wanted.add(SeedUnit(site_id=site_id, gen_no=gen_no, inferred=inferred))

        missing = sorted(
            (u for u in wanted if (u.site_id, u.gen_no) not in existing),
            key=lambda u: (str(u.site_id), u.gen_no),
        )

        print(f"distinct (site, gen_no) pairs in diesel history : {len(wanted)}")
        print(f"generator rows already present                  : {len(existing)}")
        print(f"rows to create                                  : {len(missing)}")
        if unparseable:
            print(
                f"fill-ups with absent/unparseable gen_no         : {unparseable} "
                f"(assigned Gen {FALLBACK_GEN_NO}, matching DieselHistoryEntry)"
            )
        if orphan_site_ids:
            print(
                f"fill-ups referencing an unknown site            : {len(orphan_site_ids)} "
                "(skipped — no valid FK target; listed below)"
            )
            for sid in sorted(orphan_site_ids):
                print(f"    {sid}")

        for unit in missing:
            note = "  [gen_no inferred]" if unit.inferred else ""
            print(f"  + site {unit.site_id}  Gen {unit.gen_no}{note}")

        if not args.apply:
            print("\nDry run. Re-run with --apply to create the rows above.")
            return 0

        for unit in missing:
            conn.execute(
                INSERT_SQL,
                {"id": uuid4(), "site_id": unit.site_id, "gen_no": unit.gen_no},
            )
        print(f"\nCreated {len(missing)} generator row(s).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
