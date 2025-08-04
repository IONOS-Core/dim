from dim import db
from dim.dns import get_ip_from_ptr_name
from dim.rrtype import validate_strings
from dim.errors import InvalidParameterError, AlreadyExistsError, InvalidZoneError, DimError
from tests.util import RPCTest, raises


def test_validate_strings():
    validate_strings(None, 'strings', [r'''\"\\\223'''])
    validate_strings(None, 'strings', [r'''\"\\\223'''])


def rrs(coll, fields=('record', 'zone', 'type', 'value')):
    if not coll:
        return set()
    if isinstance(coll[0], dict):
        return set(tuple(rr[field] for field in fields) for rr in coll
                   if 'type' not in fields or rr['type'] != 'SOA')
    else:
        return set(coll)


def print_messages(result):
    print('\n'.join(m[1] for m in result['messages']))


def test_get_ip_from_ptr_name():
    assert get_ip_from_ptr_name('1.2.3.4.in-addr.arpa.') == '4.3.2.1'
    assert get_ip_from_ptr_name('1.2/32.2.3.4.in-addr.arpa.') == '4.3.2.1'
    assert get_ip_from_ptr_name('1.2/32.2.3.4.in-addr.arpa.') == '4.3.2.1'
    assert get_ip_from_ptr_name('2.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.1.0.0.0.ip6.arpa.') == \
        '0001:0000:0000:0000:0000:0000:0000:0002'
    with raises(ValueError):
        get_ip_from_ptr_name('abc')
    with raises(ValueError):
        get_ip_from_ptr_name('1.3.4.in-addr.arpa.')


class ZoneTest(RPCTest):
    def test_create_zone(self):
        with raises(InvalidParameterError):
            self.r.zone_create('a 0.com')
        with raises(InvalidParameterError):
            self.r.zone_create('test.com', soa_attributes={'a': 1})
        self.r.zone_create('test.com')
        with raises(AlreadyExistsError):
            self.r.zone_create('test.com')
        with raises(InvalidParameterError):
            self.r.zone_create('test.com.')
        with raises(InvalidParameterError):
            self.r.zone_create('test-')

    def test_zone_rename(self):
        self.r.zone_create('internal', profile=True)
        self.r.rr_create(name='internal.', type='NS', nsdname='external.')
        self.r.rr_create(name='a.internal.', type='CNAME', cname='c')
        self.r.zone_rename('internal', 'public', profile=True)
        assert self.r.zone_list(profile=True) == [{'name': 'public'}]
        assert rrs(self.r.rr_list(zone='public', profile=True)) == rrs([
                ('@', 'public', 'NS', 'external.'),
                ('a', 'public', 'CNAME', 'c')])
        with raises(InvalidParameterError):
            self.r.zone_rename('public', 'private', profile=False)

    def test_add_view_1(self):
        self.r.zone_create('test.com')
        self.r.zone_create_view('test.com', 'view')
        assert self.r.zone_list_views('test.com') == [{'name': 'default'}, {'name': 'view'}]

    def test_rename_view(self):
        self.r.zone_create('test.com')
        self.r.zone_create_view('test.com', 'view')
        self.r.zone_rename_view('test.com', 'view', 'test')
        assert self.r.zone_list_views('test.com') == [{'name': 'default'}, {'name': 'test'}]

    def test_add_view_2(self):
        self.r.zone_create('profile', profile=True)
        with raises(DimError):
            self.r.zone_create_view('profile', 'test')

    def test_attrs(self):
        self.r.zone_create('test.com', attributes={'a': 'b'}, soa_attributes={'primary': 'c.'})
        assert self.r.zone_get_attrs('test.com')['a'] == 'b'

        self.r.zone_set_attrs('test.com', {'a': '1'})
        assert self.r.zone_get_attrs('test.com')['a'] == '1'
        self.r.zone_delete_attrs('test.com', ['a'])
        assert 'a' not in self.r.zone_get_attrs('test.com')

        assert self.r.zone_get_soa_attrs('test.com')['primary'] == 'c.'
        self.r.zone_set_soa_attrs('test.com', {'primary': 'd.'})
        assert self.r.zone_get_soa_attrs('test.com')['primary'] == 'd.'

    def test_profiles(self):
        self.r.zone_create('internal', profile=True, soa_attributes=dict(mail='a.b.com.', refresh='1337', expire=1))
        self.r.zone_create('test.com', from_profile='internal', soa_attributes=dict(refresh='47'))
        assert self.r.zone_get_soa_attrs('test.com')['refresh'] == 47
        assert self.r.zone_get_soa_attrs('test.com')['mail'] == 'a.b.com.'
        with raises(InvalidZoneError):
            self.r.zone_delete('internal', profile=False)
        with raises(InvalidZoneError):
            self.r.zone_delete('test.com', profile=True)
        self.r.zone_delete('internal', profile=True)
        self.r.zone_delete('test.com')

    def test_profile_rrs(self):
        self.r.zone_create('profile', profile=True)
        self.r.rr_create(name='@', zone='profile', type='NS', nsdname='whatever.com.', profile=True)
        self.r.rr_create(name='a', zone='profile', type='TXT', strings='"something"', profile=True)
        self.r.zone_create('test.com', from_profile='profile')
        assert rrs(self.r.rr_list('*test.com.')) == rrs(
            [('a', 'test.com', 'TXT', '"something"'),
             ('@', 'test.com', 'NS', 'whatever.com.')])
        with raises(InvalidParameterError):
            self.r.rr_create(name='foo.profile.', type='A', ip='127.0.0.1', profile=True)
        with raises(InvalidParameterError):
            self.r.rr_create(name='foo.profile.', type='AAAA', ip='::1', profile=True)

    def test_list_zone(self):
        self.r.zone_create('some.domain', soa_attributes=dict(primary='ns01.company.com.', mail='dnsadmin.company.com.'))
        self.r.rr_create(name='some.domain.', type='MX', preference=10, exchange='mail.other.domain.', ttl=1200)
        self.r.rr_create(name='www.some.domain.', type='A', ip='192.168.78.2')
        records = self.r.rr_list(zone='some.domain')
        assert records[0]['type'] == 'SOA' and records[0]['value'].startswith('ns01.company.com. dnsadmin.company.com')
        assert rrs([('@', 'some.domain', 1200, 'MX', '10 mail.other.domain.'),
                    ('www', 'some.domain', None, 'A', '192.168.78.2')])\
            <= rrs(records, fields=('record', 'zone', 'ttl', 'type', 'value'))

    def test_zone_list_underscore(self):
        self.r.zone_create('nounderscore.com')
        self.r.zone_create('with_underscore.com')
        assert self.r.zone_list() == [
            {'name': 'nounderscore.com'},
            {'name': 'with_underscore.com'}]
        assert self.r.zone_list('*_*') == [{'name': 'with_underscore.com'}]

    def test_zone_list(self):
        self.r.zone_create('profile.domain', profile=True)
        self.r.zone_create('master.domain')
        self.r.zone_create('no-no.domain')
        self.r.zone_create('multipleviews.domain')
        self.r.zone_create_view('multipleviews.domain', 'secondview')
        self.r.zone_create('second.domain')
        self.r.zone_group_create('zg')
        self.r.zone_group_create('zg2')
        self.r.zone_group_create('zg3')
        self.r.zone_group_add_zone('zg', 'master.domain')
        self.r.zone_group_add_zone('zg2', 'master.domain')
        self.r.zone_group_add_zone('zg', 'second.domain')
        self.r.zone_group_add_zone('zg', 'multipleviews.domain', 'default')
        self.r.zone_group_add_zone('zg2', 'multipleviews.domain', 'secondview')
        self.r.zone_group_add_zone('zg3', 'multipleviews.domain', 'default')
        assert rrs(self.r.zone_list('*domain', profile=False, fields=True),
                   fields=('name', 'views', 'zone_groups')) == rrs(
            [('second.domain', 1, 1),
             ('master.domain', 1, 2),
             ('multipleviews.domain', 2, 3),
             ('no-no.domain', 1, 0)
             ])
        assert rrs(self.r.zone_list('*domain', profile=True, fields=True),
                   fields=('name',)) == rrs([('profile.domain',)])
        assert rrs(self.r.zone_list('*domain', profile=False, fields=True),
                   fields=('name', 'views')) == rrs(
            [('second.domain', 1),
             ('master.domain', 1),
             ('no-no.domain', 1),
             ('multipleviews.domain', 2)
             ])
        assert self.r.zone_list(profile=True) == [{'name': 'profile.domain'}]
        assert set([x['name'] for x in self.r.zone_list(profile=False)]) == set(
            ['master.domain',
             'no-no.domain',
             'multipleviews.domain',
             'second.domain'
             ])
        assert set([x['name'] for x in self.r.zone_list(profile=False, limit=2, offset=1)]) == set(
            ['multipleviews.domain',
             'no-no.domain'
             ])
        assert self.r.zone_count(profile=False) == 4

    def test_zone_list_alias(self):
        assert len(self.r.zone_list(alias=1)) == 0
        assert self.r.zone_count(alias='a') == 0
        self.r.zone_create('a.de')
        assert [x['name'] for x in self.r.zone_list(profile=False, alias=True)] == ['a.de']

    def test_revzone_profiles(self):
        self.r.zone_create('revzone-profile', profile=True, soa_attributes={'primary': 'revzone.'})
        self.r.ipblock_create('12.0.0.0/8', status='Container', attributes={'reverse_dns_profile': 'revzone-profile'})
        self.r.ippool_create('pool')
        self.r.ippool_add_subnet('pool', '12.0.0.0/23')
        assert self.r.zone_get_soa_attrs('1.0.12.in-addr.arpa')['primary'] == 'revzone.'

    def test_revzone_ipv6(self):
        self.r.ipblock_create('2001:db8::/32', status='Container')
        self.r.ippool_create('pool')
        self.r.ippool_add_subnet('pool', '2001:db8:100:a::26c/126')
        assert len(self.r.zone_list('a.0.0.0.0.0.1.0.8.b.d.0.1.0.0.2.ip6.arpa')) == 1

    def test_subzone(self):
        self.r.zone_create('company.com')
        self.r.rr_create(name='srv-monitoring.company.com.', type='TXT', strings=['test'])
        self.r.rr_create(name='monitoring.company.com.', type='TXT', strings=['test2'])
        self.r.zone_create('monitoring.company.com')
        assert rrs(self.r.rr_list(zone='company.com', type='TXT')) == rrs([
                ('srv-monitoring', 'company.com', 'TXT', '"test"')])
        assert rrs(self.r.rr_list(zone='monitoring.company.com', type='TXT')) == rrs([
                ('@', 'monitoring.company.com', 'TXT', '"test2"')])

    def test_dnssec_attrs(self):
        self.r.zone_create('test.com')
        self.r.zone_set_attrs('test.com', {'default_algorithm': '8'})
        self.r.zone_set_attrs('test.com', {'default_ksk_bits': 2048})
        self.r.zone_set_attrs('test.com', {'default_zsk_bits': 1024})
        with raises(InvalidParameterError):
            self.r.zone_set_attrs('test.com', {'default_algorithm': 'rsasha1'})
        with raises(InvalidParameterError):
            self.r.zone_set_attrs('test.com', {'default_ksk_bits': 'a'})
        with raises(InvalidParameterError):
            self.r.zone_set_attrs('test.com', {'default_zsk_bits': 'a'})

    def test_favorites(self):
        # Test for a zone with a single view
        self.r.zone_create('a.de')
        assert self.r.zone_list2(favorite_only=True)['count'] == 0

        assert not self.r.zone_favorite('a.de')
        self.r.zone_favorite_add('a.de')
        assert self.r.zone_favorite('a.de')
        print(self.r.zone_list2(favorite_only=True))
        assert self.r.zone_list2(favorite_only=True)['data'][0]['name'] == 'a.de'

        self.r.zone_favorite_remove('a.de')
        assert not self.r.zone_favorite('a.de')


class RR(RPCTest):
    def test_create_twice(self):
        self.r.ipblock_create('12.0.0.0/8', status='Container')
        self.r.ippool_create('test')
        self.r.ippool_add_subnet('test', '12.0.0.0/24')
        self.r.ip_mark('12.0.0.1')
        self.r.zone_create('test.com')
        self.r.rr_create(name='a.test.com.', type='a', ip='12.0.0.1')
        self.r.rr_create(name='a.test.com.', type='a', ip='12.0.0.1', overwrite_ptr=True)
        self.r.zone_delete('test.com', cleanup=True)
        assert rrs(self.r.rr_list(pattern='*0.0.12.in-addr.arpa.')) == rrs([])

    def test_rr_create_invalid_profile(self):
        with raises(InvalidZoneError):
            self.r.rr_create(profile=True, type='NS', nsdname='a.', zone='inexistent', name='@')

    def test_create_invalid_record_name(self):
        self.r.zone_create('a.de')
        self.r.rr_create(name='a.de.', type='TXT', strings=['text'], zone='a.de')
        with raises(InvalidParameterError):
            self.r.rr_create(name='suba.de.', type='TXT', strings=['text'], zone='a.de')

    def test_rr_delete_1(self):
        self.r.zone_create('test.com')
        self.r.rr_create(name='a.test.com.', type='cname', cname='b.test.com.')
        assert len(rrs(self.r.rr_list())) == 1
        self.r.rr_delete(name='a.test.com.', type='cname', cname='b.test.com.')
        assert len(rrs(self.r.rr_list())) == 0

    def test_rr_delete_2(self):
        self.r.ipblock_create('12.0.0.0/8', status='Container')
        self.r.ippool_create('test')
        self.r.ippool_add_subnet('test', '12.0.0.0/24')
        self.r.zone_create('test.com')
        self.r.rr_create(name='a.test.com.', type='a', ip='12.0.0.1')
        assert len(rrs(self.r.rr_list())) == 2
        self.r.rr_delete(name='a.test.com.', type='a', ip='12.0.0.1', free_ips=True)
        assert len(rrs(self.r.rr_list())) == 0
        assert self.r.ipblock_get_attrs('12.0.0.1')['status'] == 'Available'

    def test_rr_delete_3(self):
        self.r.ipblock_create('12::/32', status='Container')
        self.r.zone_create('test.com')
        self.r.ippool_create('test')
        self.r.ippool_add_subnet('test', '12::/64')
        self.r.rr_create(name='a.test.com.', type='a', ip='12.0.0.1')
        self.r.rr_create(name='a.test.com.', type='aaaa', ip='12::1')
        self.r.rr_delete(name='a.test.com.', type='a', ip='12.0.0.1')
        assert rrs(self.r.rr_list('a.test.com.')) == rrs([
                ('a', 'test.com', 'AAAA', '12::1'),
                ('1.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0', '0.0.0.0.0.0.0.0.0.0.0.0.2.1.0.0.ip6.arpa', 'PTR', 'a.test.com.')])

    def test_rr_delete_4(self):
        self.r.ipblock_create('12.0.0.0/8', status='Container')
        self.r.ippool_create('test')
        self.r.ippool_add_subnet('test', '12.0.0.0/24')
        self.r.zone_create('test.com')
        self.r.rr_create(name='a.test.com.', type='a', ip='12.0.0.1')
        self.r.rr_create(name='b.test.com.', type='a', ip='12.0.0.1', overwrite_ptr=True)
        self.r.rr_delete(name='a.test.com.', type='a', ip='12.0.0.1')
        assert not self.r.rr_list('a.test.com.')
        assert rrs(self.r.rr_list('b.test.com.')) == rrs([
                ('b', 'test.com', 'A', '12.0.0.1'),
                ('1', '0.0.12.in-addr.arpa', 'PTR', 'b.test.com.')])

    def test_rr_delete_5(self):
        # trigger recursive delete via rr_delete(ptr)
        self.r.ipblock_create('12.0.0.0/8', status='Container')
        self.r.ippool_create('test')
        self.r.ippool_add_subnet('test', '12.0.0.0/24')
        self.r.zone_create('test.com')
        self.r.rr_create(name='a.test.com.', type='a', ip='12.0.0.1')
        self.r.rr_create(name='b.test.com.', type='cname', cname='a')
        self.r.rr_delete(ip='12.0.0.1', type='ptr', ptrdname='a.test.com.', references='delete')
        assert rrs(self.r.rr_list()) == set()

    def test_rr_delete_6(self):
        # delete only one forward reference; expect ptr unchanged
        self.r.ipblock_create('12.0.0.0/8', status='Container')
        self.r.ippool_create('test')
        self.r.ippool_add_subnet('test', '12.0.0.0/24')
        self.r.zone_create('test.com')
        self.r.zone_create_view('test.com', 'other')
        self.r.rr_create(name='a.test.com.', type='a', ip='12.0.0.1', views=['default', 'other'])
        self.r.rr_delete(name='a.test.com.', type='a', ip='12.0.0.1', views=['default'])
        assert rrs(self.r.rr_list()) == rrs([
                ('a', 'test.com', 'A', '12.0.0.1'),
                ('1', '0.0.12.in-addr.arpa', 'PTR', 'a.test.com.')])

    def test_rr_delete_by_id(self):
        self.r.zone_create('test.com')
        self.r.rr_create(name='a.test.com.', type='a', ip='12.0.0.1')
        rr_id = self.r.rr_get_references(name='a.test.com.', type='A')['root']
        with raises(InvalidParameterError):
            self.r.rr_delete(ids=rr_id)
            self.r.rr_delete(ids=[rr_id], zone='a.de')
            self.r.rr_delete(ids=[rr_id], unknown='a')
        self.r.rr_delete(ids=[rr_id])

    def test_ptr_overwrite(self):
        self.r.ipblock_create('12.0.0.0/8', status='Container')
        self.r.ippool_create('test')
        self.r.ippool_add_subnet('test', '12.0.0.0/24')
        self.r.ip_mark('12.0.0.1')
        self.r.ip_mark('12.0.0.2')
        self.r.zone_create('test.com')
        self.r.rr_create(name='a.test.com.', type='A', ip='12.0.0.1')
        self.r.rr_create(ip='12.0.0.1', type='PTR', ptrdname='b.test.com.')
        assert rrs(self.r.rr_list(pattern='*')) == rrs(
            [('a', 'test.com', 'A', '12.0.0.1'),
             ('b', 'test.com', 'A', '12.0.0.1'),
             ('1', '0.0.12.in-addr.arpa', 'PTR', 'a.test.com.')])
        self.r.rr_create(ip='12.0.0.1', type='PTR', ptrdname='b.test.com.', overwrite_ptr=True)
        assert rrs(self.r.rr_list(pattern='*')) == rrs(
            [('a', 'test.com', 'A', '12.0.0.1'),
             ('b', 'test.com', 'A', '12.0.0.1'),
             ('1', '0.0.12.in-addr.arpa', 'PTR', 'b.test.com.')])
        self.r.rr_create(name='b.test.com.', type='A', ip='12.0.0.2')
        self.r.rr_create(ip='12.0.0.1', type='PTR', ptrdname='b.test.com.', overwrite_ptr=True, overwrite_a=True)
        assert rrs(self.r.rr_list(pattern='*')) == rrs(
            [('a', 'test.com', 'A', '12.0.0.1'),
             ('b', 'test.com', 'A', '12.0.0.1'),
             ('1', '0.0.12.in-addr.arpa', 'PTR', 'b.test.com.'),
             ('2', '0.0.12.in-addr.arpa', 'PTR', 'b.test.com.')])

    def test_create_a(self):
        self.r.ip_mark('12.0.0.1')
        self.r.ip_mark('12.0.0.2')
        self.r.ip_mark('12::1')
        self.r.ip_mark('12::2')
        self.r.zone_create('test.com')
        self.r.rr_create(name='a.test.com.', type='A', ip='12.0.0.1', ttl=1)
        self.r.rr_create(name='b.test.com.', type='A', ip='12.0.0.2')
        self.r.rr_create(name='c.test.com.', type='AAAA', ip='12::1')
        self.r.rr_create(name='d.test.com.', type='AAAA', ip='12::2')
        assert rrs(self.r.rr_list('*test.com.')) == rrs(
            [('a', 'test.com', 'A', '12.0.0.1'),
             ('b', 'test.com', 'A', '12.0.0.2'),
             ('c', 'test.com', 'AAAA', '12::1'),
             ('d', 'test.com', 'AAAA', '12::2')])

    def test_create_a2(self):
        # ND-57
        self.r.zone_create('test.com')
        with raises(InvalidParameterError):
            self.r.rr_create(name='test.com.', type='A', ip='::1')
        with raises(InvalidParameterError):
            self.r.rr_create(name='test.com.', type='AAAA', ip='127.0.0.1')
        with raises(InvalidParameterError):
            self.r.rr_get_attrs(name='test.com', type='A', ip='::1')
        with raises(InvalidParameterError):
            self.r.rr_get_attrs(name='test.com', type='AAAA', ip='0.0.0.1')
        self.r.rr_create(name='test.com.', type='AAAA', ip='::1')
        assert rrs(self.r.rr_list('*test.com.')) == rrs(
            [('@', 'test.com', 'AAAA', '::1')])
        self.r.rr_get_attrs(name='test.com.', type='AAAA', ip='::1')

    def test_create_cname(self):
        self.r.zone_create('test.com')
        with raises(InvalidParameterError):
            self.r.rr_create(name='a.test.com', type='CNAME', cname='c.test.com')
        self.r.rr_create(name='a.test.com.', type='CNAME', cname='c.test.com.')
        self.r.rr_create(name='b.test.com.', type='MX', preference=10, exchange='test.com.')
        with raises(InvalidParameterError):
            self.r.rr_create(name='b.test.com', type='CNAME', cname='c.test.com')
        with raises(InvalidParameterError):
            self.r.rr_create(name='d.test.com.', type='MX', preference=10, exchange='a.test.com.')

    def test_create_cname_2(self):
        # ND-100
        self.r.zone_create('test.com')
        self.r.rr_create(name='cname.test.com.', type='CNAME', cname='test.com.')
        self.r.ipblock_create('12.0.0.0/8', status='Container')
        self.r.ippool_create('test')
        self.r.ippool_add_subnet('test', '12.0.0.0/24')
        self.r.rr_create(ip='12.0.0.1', type='PTR', ptrdname='cname.test.com.', create_linked=False)
        with raises(InvalidParameterError):
            self.r.rr_create(ip='12.0.0.1', type='PTR', ptrdname='cname.test.com.', create_linked=True)

    def test_create_srv(self):
        self.r.zone_create('test.com')
        self.r.rr_create(name='a.test.com.', type='CNAME', cname='c.test.com.')
        with raises(InvalidParameterError):
            self.r.rr_create(name='_a._b.test.com.', type='SRV', priority=10, weight=1, port=1, target='a.test.com.')
        self.r.rr_create(name='_a._b.test.com.', type='SRV', priority=10, weight=1, port=1, target='c.test.com.')
        with raises(InvalidParameterError):
            self.r.rr_create(name='c.test.com.', type='CNAME', cname='a.test.com.')

    def test_email(self):
        self.r.zone_create('test.com')
        self.r.zone_set_soa_attrs('test.com', {'mail': 'first.last.test.com.'})
        assert " first.last.test.com. " in self.r.zone_dump('test.com')

    def test_create_revzone(self):
        self.r.rr_create(ip='12.0.0.1', type='PTR', ptrdname='test.com.', create_linked=False, create_revzone=True)

    def test_create_rr_rp(self):
        self.r.zone_create('test.com')
        self.r.rr_create(name='a.test.com.', type='RP', mbox='john.doe.example.com.', txtdname='test.com.')

    def test_create_rr_cert(self):
        self.r.zone_create('test.com')
        self.r.rr_create(name='a.test.com.', type='CERT', certificate_type=1, key_tag=2, algorithm=3, certificate='abc')
        with raises(DimError):
            self.r.rr_create(name='a.test.com.', type='CERT', certificate_type=1, key_tag=2, algorithm=3, certificate='a c')

    def test_create_rr_tlsa(self):
        default = dict(name='a.test.com.',
                       type='TLSA',
                       certificate_usage=1, selector=2, matching_type=1, certificate='abcd')

        def rr_create(**kwargs):
            d = default.copy()
            d.update(kwargs)
            return self.r.rr_create(**d)

        self.r.zone_create('test.com')
        assert set(rr_create(certificate_usage=4, selector=2, matching_type=3)['messages']) == set([
            (20, 'Creating RR a TLSA 4 2 3 abcd in zone test.com'),
            (30, 'certificate_usage value 4 is unassigned'),
            (30, 'selector value 2 is unassigned'),
            (30, 'matching_type value 3 is unassigned'),
        ])
        rr_create(certificate_usage='PKIX-TA', selector='PRIVSEL', matching_type='SHA2-512')
        for k, v in (('certificate', '1 2'),
                     ('certificate', 'afcs'),
                     ('selector', -1),
                     ('matching_type', 256),
                     ('certificate_usage', 'bad')):
            with raises(DimError):
                rr_create(k=v)

    def test_rr_list_value_as_object(self):
        self.r.zone_create('test.com')
        rrs = [dict(type='TXT', strings='"a" "b"'),
               dict(type='mx', preference=5, exchange='test.com.'),
               dict(type='HINFO', os='os', cpu='cpu'),
               dict(type='a', ip='1.2.3.4'),
               dict(type='srv', priority=10, weight=1, port=1, target='a.test.com.'),
               dict(type='naptr', order=1, preference=2, flags='f', service=r'223', regexp=r'r', replacement='a.de.'),
               dict(type='cert', certificate_type=1, algorithm=2, key_tag=3, certificate='cert'),
               dict(type='rp', mbox='gigi.a.de.', txtdname='test.com.')
               ]
        for param in rrs:
            name = '_a._b.test.com.'
            self.r.rr_create(name=name, **param)
            del param['type']
            assert self.r.rr_list(name, value_as_object=True)[0]['value'] == param
            self.r.rr_delete(name=name)

    def test_root_zone_list(self):
        self.r.zone_create('.')
        self.r.rr_create(name='a.', type='TXT', strings=[''])
        assert self.r.rr_list('a.')[0]['record'] == 'a'

    def test_rr_attrs(self):
        self.r.zone_create('a.de')
        rrs = [dict(name='hinfo.a.de.', type='HINFO', os='os\\"', cpu='\\\\'),
               dict(name='mx.a.de.', type='MX', preference=10, exchange='a.de.')]
        for rr in rrs:
            self.r.rr_create(**rr)
            self.r.rr_set_ttl(ttl=300, **rr)
            self.r.rr_set_comment(comment='com', **rr)
            attrs = self.r.rr_get_attrs(**rr)
            assert attrs['comment'] == 'com'
            assert attrs['ttl'] == 300
        with raises(InvalidParameterError):
            self.r.rr_set_attrs(**rrs[0])
        for dryrun in [False, True]:
            comment = '%s' % dryrun
            ttl = int(dryrun)
            attrs = self.r.rr_set_attrs(ttl=ttl, comment=comment, dryrun=dryrun, **rr)
            assert attrs['comment'] == comment
            assert attrs['ttl'] == ttl

    def test_rr_sorting(self):
        self.r.zone_create('a.de')
        rrs = [dict(name='a.de.', type='NS', nsdname='ns.a.de.', ttl=600),
               dict(name='a.de.', type='A', ip='1.2.3.4', ttl=3600),
               dict(name='*.b.a.de.', type='CNAME', cname='b.a.de.'),
               dict(name='mx.a.de.', type='MX', preference=10, exchange='a.de.')]
        for rr in rrs:
            self.r.rr_create(**rr)
        assert(self.r.rr_list(zone='a.de', limit=2)[1]['record'] == '@')

    # TODO: test rr_list(created_by, modified_by)


class PTR(RPCTest):
    def setUp(self):
        RPCTest.setUp(self)
        # Initial setup:
        # Forward Zone:
        #     w1.zone.   IN A     	12.0.0.13
        # Reverse Zone:
        #     13.0.0.12.in-addr.arpa IN PTR   w1.zone.
        #     14.0.0.12.in-addr.arpa IN PTR   w2.zone.
        self.r.ipblock_create('12.0.0.0/8', status='Container')
        self.r.ippool_create("pool")
        self.r.ippool_add_subnet("pool", "12.0.0.0/24")
        self.r.ip_mark('12.0.0.13')
        self.r.ip_mark('12.0.0.14')
        self.r.ip_mark('12.0.0.15')
        self.r.rr_create(ip='12.0.0.14', type='PTR', ptrdname='w2.zone.')
        self.r.zone_create("zone")
        self.r.rr_create(name="w1.zone.", type='A', ip="12.0.0.13")
        assert rrs(self.r.rr_list('*zone.')) == rrs(
            [('w1', 'zone', 'A', '12.0.0.13'),
             ('14', '0.0.12.in-addr.arpa', 'PTR', 'w2.zone.'),
             ('13', '0.0.12.in-addr.arpa', 'PTR', 'w1.zone.')])

    def test_new(self):
        self.r.rr_create(ip='12.0.0.15', type='PTR', ptrdname='w2.zone.')
        assert rrs(self.r.rr_list('12.0.0.15')) == rrs(
            [('15', '0.0.12.in-addr.arpa', 'PTR', 'w2.zone.'),
             ('w2', 'zone', 'A', '12.0.0.15')])

    def test_no_overwrite(self):
        assert self.r.rr_create(type='PTR', ip='12.0.0.13', ptrdname='w3.zone.')['messages'] == [
            (30, 'Not overwriting: 13.0.0.12.in-addr.arpa. PTR w1.zone.'),
            (20, 'Creating RR w3 A 12.0.0.13 in zone zone')]
        assert rrs(self.r.rr_list('12.0.0.13')) == rrs([
                ('w1', 'zone', 'A', '12.0.0.13'),
                ('w3', 'zone', 'A', '12.0.0.13'),
                ('13', '0.0.12.in-addr.arpa', 'PTR', 'w1.zone.')])

    def test_overwrite(self):
        assert set(self.r.rr_create(type='PTR', ip='12.0.0.13', ptrdname='w3.zone.', overwrite_ptr=True)['messages']) == set([
            (30, 'Deleting RR 13 PTR w1.zone. from zone 0.0.12.in-addr.arpa'),
            (20, 'Creating RR 13 PTR w3.zone. in zone 0.0.12.in-addr.arpa'),
            (20, 'Creating RR w3 A 12.0.0.13 in zone zone')])
        assert rrs(self.r.rr_list('12.0.0.13')) == rrs(
            [('w1', 'zone', 'A', '12.0.0.13'),
             ('13', '0.0.12.in-addr.arpa', 'PTR', 'w3.zone.'),
             ('w3', 'zone', 'A', '12.0.0.13')])


class TXT(RPCTest):
    def setUp(self):
        RPCTest.setUp(self)
        self.r.zone_create('test.com')

    def _simple_quote(self, txt: str):
        return '"' + txt + '"'

    def test_parse(self):
        for txt in ('unquoted', '"', '\\"', '\\', '"\\"', '"\\', '"\\0"', '"\\999"', 'a"b"', '"a"b', '"""', '"\\\\\\"'):
            with raises(InvalidParameterError):
                self.r.rr_create(name='a.test.com.', type='TXT', txt=txt)
        canonical = {'"simple"': '"simple"',
                     '"ignore" \t\n"whitespace"': '"ignore" "whitespace"',
                     '"regular escape\\100"': '"regular escaped"',
                     '"preserved escape\\\\\\"\\244"': '"preserved escape\\\\\\"\\244"',
                     '""': '',
                     '"" "a"': '"a"',
                     '"a" ""': '"a"',
                     r'"\\" "\"" "\223"': r'"\\" "\"" "\223"',
                      # splitting values longer than 255 bytes
                     self._simple_quote('a' * 255): self._simple_quote('a' * 255),
                     self._simple_quote('a' * 256): ' '.join([self._simple_quote('a' * 255), self._simple_quote('a' * 1)]),
                     self._simple_quote('a' * 513): ' '.join([self._simple_quote('a' * 255), self._simple_quote('a' * 255), self._simple_quote('a' * 3)])}
        for i, original in enumerate(canonical.keys()):
            rr_name = '%d.test.com.' % i
            self.r.rr_create(name=rr_name, type='TXT', strings=original)
            assert self.r.rr_list(rr_name)[0]['value'] == canonical[original]

    def test_dname_create(self):
        """Test basic DNAME record creation"""
        self.r.zone_create('old.example.com')
        self.r.zone_create('new.example.com')
        
        # Create basic DNAME record
        self.r.rr_create(name='dept.old.example.com.', type='DNAME', target='dept.new.example.com.')
        rrs_result = self.r.rr_list('dept.old.example.com.')
        assert len(rrs_result) == 1
        assert rrs_result[0]['type'] == 'DNAME'
        assert rrs_result[0]['value'] == 'dept.new.example.com.'

    def test_dname_zone_apex_forbidden(self):
        """Test that DNAME cannot be created at zone apex"""
        self.r.zone_create('old.example.com')
        self.r.zone_create('new.example.com')
        
        with raises(InvalidParameterError, 'It is not allowed to create a DNAME for a zone'):
            self.r.rr_create(name='old.example.com.', type='DNAME', target='new.example.com.')

    def test_dname_conflicts_with_other_records(self):
        """Test that DNAME cannot coexist with other records at same name"""
        self.r.zone_create('test.com')
        
        # Create A record first
        self.r.rr_create(name='conflict.test.com.', type='A', ip='1.2.3.4')
        
        # Try to create DNAME at same name - should fail
        with raises(InvalidParameterError, 'cannot be created because other RRs with the same name exist'):
            self.r.rr_create(name='conflict.test.com.', type='DNAME', target='target.example.com.')

    def test_other_records_conflict_with_dname(self):
        """Test that other records cannot be created at same name as DNAME"""
        self.r.zone_create('test.com')
        
        # Create DNAME first
        self.r.rr_create(name='dept.test.com.', type='DNAME', target='dept.example.com.')
        
        # Try to create A record at same name - should fail
        with raises(InvalidParameterError, 'cannot be created because a DNAME with the same name exists'):
            self.r.rr_create(name='dept.test.com.', type='A', ip='1.2.3.4')

    def test_dname_subtree_conflict(self):
        """Test that records cannot be created under DNAME subtree"""
        self.r.zone_create('test.com')
        
        # Create DNAME first
        self.r.rr_create(name='dept.test.com.', type='DNAME', target='dept.example.com.')
        
        # Try to create record under DNAME subtree - should fail
        with raises(InvalidParameterError, 'cannot be created under DNAME subtree'):
            self.r.rr_create(name='hr.dept.test.com.', type='A', ip='1.2.3.4')

    def test_dname_existing_subtree_conflict(self):
        """Test that DNAME cannot be created if records exist under the subtree"""
        self.r.zone_create('test.com')
        
        # Create record under subtree first
        self.r.rr_create(name='marketing.dept.test.com.', type='A', ip='1.2.3.4')
        
        # Try to create DNAME - should fail
        with raises(InvalidParameterError, 'cannot be created because RRs exist under the DNAME subtree'):
            self.r.rr_create(name='dept.test.com.', type='DNAME', target='dept.example.com.')

    def test_dname_delete(self):
        """Test DNAME record deletion"""
        self.r.zone_create('old.example.com')
        self.r.zone_create('new.example.com')
        
        # Create DNAME record
        self.r.rr_create(name='dept.old.example.com.', type='DNAME', target='dept.new.example.com.')
        assert len(self.r.rr_list('dept.old.example.com.')) == 1
        
        # Delete DNAME record
        self.r.rr_delete(name='dept.old.example.com.', type='DNAME', target='dept.new.example.com.')
        assert len(self.r.rr_list('dept.old.example.com.')) == 0

    def test_dname_multiple_non_conflicting(self):
        """Test that multiple DNAME records can coexist if they don't conflict"""
        self.r.zone_create('old.example.com')
        self.r.zone_create('new.example.com')
        
        # Create multiple DNAME records
        self.r.rr_create(name='dept1.old.example.com.', type='DNAME', target='dept1.new.example.com.')
        self.r.rr_create(name='dept2.old.example.com.', type='DNAME', target='dept2.new.example.com.')
        
        # Both should exist
        assert len(self.r.rr_list('dept1.old.example.com.')) == 1
        assert len(self.r.rr_list('dept2.old.example.com.')) == 1


class IpblockRRs(RPCTest):
    def setUp(self):
        RPCTest.setUp(self)
        self.r.ipblock_create('12.0.0.0/8', status='Container')
        self.r.ippool_create('test')
        self.r.ippool_add_subnet('test', '12.0.0.0/24')
        self.r.zone_create('test.com')
        self.r.rr_create(type='PTR', ptrdname='test.com.', ip='12.0.0.1')
        assert len(rrs(self.r.rr_list())) == 2

    def test_free_ip_simple(self):
        self.r.ip_free('12.0.0.1')
        assert len(rrs(self.r.rr_list('*'))) == 0

    def test_free_ip_cname(self):
        self.r.rr_create(name='a.test.com.', type='CNAME', cname='test.com.')
        self.r.rr_create(name='b.test.com.', type='CNAME', cname='a.test.com.')
        self.r.rr_create(name='c.test.com.', type='CNAME', cname='b.test.com.')
        self.r.ip_free('12.0.0.1')
        assert len(rrs(self.r.rr_list())) == 0

    def test_delete_pool(self):
        self.r.ippool_delete('test', force=True, delete_subnets=True)
        assert len(rrs(self.r.rr_list())) == 0


class ZoneViewTest(RPCTest):
    def setUp(self):
        RPCTest.setUp(self)
        self.r.zone_create('example.com')
        self.r.zone_rename_view('example.com', 'default', 'us')
        self.r.zone_create_view('example.com', 'de')
        self.r.zone_create_view('example.com', 'sg')
        self.r.rr_create(type='A', name='example.com.', ip='212.217.217.7', views=['us', 'de', 'sg'])
        self.r.rr_create(type='A', name='example.com.', ip='74.208.13.212', views=['us'])
        self.r.rr_create(type='A', name='example.com.', ip='7.12.212.98', views=['sg'])
        self.r.rr_create(type='MX', name='example.com.', preference=10, exchange='mx-ha1.company.de.', views=['us', 'de', 'sg'])
        self.r.rr_create(type='MX', name='example.com.', preference=10, exchange='mx-ha2.company.de.', views=['us', 'de', 'sg'])
        self.r.rr_create(type='MX', name='example.com.', preference=10, exchange='mx01.company.com.', views=['de'])
        self.r.rr_create(type='MX', name='example.com.', preference=10, exchange='mx02.company.com.', views=['de'])
        self.r.rr_create(type='MX', name='example.com.', preference=10, exchange='mx1.example.com.', views=['us'])

    def test_export_views(self):
        assert rrs(self.r.rr_list(zone='example.com', view='de')[1:]) == rrs(
            [('@', 'example.com', 'A', '212.217.217.7'),
             ('@', 'example.com', 'MX', '10 mx01.company.com.'),
             ('@', 'example.com', 'MX', '10 mx02.company.com.'),
             ('@', 'example.com', 'MX', '10 mx-ha1.company.de.'),
             ('@', 'example.com', 'MX', '10 mx-ha2.company.de.')])
        assert rrs(self.r.rr_list(zone='example.com', view='us')[1:]) == rrs(
            [('@', 'example.com', 'A', '212.217.217.7'),
             ('@', 'example.com', 'A', '74.208.13.212'),
             ('@', 'example.com', 'MX', '10 mx1.example.com.'),
             ('@', 'example.com', 'MX', '10 mx-ha1.company.de.'),
             ('@', 'example.com', 'MX', '10 mx-ha2.company.de.')])
        assert rrs(self.r.rr_list(zone='example.com', view='sg')[1:]) == rrs(
            [('@', 'example.com', 'A', '212.217.217.7'),
             ('@', 'example.com', 'A', '7.12.212.98'),
             ('@', 'example.com', 'MX', '10 mx-ha1.company.de.'),
             ('@', 'example.com', 'MX', '10 mx-ha2.company.de.')])

    def test_favorites(self):
        assert not self.r.zone_favorite('example.com')
        self.r.zone_favorite_add('example.com', view='us')
        assert self.r.zone_favorite('example.com', 'us')
        assert not self.r.zone_favorite('example.com', 'de')
        self.r.zone_favorite_add('example.com', view='us')
        assert self.r.zone_favorite('example.com', 'us')

        self.r.zone_favorite_add('example.com', view='de')
        assert self.r.zone_favorite('example.com', 'de')
        assert self.r.zone_favorite('example.com', 'us')

        fav = self.r.zone_list2(favorite_only=True)['data']
        assert len(fav) == 1
        assert len(fav[0]['views']) == 2

        self.r.zone_favorite_remove('example.com', view='us')
        assert not self.r.zone_favorite('example.com', 'us')
        assert self.r.zone_favorite('example.com', 'de')

        self.r.zone_favorite_remove('example.com', view='de')
        assert not self.r.zone_favorite('example.com', 'de')
        assert not self.r.zone_favorite('example.com', 'us')


def no_warn(result):
    assert [x for x in result['messages'] if x[0] == 30] == []


class RRReferencesTest(RPCTest):
    def setUp(self):
        RPCTest.setUp(self)
        db.session.execute('ALTER TABLE rr AUTO_INCREMENT = 1')
        self.r.ipblock_create('1.0.0.0/8', status='Container')
        self.r.ippool_create('p')
        self.r.ippool_add_subnet('p', '1.1.1.0/24')
        self.r.zone_create('a.de')
        self.r.zone_create_view('a.de', 'second')
        self.r.rr_create(type='A', name='a.de.', ip='1.1.1.1', views=['default', 'second'])
        self.r.zone_create('b.de')
        self.r.rr_create(type='MX', name='mx.b.de.', preference=10, exchange='a.de.')
        self.r.zone_create('c.de')
        self.r.rr_create(type='CNAME', name='cname.c.de.', cname='mx.b.de.')
        self.r.zone_create('subzone.a.de')
        self.r.zone_delete_view('subzone.a.de', 'second')
        self.r.zone_create_view('subzone.a.de', 'third')
        nodes = [{'id': 1,
                  'name': 'a.de.',
                  'type': 'A',
                  'value': '1.1.1.1',
                  'view': 'default',
                  'zone': 'a.de'},
                 {'id': 2,
                  'name': '1.1.1.1.in-addr.arpa.',
                  'type': 'PTR',
                  'value': 'a.de.',
                  'view': 'default',
                  'zone': '1.1.1.in-addr.arpa'},
                 {'id': 3,
                  'name': 'a.de.',
                  'type': 'A',
                  'value': '1.1.1.1',
                  'view': 'second',
                  'zone': 'a.de'},
                 {'id': 4,
                  'name': 'mx.b.de.',
                  'type': 'MX',
                  'value': '10 a.de.',
                  'view': 'default',
                  'zone': 'b.de'},
                 {'id': 5,
                  'name': 'cname.c.de.',
                  'type': 'CNAME',
                  'value': 'mx.b.de.',
                  'view': 'default',
                  'zone': 'c.de'}]
        self.nodes = {}
        for node in nodes:
            self.nodes[node['id']] = node
        self.mx_ref_result = {'graph': {4: [5], 5: []},
                              'records': [self.nodes[i] for i in [4, 5]],
                              'root': 4}

    def test_get_references(self):
        a_rr = dict(name='a.de.', type='A', view='second', ip='1.1.1.1')
        assert self.r.rr_get_references(delete=True, **a_rr) == \
            {'graph': {3: [4], 4: [5], 5: []},
             'records': [self.nodes[i] for i in [3, 4, 5]],
             'root': 3}
        assert self.r.rr_get_references(delete=False, **a_rr) == \
            {'graph': {3: [4], 4: []},
             'records': [self.nodes[i] for i in [3, 4]],
             'root': 3}
        ptr_rr = dict(name='1.1.1.1.in-addr.arpa.', type='PTR', ptrdname='a.de.')
        assert self.r.rr_get_references(delete=True, **ptr_rr) == \
            {'graph': {1: [4], 2: [1, 3], 3: [4], 4: [5], 5: []},
             'records': [self.nodes[i] for i in [1, 2, 3, 4, 5]],
             'root': 2}
        assert self.r.rr_get_references(delete=False, **ptr_rr) == \
            {'graph': {1: [4], 2: [1, 3], 3: [4], 4: []},
             'records': [self.nodes[i] for i in [1, 2, 3, 4]],
             'root': 2}
        mx_rr = dict(name='mx.b.de.', type='MX', exchange='a.de.', preference=10)
        assert self.r.rr_get_references(delete=True, **mx_rr) == self.mx_ref_result
        assert self.r.rr_get_references(delete=False, **mx_rr) == self.mx_ref_result

        self.r.zone_delete_view('a.de', 'default', cleanup=True)
        assert self.r.rr_get_references(delete=True, **a_rr) == \
            {'graph': {2: [], 3: [4, 2], 4: [5], 5: []},
             'records': [self.nodes[i] for i in [2, 3, 4, 5]],
             'root': 3}
        assert self.r.rr_get_references(delete=False, **a_rr) == \
            {'graph': {2: [], 3: [4, 2], 4: []},
             'records': [self.nodes[i] for i in [2, 3, 4]],
             'root': 3}

    def test_edit_comment_ttl(self):
        no_warn(self.r.rr_edit(2))
        no_warn(self.r.rr_edit(2, comment='comment'))
        no_warn(self.r.rr_edit(2, comment=None))
        no_warn(self.r.rr_edit(2, ttl=77))
        no_warn(self.r.rr_edit(2, ttl=None))
        no_warn(self.r.rr_edit(2, references=[1, 3, 4], comment='comment', ttl=77))
        ptr_rr = dict(name='1.1.1.1.in-addr.arpa.', type='PTR', ptrdname='a.de.')
        assert self.r.rr_get_references(delete=False, **ptr_rr) == \
            {'graph': {1: [4], 2: [1, 3], 3: [4], 4: []},
             'records': [self.nodes[i] for i in [1, 2, 3, 4]],
             'root': 2}
        attrs = self.r.rr_get_attrs(**ptr_rr)
        assert attrs['comment'] == 'comment'
        assert attrs['ttl'] == 77

    def test_edit_no_diff(self):
        props = dict(name='mx.b.de.', comment=None, ttl=None, preference=10, exchange='a.de.', views=['default'])
        import itertools
        for i in range(len(props)):
            no_warn(self.r.rr_edit(4, dict([x[0] for x in itertools.combinations(iter(props.items()), i + 1)])))
        no_warn(self.r.rr_edit(4, name='mx.b.de.'))
        assert self.r.rr_get_references(delete=False, type='MX', name='mx.b.de.') == \
            {'graph': {4: [5], 5: []},
             'records': [self.nodes[i] for i in [4, 5]],
             'root': 4}

    def test_edit_no_references(self):
        no_warn(self.r.rr_edit(4, references=[5], preference=20))
        assert rrs(self.r.rr_list(zone='b.de')) == rrs([('mx', 'b.de', 'MX', '20 a.de.')])
        assert self.r.rr_get_references(name='mx.b.de.')['graph'][6] == [5]

    def test_edit_references(self):
        no_warn(self.r.rr_edit(4, references=[5], name='mx2.b.de.'))
        assert rrs(self.r.rr_list(zone='b.de')) == rrs([
                ('mx2', 'b.de', 'MX', '10 a.de.')])
        assert rrs(self.r.rr_list(zone='c.de')) == rrs([
                ('cname', 'c.de', 'CNAME', 'mx2.b.de.')])

    # TODO fix this; it's probably bad
    def test_edit_fail(self):
        self.r.rr_create(name='mx2.b.de.', type='CNAME', cname='smth')
        with raises(InvalidParameterError):
            self.r.rr_edit(77)
            self.r.rr_edit(4, references=[77])
            self.r.rr_edit(4, cname='smth.')
            self.r.rr_edit(4, exchange='smth.')
            self.r.rr_edit(4, references=[5], name='mx2.b.de.')

    def test_edit_subzone(self):
        self.r.rr_edit(2, references=[1, 3, 4], ptrdname='subzone.a.de.', views=['default', 'third'])
        assert rrs(self.r.rr_list(zone='1.1.1.in-addr.arpa')) ==\
            rrs([('1', '1.1.1.in-addr.arpa', 'PTR', 'subzone.a.de.')])
        a_rrs = rrs([('@', 'subzone.a.de', 'A', '1.1.1.1')])
        for view in ['default', 'third']:
            assert rrs(self.r.rr_list(zone='subzone.a.de', type='A', view=view)) == a_rrs

    def test_edit_a_ip_with_ref(self):
        self.r.rr_edit(1, references=[2], ip='1.1.1.2')
        assert rrs(self.r.rr_list(zone='1.1.1.in-addr.arpa')) ==\
            rrs([('2', '1.1.1.in-addr.arpa', 'PTR', 'a.de.')])
        assert rrs(self.r.rr_list(zone='a.de', view='default')) == rrs([('@', 'a.de', 'A', '1.1.1.2')])
        assert rrs(self.r.rr_list(zone='a.de', view='second')) == rrs([('@', 'a.de', 'A', '1.1.1.1')])

    def test_edit_a_ip_no_ref(self):
        self.r.rr_edit(1, references=None, ip='1.1.1.2')
        assert rrs(self.r.rr_list(zone='1.1.1.in-addr.arpa')) ==\
            rrs([('1', '1.1.1.in-addr.arpa', 'PTR', 'a.de.'),
                 ('2', '1.1.1.in-addr.arpa', 'PTR', 'a.de.')])
        assert rrs(self.r.rr_list(zone='a.de', view='default')) == rrs([('@', 'a.de', 'A', '1.1.1.2')])
        assert rrs(self.r.rr_list(zone='a.de', view='second')) == rrs([('@', 'a.de', 'A', '1.1.1.1')])

    def test_edit_ptr_ip_no_ref(self):
        self.r.rr_edit(2, references=None, ip='1.1.1.2')
        assert rrs(self.r.rr_list(zone='1.1.1.in-addr.arpa')) ==\
            rrs([('2', '1.1.1.in-addr.arpa', 'PTR', 'a.de.')])
        assert rrs(self.r.rr_list(zone='a.de', view='second')) == rrs([('@', 'a.de', 'A', '1.1.1.1')])
        assert rrs(self.r.rr_list(zone='a.de', view='default')) == rrs([('@', 'a.de', 'A', '1.1.1.1')])

    def test_edit_ptr_ip_with_ref(self):
        self.r.rr_edit(2, references=[1], ip='1.1.1.2')
        assert rrs(self.r.rr_list(zone='1.1.1.in-addr.arpa')) ==\
            rrs([('2', '1.1.1.in-addr.arpa', 'PTR', 'a.de.')])
        assert rrs(self.r.rr_list(zone='a.de', view='default')) == rrs([('@', 'a.de', 'A', '1.1.1.2')])
        assert rrs(self.r.rr_list(zone='a.de', view='second')) == rrs([('@', 'a.de', 'A', '1.1.1.1')])

    def test_edit_a_name_no_ref(self):
        self.r.rr_edit(1, references=None, name='new.a.de.')
        assert rrs(self.r.rr_list(zone='1.1.1.in-addr.arpa')) ==\
            rrs([('1', '1.1.1.in-addr.arpa', 'PTR', 'a.de.')])
        assert rrs(self.r.rr_list(zone='a.de', view='default')) == rrs([('new', 'a.de', 'A', '1.1.1.1')])
        assert rrs(self.r.rr_list(zone='a.de', view='second')) == rrs([('@', 'a.de', 'A', '1.1.1.1')])

    def test_edit_a_name_no_ref2(self):
        self.r.zone_delete_view('a.de', 'second', True)
        self.r.rr_edit(1, references=None, name='new.a.de.')
        assert rrs(self.r.rr_list(zone='1.1.1.in-addr.arpa')) ==\
            rrs([('1', '1.1.1.in-addr.arpa', 'PTR', 'new.a.de.')])
        assert rrs(self.r.rr_list(zone='a.de', view='default')) == rrs([('new', 'a.de', 'A', '1.1.1.1')])

    def test_edit_a_name_with_ref(self):
        self.r.rr_edit(1, references=[2], name='new.a.de.')
        assert rrs(self.r.rr_list(zone='1.1.1.in-addr.arpa')) ==\
            rrs([('1', '1.1.1.in-addr.arpa', 'PTR', 'new.a.de.')])
        assert rrs(self.r.rr_list(zone='a.de', view='default')) == rrs([('new', 'a.de', 'A', '1.1.1.1')])
        assert rrs(self.r.rr_list(zone='a.de', view='second')) == rrs([('@', 'a.de', 'A', '1.1.1.1')])

    def test_edit_ptr_name_no_ref(self):
        self.r.rr_edit(2, references=[], ptrdname='new.a.de.')
        assert rrs(self.r.rr_list(zone='1.1.1.in-addr.arpa')) ==\
            rrs([('1', '1.1.1.in-addr.arpa', 'PTR', 'new.a.de.')])
        assert rrs(self.r.rr_list(zone='a.de', view='default')) == rrs([('@', 'a.de', 'A', '1.1.1.1')])
        assert rrs(self.r.rr_list(zone='a.de', view='second')) == rrs([('@', 'a.de', 'A', '1.1.1.1')])

    def test_edit_ptr_name_with_ref(self):
        self.r.rr_edit(2, references=[1], ptrdname='new.a.de.')
        assert rrs(self.r.rr_list(zone='1.1.1.in-addr.arpa')) ==\
            rrs([('1', '1.1.1.in-addr.arpa', 'PTR', 'new.a.de.')])
        assert rrs(self.r.rr_list(zone='a.de', view='default')) == rrs([('new', 'a.de', 'A', '1.1.1.1')])
        assert rrs(self.r.rr_list(zone='a.de', view='second')) == rrs([('@', 'a.de', 'A', '1.1.1.1')])

    def test_edit_cname_with_ref(self):
        self.r.zone_create_view('c.de', 'second')
        self.r.rr_create(type='CNAME', name='cname.c.de.', cname='mx.b.de.', views=['second'])
        self.r.rr_edit(id=4, name='mx2.b.de.')
        for view in ['default', 'second']:
            assert rrs(self.r.rr_list(zone='c.de', view=view, type='cname')) == rrs([('cname', 'c.de', 'CNAME', 'mx.b.de.')])
        self.r.rr_edit(id=7, name='mx3.b.de.', references=[5])
        for view, cname in [('default', 'mx3.b.de.'), ('second', 'mx.b.de.')]:
            assert rrs(self.r.rr_list(zone='c.de', view=view, type='cname')) == rrs([('cname', 'c.de', 'CNAME', cname)])
