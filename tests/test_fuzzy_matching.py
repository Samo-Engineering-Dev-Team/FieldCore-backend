from decimal import Decimal

from app.models import Client, Site
from app.services.client import ClientService
from app.services.site import _SiteService
from app.utils.enums import Region


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _Session:
    def __init__(self, rows):
        self.rows = rows
        self.statement = None

    def exec(self, statement):
        self.statement = statement
        return _Result(self.rows)


def test_site_fuzzy_search_returns_match_score_and_counts():
    site = Site(name="SEACOM Durban POP", region=Region.KZN, address="Durban")
    session = _Session([(site, Decimal("0.72"))])
    service = _SiteService()
    service._get_related_counts = lambda _session, _ids: ({site.id: 2}, {site.id: 1})  # type: ignore[method-assign]

    result = service.search_sites(" secom durbn ", session)

    assert len(result) == 1
    assert result[0].name == "SEACOM Durban POP"
    assert result[0].match_score == 0.72
    assert result[0].num_tasks == 2
    assert result[0].num_incidents == 1
    assert session.statement is not None


def test_client_fuzzy_search_returns_match_score():
    client = Client(name="SEACOM", is_active=True)
    session = _Session([(client, Decimal("0.81"))])

    result = ClientService().search_clients(" secom ", session)

    assert len(result) == 1
    assert result[0].name == "SEACOM"
    assert result[0].match_score == 0.81
    assert session.statement is not None
