import sys
import textwrap
import unittest
from io import StringIO

from . import ndcli, user, net, admin, assert_match_table
from dimcli import _print_table


# Actual tests
def test_global_options():
    assert ndcli('invalid').code == 1
    assert ndcli('-D invalid').code == 32
    assert ndcli('-D -q -v').code == 32


def test_print_table():
    old_stdout = sys.stdout
    sys.stdout = mystdout = StringIO()
    _print_table(['name', dict(header='Name'),
                  'age', dict(align='r')],
                 [{'name': 'a b c', 'age': 2},
                  {'name': 'a b c d', 'age': 10}],
                 script=False,
                 width=9)
    sys.stdout = old_stdout
    assert mystdout.getvalue() == 'Name  age\na b c   2\na b c  10\nd        \n'


def test_assignmentsize():
    assert ndcli('create container 12::/32').ok
    assert ndcli('create pool test').ok
    assert ndcli('modify pool test add subnet 12::/48').ok
    assert ndcli('modify pool test set attrs assignmentsize:64').ok
    assert ndcli('list pool test -H').table == [['1', '12::/48', '', '65535', '65536']]
    assert ndcli('modify pool test remove subnet 12::/48 --force --cleanup').ok
    assert ndcli('delete pool test').ok
    assert ndcli('delete container 12::/32').ok


def test_list_vlans():
    vlans = {'': ['p_1', 'p_2', 'p_3'],
             '1': ['p1_1', 'p1_2'],
             '140': ['p140_1']}
    for vlan in list(vlans.keys()):
        for pool in vlans[vlan]:
            if vlan:
                assert ndcli('create pool %s vlan %s' % (pool, vlan)).ok
            else:
                assert ndcli('create pool %s' % pool).ok
    table = ndcli('list vlans -H').table
    for row in table:
        assert set(vlans[row[0]]) == set(row[1].split())
    for vlan in list(vlans.keys()):
        for pool in vlans[vlan]:
            assert ndcli('delete pool %s' % pool).ok


def test_list_container():
    assert ndcli('create container 87.106.0.0/16 country:es').ok
    assert ndcli('create container 87.106.208.0/20 city:barcelona').ok
    assert ndcli('create pool test').ok
    assert ndcli('modify pool test add subnet 87.106.0.0/24 room:a').ok
    assert ndcli('modify pool test add subnet 87.106.1.0/24 room:b').ok
    assert ndcli('modify pool test remove subnet 87.106.1.0/24 --force --cleanup').ok
    expected = textwrap.dedent(
        '''
        87.106.0.0/16 (Container) country:es
          87.106.0.0/24 (Subnet) pool:test room:a
          87.106.1.0/24 (Available)
          87.106.2.0/23 (Available)
          87.106.4.0/22 (Available)
          87.106.8.0/21 (Available)
          87.106.16.0/20 (Available)
          87.106.32.0/19 (Available)
          87.106.64.0/18 (Available)
          87.106.128.0/18 (Available)
          87.106.192.0/20 (Available)
          87.106.208.0/20 (Container) city:barcelona
            87.106.208.0/20 (Available)
          87.106.224.0/19 (Available)
        ''').strip()
    assert ndcli('list container').stdout.strip() == expected
    assert ndcli('list container 87.106.0.0/16').stdout.strip() == expected
    assert ndcli('modify pool test remove subnet 87.106.0.0/24 --force --cleanup').ok
    assert ndcli('delete pool test').ok
    assert ndcli('delete container 87.106.0.0/16').ok
    assert ndcli('delete container 87.106.208.0/20').ok


def test_list_ips():
    assert ndcli('create container 12::/32').ok
    assert ndcli('create pool test').ok
    assert ndcli('modify pool test add subnet 12::/48').ok
    assert ndcli('list ips test -L 1 -a ip -H').table == [['12::']]
    assert ndcli('modify pool test remove subnet 12::/48 -f -c').ok
    assert ndcli('delete pool test').ok
    assert ndcli('delete container 12::/32').ok


def test_create_zone():
    assert ndcli('create zone a-').err
    assert ndcli('create zone test.com').ok
    assert ndcli('delete zone test.com').ok
    assert ndcli('delete zone test.com').err


def test_create_profile():
    assert ndcli('create zone-profile internal').ok
    assert ndcli('create zone test.com profile internal').ok
    assert ndcli('delete zone-profile test.com').err
    assert ndcli('delete zone-profile internal').ok
    assert ndcli('delete zone-profile internal').err
    assert ndcli('delete zone test.com').ok


def test_list_zones():
    assert ndcli('create zone-profile internal').ok
    assert ndcli('create zone test.com').ok
    assert ndcli('create zone waza.com').ok
    assert_match_table(ndcli('list zones -H').table, [['test.com', '1', '0'], ['waza.com', '1', '0']])
    assert_match_table(ndcli('list zones -H *.com').table, [['test.com', '1', '0'], ['waza.com', '1', '0']])
    assert_match_table(ndcli('list zones -H test*').table, [['test.com', '1', '0']])
    assert ndcli('list zone-profiles -H').table == [['internal']]
    assert ndcli('delete zone-profile internal').ok
    assert ndcli('delete zone test.com').ok
    assert ndcli('delete zone waza.com').ok


def test_create_rr_a():
    assert ndcli('create container 12.0.0.0/8').ok
    assert ndcli('create pool test').ok
    assert ndcli('modify pool test add subnet 12.0.0.0/24').ok
    assert ndcli('modify pool test mark ip 12.0.0.1').ok
    assert ndcli('create zone test.com').ok
    assert ndcli('create rr a.test.com. ttl 10 a 12.0.0.1').ok
    assert ndcli('delete zone test.com --cleanup').ok
    # assert ndcli('delete zone 0.0.12.in-addr.arpa --cleanup').ok
    assert ndcli('modify pool test remove subnet 12.0.0.0/24 --force --cleanup').ok
    assert ndcli('delete pool test').ok
    assert ndcli('list rrs * -H').table == []
    assert ndcli('delete container 12.0.0.0/8').ok


def nosoa(table):
    return [row for row in table if 'SOA' not in row]


def test_create_rr_from():
    assert ndcli('create container 12.0.0.0/8').ok
    assert ndcli('create zone test.com').ok
    assert ndcli('create pool test').ok
    assert ndcli('modify pool test add subnet 12.0.0.0/24').ok
    assert ndcli('create rr a.test.com. ttl 10 from test -c test').ok
    assert ['a', 'test.com', 'default', '10', 'A', '12.0.0.1'] in ndcli('list rrs a.test.com. -H').table
    assert 'comment:test' in ndcli('show rr a.test.com.').stdout
    assert ndcli('delete zone test.com --cleanup').ok
    assert ndcli('modify pool test remove subnet 12.0.0.0/24 --force --cleanup').ok
    assert ndcli('delete pool test')
    assert ndcli('delete container 12.0.0.0/8').ok


def test_create_rr_ptr():
    assert ndcli('create container 12.0.0.0/8').ok
    assert ndcli('create pool test').ok
    assert ndcli('modify pool test add subnet 12.0.0.0/24').ok
    assert ndcli('modify pool test mark ip 12.0.0.1').ok
    assert ndcli('create zone test.com').ok
    assert ndcli('create rr a.test.com. a 12.0.0.1').ok
    assert ndcli('create rr 12.0.0.1 ptr b.test.com. --overwrite-ptr').ok
    assert nosoa(ndcli('list rrs * -H').table) == [
        ['1', '0.0.12.in-addr.arpa', 'default', '', 'PTR', 'b.test.com.'],
        ['a', 'test.com', 'default', '', 'A', '12.0.0.1'],
        ['b', 'test.com', 'default', '', 'A', '12.0.0.1']]
    assert ndcli('delete zone test.com --cleanup').ok
    assert ndcli('modify pool test remove subnet 12.0.0.0/24 --force --cleanup').ok
    assert ndcli('delete pool test').ok
    assert ndcli('delete container 12.0.0.0/8').ok


def test_create_rr_mx():
    assert ndcli('create zone test.com').ok
    assert ndcli('create rr a.test.com. mx 10 fake.').ok
    assert ndcli('create rr a.test.com. mx 20 take.').ok
    assert nosoa(ndcli('list rrs * -H').table) == [
        ['a', 'test.com', 'default', '', 'MX', '10 fake.'],
        ['a', 'test.com', 'default', '', 'MX', '20 take.']]
    assert ndcli('create rr a.test.com. mx 30 wake. --overwrite').ok
    assert nosoa(ndcli('list rrs * -H').table) == [
        ['a', 'test.com', 'default', '', 'MX', '30 wake.']]
    assert ndcli('delete zone test.com --cleanup').ok


def test_create_rr_ns():
    assert ndcli('create zone test.com').ok
    assert ndcli('create rr test.com. ns ns1.test.com.').ok
    assert ndcli('create rr test.com. ns ns2.test.com.').ok
    assert nosoa(ndcli('list rrs * -H').table) == [
        ['@', 'test.com', 'default', '', 'NS', 'ns1.test.com.'],
        ['@', 'test.com', 'default', '', 'NS', 'ns2.test.com.']]
    assert ndcli('create rr test.com. ns ns3.test.com. --overwrite').ok
    assert nosoa(ndcli('list rrs * -H').table) == [
        ['@', 'test.com', 'default', '', 'NS', 'ns3.test.com.']]
    assert ndcli('delete zone test.com --cleanup').ok


def test_create_rr_srv():
    assert ndcli('create zone test.com').ok
    assert ndcli('create rr _jabber._tcp.test.com. srv 5 0 5269 xmpp.test.com.').ok
    assert nosoa(ndcli('list rrs * -H').table) == [
        ['_jabber._tcp', 'test.com', 'default', '', 'SRV', '5 0 5269 xmpp.test.com.']]
    assert ndcli('delete zone test.com --cleanup').ok


def test_create_rr_txt():
    assert ndcli('create zone test.com').ok
    assert ndcli('create rr test.com. txt "za text"').ok
    assert nosoa(ndcli('list rrs * -H').table) == [
        ['@', 'test.com', 'default', '', 'TXT', '"za text"']]
    assert ndcli('delete zone test.com --cleanup').ok


def test_create_rr_cname():
    assert ndcli('create zone test.com').ok
    assert ndcli('create rr a.test.com. cname gmail.com.').ok
    assert nosoa(ndcli('list rrs * -H').table) == [
        ['a', 'test.com', 'default', '', 'CNAME', 'gmail.com.']]
    assert ndcli('delete zone test.com --cleanup').ok


def test_list_rrs_exact():
    assert ndcli('create zone test.com').ok
    assert ndcli('create rr *.test.com. a 12.0.0.1').ok
    assert ndcli('create rr a.test.com. a 12.0.0.2').ok
    wildcard = nosoa(ndcli('list rrs *.test.com. -H').table)
    assert ['*', 'test.com', 'default', '', 'A', '12.0.0.1'] in wildcard
    assert ['a', 'test.com', 'default', '', 'A', '12.0.0.2'] in wildcard
    assert nosoa(ndcli('list rrs *.test.com. --exact -H').table) == [
        ['*', 'test.com', 'default', '', 'A', '12.0.0.1']]
    assert ndcli('delete zone test.com --cleanup').ok


def test_show_rr():
    # ND-92
    assert ndcli('create zone test.com').ok
    assert ndcli('modify zone test.com create view second').ok
    assert ndcli('create rr a.test.com. hinfo a b view default second').ok
    assert ndcli('show rr a.test.com. hinfo view default').ok
    assert ndcli('show rr a.test.com. hinfo a b view default').ok
    assert ndcli('delete rr a.test.com. hinfo view second').ok
    assert ndcli('delete rr a.test.com. hinfo a b view default').ok
    assert ndcli('modify zone test.com delete view second').ok
    assert ndcli('delete zone test.com --cleanup').ok


def test_create_rr_layer3domain():
    try:
        assert ndcli('create zone a.de').ok
        assert ndcli('modify zone a.de create rr -n name ttl 10 aaaa ::1').ok
        assert ndcli('modify zone a.de create rr -n name ttl 10 aaaa ::1 --comment c').ok
        assert ndcli('modify zone a.de create rr -n name aaaa ::1 view default').ok
        assert ndcli('modify zone a.de create rr -n name aaaa ::1 view default --comment c').ok
        assert ndcli('modify zone a.de create rr -n name aaaa ::1 layer3domain default').ok
        assert ndcli('modify zone a.de create rr -n name aaaa ::1 view default layer3domain default').ok
        assert ndcli('modify zone a.de create rr -n name aaaa ::1 view default  --comment c layer3domain default').ok
        assert ndcli('create rr a.de. -n ttl 10 aaaa ::1').ok
        assert ndcli('create rr a.de. -n ttl 10 aaaa ::1 view default').ok
        assert ndcli('create rr a.de. -n ttl 10 aaaa ::1 layer3domain default').ok
        assert ndcli('create rr a.de. -n ttl 10 aaaa ::1 view default layer3domain default').ok
    finally:
        ndcli('delete zone a.de')


class RightsTest(unittest.TestCase):
    def setUp(self):
        assert user('list pools').ok
        assert net('list pools').ok
        admin('delete user-group networkgroup')
        assert admin('create user-group networkgroup').ok
        assert admin('modify user-group networkgroup grant network_admin').ok
        assert admin('modify user-group networkgroup add user net').ok

    def test_create_add_remove(self):
        assert user('create user-group usergroup').err
        assert net('create user-group usergroup').ok
        assert user('modify user-group usergroup add user user').err
        assert net('modify user-group usergroup add user user').ok
        assert user('delete user-group usergroup').err
        assert net('delete user-group usergroup').ok

    def test_delete_group(self):
        assert user('list user-groups -H').table == [['all_users'], ['networkgroup']]
        assert user('list user net groups -H').table == [['all_users'], ['networkgroup']]
        assert user('list user-group networkgroup users -H').table == [['net']]
        assert admin('delete user-group networkgroup').ok
        assert user('list user-groups -H').table == [['all_users']]
        assert user('list user net groups -H').table == [['all_users']]
        assert user('list user-group networkgroup users').err
        assert admin('delete user-group networkgroup').err
        assert admin('create user-group networkgroup').ok

    def test_allocate(self):
        assert admin('modify user-group networkgroup grant dns_admin').ok
        assert net('create container 12.0.0.0/8').ok
        assert net('create pool test_pool').ok
        assert net('modify pool test_pool subnet add 12.0.0.0/24').ok
        assert net('modify pool test_pool get ip').ok
        assert user('modify pool test_pool get ip').err

        assert net('create user-group usergroup').ok
        assert net('modify user-group usergroup add user user').ok
        assert net('modify user-group usergroup grant allocate pool').ok
        assert user('list user-group usergroup rights -H').table == [['allocate', 'pool']]

        assert net('modify user-group usergroup revoke allocate pool').ok
        assert user('modify pool test_pool get ip').err
        assert net('modify user-group usergroup grant allocate pool').ok
        assert net('modify user-group usergroup remove user user').ok
        assert user('modify pool test_pool get ip').err

        assert net('delete user-group usergroup').ok
        assert net('modify pool test_pool remove subnet 12.0.0.0/24 --force --cleanup').ok
        assert net('delete pool test_pool -D').ok
        assert net('delete container 12.0.0.0/8').ok

    def test_revoke_network_admin(self):
        assert user('list user-group networkgroup rights -H').table == [['network_admin', 'all']]
        assert admin('modify user-group networkgroup revoke network_admin')
        assert user('list user-group networkgroup rights -H').table == []

    def test_rename(self):
        assert user('list user-group networkgroup rights -H').table == [['network_admin', 'all']]
        assert admin('rename user-group networkgroup to testgroup').ok
        assert user('list user-group testgroup rights -H').table == [['network_admin', 'all']]
        assert admin('delete user-group testgroup').ok
