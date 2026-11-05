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

    def run_update_validity(self, args=None):
        result = self.app.test_cli_runner().invoke(update_validity, args or [])
        assert result.exit_code == 0, result.output
        db.session.expire_all()
        return result

    def valid_begin(self, name):
        return Zone.query.filter_by(name=name).one().valid_begin

    def valid_end(self, name):
        return Zone.query.filter_by(name=name).one().valid_end

    def assert_renewed_and_published(self, name):
        before = self.dnssec_updates(name)
        now = datetime.datetime.utcnow()
        self.run_update_validity()
        # Verify valid_begin is backdated by 1 hour (RFC 6781 skew)
        assert now - datetime.timedelta(seconds=3660) <= self.valid_begin(name) <= now - datetime.timedelta(seconds=3540)
        # Verify valid_end is set to full window in the future
        assert self.valid_end(name) > now + datetime.timedelta(days=13)
        # Verify renewal task is queued in outputupdate table
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

    def test_valid_begin_is_backdated_by_default_skew(self):
        self.sign('skew.de')
        now = datetime.datetime.utcnow()
        begin = self.valid_begin('skew.de')
        end = self.valid_end('skew.de')
        assert now - datetime.timedelta(seconds=3660) <= begin <= now - datetime.timedelta(seconds=3540)
        assert now + datetime.timedelta(days=13, hours=23) <= end <= now + datetime.timedelta(days=14, hours=1)

    def test_custom_validity_skew_attribute(self):
        self.r.zone_create('custom.de')
        self.r.zone_set_attrs('custom.de', {'dnssec_validity_skew': 7200})
        self.r.zone_group_add_zone('zg', 'custom.de')
        self.r.zone_dnssec_enable('custom.de')
        now = datetime.datetime.utcnow()
        begin = self.valid_begin('custom.de')
        assert now - datetime.timedelta(seconds=7260) <= begin <= now - datetime.timedelta(seconds=7140)

    def test_dryrun_does_not_commit_changes(self):
        self.sign('dry.de')
        self.age('dry.de', 8)
        end_before = self.valid_end('dry.de')
        updates_before = self.dnssec_updates('dry.de')
        result = self.app.test_cli_runner().invoke(update_validity, ['-n'])
        assert result.exit_code == 0
        assert 'Dry-run' in result.output
        db.session.rollback()
        assert self.valid_end('dry.de') == end_before
        assert self.dnssec_updates('dry.de') == updates_before

    def test_single_zone_renewed_when_due(self):
        self.sign('target.de')
        self.sign('other.de')
        self.age('target.de', 8)
        self.age('other.de', 8)
        before_target = self.dnssec_updates('target.de')
        before_other = self.dnssec_updates('other.de')
        end_other = self.valid_end('other.de')

        result = self.run_update_validity(['target.de'])
        assert '[QUEUE] target.de' in result.output
        assert self.valid_end('target.de') > datetime.datetime.utcnow() + datetime.timedelta(days=13)
        assert self.dnssec_updates('target.de') == before_target + 1
        assert self.valid_end('other.de') == end_other
        assert self.dnssec_updates('other.de') == before_other

    def test_single_zone_skipped_when_fresh(self):
        self.sign('fresh-single.de')
        end_before = self.valid_end('fresh-single.de')
        before_updates = self.dnssec_updates('fresh-single.de')

        result = self.run_update_validity(['fresh-single.de'])
        assert '[SKIP]  fresh-single.de' in result.output
        assert self.valid_end('fresh-single.de') == end_before
        assert self.dnssec_updates('fresh-single.de') == before_updates

    def test_single_zone_forced_renewal(self):
        self.sign('force-single.de')
        self.age('force-single.de', 2)
        end_before = self.valid_end('force-single.de')
        before_updates = self.dnssec_updates('force-single.de')

        result = self.run_update_validity(['force-single.de', '--force'])
        assert '[QUEUE] force-single.de: forced renewal (--force)' in result.output
        assert self.valid_end('force-single.de') > end_before
        assert self.dnssec_updates('force-single.de') == before_updates + 1

    def test_nonexistent_zone_fails(self):
        runner = self.app.test_cli_runner()
        result = runner.invoke(update_validity, ['nonexistent.de'])
        assert result.exit_code == 1
        assert "Zone 'nonexistent.de' does not exist" in result.output

    def test_unsigned_zone_fails(self):
        self.r.zone_create('unsigned-single.de')
        self.r.zone_group_add_zone('zg', 'unsigned-single.de')
        runner = self.app.test_cli_runner()
        result = runner.invoke(update_validity, ['unsigned-single.de'])
        assert result.exit_code == 1
        assert "Zone 'unsigned-single.de' is not signed" in result.output

    def test_force_all_zones(self):
        self.sign('all1.de')
        self.sign('all2.de')
        before1 = self.dnssec_updates('all1.de')
        before2 = self.dnssec_updates('all2.de')

        result = self.run_update_validity(['--force'])
        assert '[QUEUE] all1.de (forced renewal (--force))' in result.output
        assert '[QUEUE] all2.de (forced renewal (--force))' in result.output
        assert self.dnssec_updates('all1.de') == before1 + 1
        assert self.dnssec_updates('all2.de') == before2 + 1
