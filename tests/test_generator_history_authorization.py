"""
Who may read a generator's refuel history.

The guard has to hang off the unit's assignment, not off which data source
happens to have rows. An earlier version ran the site-scope check inside the
legacy-fills branch, so a unit that was placed at a site but carried no
`legacy_gen_no` — every unit registered through the asset register, i.e. all
future units — matched neither branch and was checked by nothing, while the
ledger leg still returned its history to any technician who asked.
"""

from uuid import uuid4

import pytest

from app.exceptions.http import ForbiddenException
from app.models.auth import TokenData
from app.services import generator as generator_module
from app.services.generator import _GeneratorService
from app.utils.enums import UserRole


class _FakeSession:
    """The authorization checks run before any query the history needs."""

    def exec(self, *_args, **_kwargs):
        class _Result:
            def all(self):
                return []

            def first(self):
                return None

        return _Result()


def _token(role: UserRole) -> TokenData:
    return TokenData(user_id=uuid4(), role=role)


def _generator(site_id, legacy_gen_no):
    from app.models import Generator

    return Generator(
        name="Gen 1", site_id=site_id, legacy_gen_no=legacy_gen_no, serial_no=None
    )


@pytest.fixture
def service(monkeypatch):
    svc = _GeneratorService()
    monkeypatch.setattr(
        generator_module, "legacy_refuel_cutover", lambda session: None
    )
    return svc


class TestSiteScoping:
    @pytest.mark.parametrize(
        "legacy_gen_no",
        [
            1,  # a migrated unit — the legacy leg runs
            None,  # registered through the asset register — it does not
        ],
        ids=["migrated_unit", "asset_register_unit"],
    )
    def test_an_assigned_unit_is_always_site_scoped(
        self, service, monkeypatch, legacy_gen_no
    ):
        site_id = uuid4()
        calls: list = []

        def _scope(site, user, session):
            calls.append(site)

        monkeypatch.setattr(generator_module, "assert_site_history_in_scope", _scope)
        monkeypatch.setattr(
            service, "_get", lambda gid, session: _generator(site_id, legacy_gen_no)
        )

        service.read_diesel_history(
            uuid4(), _FakeSession(), _token(UserRole.TECHNICIAN)  # type: ignore[arg-type]
        )

        # The guard must run for both shapes. Only the legacy leg is optional.
        assert calls == [site_id]

    def test_an_unassigned_unit_is_management_only(self, service, monkeypatch):
        monkeypatch.setattr(service, "_get", lambda gid, session: _generator(None, None))

        with pytest.raises(ForbiddenException):
            service.read_diesel_history(
                uuid4(), _FakeSession(), _token(UserRole.TECHNICIAN)  # type: ignore[arg-type]
            )

    def test_management_may_read_an_unassigned_unit(self, service, monkeypatch):
        monkeypatch.setattr(service, "_get", lambda gid, session: _generator(None, None))

        history = service.read_diesel_history(
            uuid4(), _FakeSession(), _token(UserRole.MANAGER)  # type: ignore[arg-type]
        )
        assert history.entry_count == 0

    def test_an_internal_call_with_no_user_skips_the_guards(self, service, monkeypatch):
        # current_user=None is the service-to-service path; it must not blow up
        # on a missing token, and must not be how a request bypasses scoping.
        called: list = []
        monkeypatch.setattr(
            generator_module,
            "assert_site_history_in_scope",
            lambda *a: called.append(a),
        )
        monkeypatch.setattr(
            service, "_get", lambda gid, session: _generator(uuid4(), None)
        )

        service.read_diesel_history(uuid4(), _FakeSession(), None)  # type: ignore[arg-type]
        assert called == []
