import datetime

from dim import db
from dim.manage_dim import update_validity
from dim.models import Zone, OutputUpdate
from tests.util import RPCTest


class UpdateValidityTest(RPCTest):
    '''manage_dim update-validity renews the signature validity window of signed zones.

    A renewed window only reaches the name servers when the outputs get a
    dnssec_update for the zone, so every test checks for that update and not
    only for the new window in the database.
    '''

    def setUp(self):
        RPCTest.setUp(self)
        self.r.zone_group_create('zg')
        self.r.output_create('o', plugin='pdns-db', db_uri='')
        self.r.output_add_group('o', 'zg')

    def sign(self, name, **nsec3):
        self.r.zone_create(name)
        self.r.zone_group_add_zone('zg', name)
        self.r.zone_dnssec_enable(name, **nsec3)

    def age(self, name, days):
        # move the validity window into the past, as if it was set `days` ago
        zone = Zone.query.filter_by(name=name).one()
        zone.valid_begin -= datetime.timedelta(days=days)
        zone.valid_end -= datetime.timedelta(days=days)
        db.session.commit()

    def dnssec_updates(self, name):
        return OutputUpdate.query.filter_by(zone_name=name, action=OutputUpdate.DNSSEC_UPDATE).count()

    def run_update_validity(self):
        result = self.app.test_cli_runner().invoke(update_validity)
        assert result.exit_code == 0, result.output
        db.session.expire_all()

    def valid_end(self, name):
        return Zone.query.filter_by(name=name).one().valid_end

    def assert_renewed_and_published(self, name):
        before = self.dnssec_updates(name)
        self.run_update_validity()
        assert self.valid_end(name) > datetime.datetime.utcnow() + datetime.timedelta(days=13)
        assert self.dnssec_updates(name) == before + 1

    def test_nsec3_zone_is_renewed_and_published(self):
        self.sign('nsec3.de', nsec3_algorithm=1, nsec3_iterations=0, nsec3_salt='-')
        self.age('nsec3.de', 8)
        self.assert_renewed_and_published('nsec3.de')

    def test_nsec_zone_is_renewed_and_published(self):
        # NSEC zones (no nsec3_algorithm) used to get the new window in the
        # database only, so their signatures expired after 14 days
        self.sign('nsec.de')
        self.age('nsec.de', 8)
        self.assert_renewed_and_published('nsec.de')

    def test_zone_with_more_than_half_of_the_window_left_is_not_touched(self):
        self.sign('fresh.de')
        self.age('fresh.de', 6)
        end = self.valid_end('fresh.de')
        before = self.dnssec_updates('fresh.de')
        self.run_update_validity()
        assert self.valid_end('fresh.de') == end
        assert self.dnssec_updates('fresh.de') == before

    def test_unsigned_zone_is_not_touched(self):
        self.r.zone_create('unsigned.de')
        self.r.zone_group_add_zone('zg', 'unsigned.de')
        self.run_update_validity()
        assert self.valid_end('unsigned.de') is None
        assert self.dnssec_updates('unsigned.de') == 0
