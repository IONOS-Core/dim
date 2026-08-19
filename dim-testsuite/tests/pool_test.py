# -*- coding: utf-8 -*-
from tests.util import RPCTest, raises, query_ip
from dim.ipaddr import IP
from dim.models import db, Pool, Ipblock, PoolAttr, Vlan
from dim.errors import InvalidVLANError, AlreadyExistsError, InvalidIPError, InvalidStatusError, \
    NotInPoolError, InvalidPriorityError, InvalidParameterError


def get_pool(name):
    return Pool.query.filter_by(name=name).first()


class PoolTest(RPCTest):
    def test_create_remove(self):
        with raises(InvalidVLANError):
            self.r.ippool_create('invalid', vlan=0)
        with raises(InvalidVLANError):
            self.r.ippool_create('invalid', vlan=4095)
        with raises(InvalidVLANError):
            self.r.ippool_create('invalid', vlan='test')
        self.r.ippool_create('valid', vlan='22')
        self.r.ippool_create('test')
        with raises(AlreadyExistsError):
            self.r.ippool_create('test')
        assert get_pool('test')
        assert self.r.ippool_delete('test')
        assert not get_pool('test')

    def test_create_remove_reserved(self):
        self.r.ipblock_create('12.0.0.0/8', status='Container')
        self.r.ippool_create('test')
        self.r.ippool_add_subnet('test', '12.0.0.0/25')
        assert self.r.ipblock_get_attrs('12.0.0.0')['status'] == 'Reserved'
        assert self.r.ipblock_get_attrs('12.0.0.127')['status'] == 'Reserved'
        self.r.ipblock_remove('12.0.0.0/25', status='Subnet', pool='test')
        self.r.ipblock_remove('12.0.0.0/8')
        assert self.r.ipblock_get_attrs('12.0.0.0')['status'] == 'Unmanaged'
        assert self.r.ipblock_get_attrs('12.0.0.127')['status'] == 'Unmanaged'
        assert self.r.ippool_delete('test')

    def test_force_remove(self):
        self.r.ipblock_create('12.0.0.0/8', status='Container')
        self.r.ippool_create('test', vlan=4)
        assert get_pool('test')
        self.r.ippool_add_subnet('test', '12.0.0.0/24')
        assert not self.r.ippool_delete('test')
        assert self.r.ippool_delete('test', force=True, delete_subnets=True)
        assert not get_pool('test')

    def test_create_invalid(self):
        with raises(ValueError):
            self.r.ippool_create('ü')
        with raises(InvalidParameterError):
            self.r.ippool_create('a' * 300)

    def test_rename(self):
        self.r.ippool_create('old')
        self.r.ippool_rename('old', 'new')
        assert not get_pool('old')
        assert get_pool('new')
        with raises(InvalidParameterError):
            self.r.ippool_rename('new', 'a' * 300)

    def test_reserves(self):
        self.r.ipblock_create('12::/32', status='Container')
        self.r.ippool_create('v6')
        self.r.ippool_add_subnet('v6', '12::/64')
        assert Ipblock.query.count() == 3
        assert self.r.ippool_delete('v6', force=True, delete_subnets=True)

        self.r.ipblock_create('12.0.0.0/8', status='Container')
        self.r.ippool_create('v4')
        self.r.ippool_add_subnet('v4', '12.0.0.0/23')
        assert query_ip('12.0.0.0').first().status.name == 'Reserved'
        assert query_ip('12.0.0.255').first().status.name == 'Reserved'
        assert query_ip('12.0.1.0').first().status.name == 'Reserved'
        assert query_ip('12.0.0.255').first().status.name == 'Reserved'
        assert self.r.ippool_delete('v4', force=True, delete_subnets=True)

        # .0 and .255 addresses should always be Reserved
        self.r.ippool_create('v4')
        self.r.ippool_add_subnet('v4', '12.0.0.0/23', dont_reserve_network_broadcast=True)
        assert query_ip('12.0.0.0').first() is None
        assert query_ip('12.0.0.255').first() is None
        assert query_ip('12.0.1.0').first() is None
        assert query_ip('12.0.0.255').first() is None
        assert self.r.ippool_delete('v4', force=True, delete_subnets=True)

        self.r.ippool_create('v4')
        self.r.ippool_add_subnet('v4', '12.0.0.64/26')
        assert query_ip('12.0.0.64').first().status.name == 'Reserved'
        assert query_ip('12.0.0.127').first().status.name == 'Reserved'
        assert self.r.ippool_delete('v4', force=True, delete_subnets=True)

        self.r.ippool_create('v4')
        self.r.ippool_add_subnet('v4', '12.0.0.64/26', dont_reserve_network_broadcast=True)
        assert query_ip('12.0.0.64').first() is None
        assert query_ip('12.0.0.127').first() is None
        assert self.r.ippool_delete('v4', force=True, delete_subnets=True)

    def test_attrs(self):
        self.r.ippool_create("control", attributes={'team': '1'})

        create_attrs = {'country': 'ro', 'team': 'IT Operations'}
        self.r.ippool_create("pool_attrs", attributes=create_attrs)

        self.assertDictSubset(self.r.ippool_get_attrs('pool_attrs'), create_attrs)
        self.r.ippool_set_attrs('pool_attrs', {'country': 'de'})
        self.assertDictSubset(self.r.ippool_get_attrs('pool_attrs'), {'country': 'de'})
        self.r.ippool_delete_attrs('pool_attrs', ['team'])
        assert 'team' not in self.r.ippool_get_attrs('pool_attrs')

        # Test that deleting an attribute with a colon raises an exception
        with raises(Exception):
            self.r.ippool_delete_attrs('pool_attrs', ['team:value'])

        assert self.r.ippool_get_attrs('control')['team'] == '1'
        assert 'country' not in self.r.ippool_get_attrs('control')

        assert self.r.ippool_delete('control')
        assert self.r.ippool_delete('pool_attrs')
        assert PoolAttr.query.count() == 0

    def check_ippool_add_subnet(self, pool, subnet, **options):
        self.r.ippool_add_subnet(pool, subnet, **options)
        pool = Pool.query.filter_by(name=pool).one()
        subnet = query_ip(subnet).one()
        gateway = None
        if 'gateway' in options:
            gateway = IP(options.get('gateway')).address
        assert subnet.pool == pool
        assert subnet.version == pool.version
        assert subnet.vlan == pool.vlan
        assert subnet.gateway == gateway
        self.assertEqual(dict((a.name.name, a.value) for a in subnet.attributes), options.get('attributes', {}))

    def test_create_complex(self):
        for prefix in range(12, 17):
            self.r.ipblock_create('%d.0.0.0/8' % prefix, status='Container')
        self.r.ipblock_create('2001::/16', status='Container')
        self.r.ippool_create("pool1", vlan=4)
        self.check_ippool_add_subnet('pool1', '12.0.0.0/24', gateway='12.0.0.1')
        self.check_ippool_add_subnet('pool1', '13.0.0.0/24', gateway='12.0.0.1')
        self.check_ippool_add_subnet('pool1', '14.0.0.0/23')
        self.check_ippool_add_subnet('pool1', '15.0.0.0/24', attributes={'country': 'ro', 'team': 'IP Operations'})
        with raises(AlreadyExistsError):
            self.r.ippool_add_subnet('pool1', '12.0.0.0/24')
        with raises(InvalidIPError):
            self.r.ippool_add_subnet('pool1', '2001:db8::/32')
        self.r.ipblock_create('17.0.0.0/24', status='Container')
        with raises(InvalidStatusError):
            self.r.ippool_add_subnet('pool1', '17.0.0.0/24')
        self.r.ippool_create("pool2", vlan=4)
        self.check_ippool_add_subnet('pool2', '16.0.0.0/24')
        with raises(AlreadyExistsError):
            self.r.ippool_add_subnet('pool1', '16.0.0.0/24')
        assert self.r.ipblock_remove('16.0.0.0/24', pool='pool2') == 1
        self.r.ippool_add_subnet('pool1', '16.0.0.0/24')
        with raises(NotInPoolError):
            self.r.ipblock_remove('16.0.0.0/24', pool='pool2', status='Subnet')
        assert self.r.ipblock_remove('16.0.0.0/24', pool='pool1') == 1
        self.r.ippool_set_vlan('pool2', 5)
        self.check_ippool_add_subnet('pool2', '16.0.0.0/24')
        assert self.r.ippool_delete('pool1', force=True, delete_subnets=True)
        assert self.r.ippool_delete('pool2', force=True, delete_subnets=True)

    def test_subnet_vlan(self):
        self.r.ipblock_create('16.0.0.0/8', status='Container')
        self.r.ippool_create("pool2", vlan=5)
        self.r.ippool_add_subnet('pool2', '16.0.0.0/24')
        self.r.ippool_create("pool", vlan=4)
        assert Ipblock.query_ip(IP('16.0.0.0/24'), None).one().vlan.vid == 5
        self.r.ippool_add_subnet('pool', '16.0.0.0/24', allow_move=True)
        assert Ipblock.query_ip(IP('16.0.0.0/24'), None).one().vlan.vid == 4

    def test_list_ippools(self):
        self.r.ipblock_create('20.0.0.0/8', status='Container')
        self.r.ippool_create("pool1", vlan=5)
        assert self.r.ippool_list(pool='pool1')[0]['subnets'] == []
        pool1_nets = ['20.0.0.0/24', '20.1.1.0/24']
        for net in pool1_nets:
            self.r.ippool_add_subnet("pool1", net)
        self.r.ippool_create("pool2")
        pool2_nets = ['20.1.0.0/24']
        for net in pool2_nets:
            self.r.ippool_add_subnet("pool2", net)
        l1 = self.r.ippool_list(pool='pool1')
        assert l1[0]['name'] == 'pool1'
        assert l1[0]['vlan'] == 5
        self.assertEqual(set(l1[0]['subnets']), set(pool1_nets))
        l2 = self.r.ippool_list(pool='pool2')
        assert l2[0]['name'] == 'pool2'
        assert l2[0]['vlan'] is None
        self.assertEqual(set(l2[0]['subnets']), set(pool2_nets))

        data_set = [(dict(pool='*'), ['pool1', 'pool2']),
                    (dict(pool='*1'), ['pool1']),
                    (dict(vlan=5), ['pool1']),
                    (dict(cidr='20.0.0.0/8'), ['pool1', 'pool2']),
                    (dict(cidr='20.1.0.0/24'), ['pool2']),
                    (dict(cidr='20.1.0.0/16'), ['pool1', 'pool2']),
                    ]
        for params, pools in data_set:
            self.assertEqual(set(p['name'] for p in self.r.ippool_list(**params)),
                             set(pools))
            self.assertEqual(set(p['name'] for p in self.r.ippool_list(include_subnets=False, **params)),
                             set(pools))

    def test_list_ippools_pagination(self):
        def query(limit=None, offset=0):
            pools = self.r.ippool_list(pool='pool*', include_subnets=True, limit=limit, offset=offset)
            return set([p['name'] for p in pools])
        pools = []
        for i in range(5):
            pools.append('pool%d' % (i,))
            self.r.ippool_create(pools[-1])
        self.r.ipblock_create('20.0.0.0/8', status='Container')
        self.r.ippool_add_subnet("pool0", '20.0.1.0/24')
        self.r.ippool_add_subnet("pool0", '20.0.2.0/24')
        self.r.ippool_add_subnet("pool0", '20.0.3.0/24')
        assert self.r.ippool_count(pool='pool*') == 5
        assert self.r.ippool_count(cidr='20.0.0.0/16') == 1
        assert query() == set(pools)
        assert query(limit=2) == set(pools[:2])
        assert query(limit=2, offset=2) == set(pools[2:4])
        assert query(limit=2, offset=4) == set(pools[4:])

    def test_priority(self):
        self.r.ipblock_create('20.0.0.0/8', status='Container')
        self.r.ipblock_create('13.0.0.0/8', status='Container')
        self.r.ippool_create("pool")
        self.r.ippool_add_subnet("pool", '20.0.0.0/24', gateway='20.0.0.1')
        self.r.ippool_add_subnet("pool", '20.0.1.0/24')
        self.r.ippool_add_subnet("pool", '20.0.2.0/24')
        l = self.r.ippool_get_subnets('pool')
        self.assertDictSubset(l[0], {'subnet': '20.0.0.0/24', 'priority': 1, 'gateway': '20.0.0.1'})
        self.assertDictSubset(l[1], {'subnet': '20.0.1.0/24', 'priority': 2})
        self.assertDictSubset(l[2], {'subnet': '20.0.2.0/24', 'priority': 3})

        self.r.ipblock_remove('20.0.1.0/24', pool='pool', status='Subnet')
        self.r.ippool_add_subnet('pool', '20.0.3.0/24')
        l = self.r.ippool_get_subnets('pool')
        self.assertDictSubset(l[0], {'subnet': '20.0.0.0/24', 'priority': 1})
        self.assertDictSubset(l[1], {'subnet': '20.0.2.0/24', 'priority': 3})
        self.assertDictSubset(l[2], {'subnet': '20.0.3.0/24', 'priority': 4})

        self.r.ipblock_create('12.0.0.0/24', status='Container')
        with raises(InvalidStatusError):
            self.r.subnet_set_priority('12.0.0.0/24', 1)
        with raises(InvalidPriorityError):
            self.r.subnet_set_priority('20.0.0.0/24', 'g')
        with raises(InvalidPriorityError):
            self.r.subnet_set_priority('20.0.0.0/24', 0)
        self.r.subnet_set_priority('20.0.0.0/24', '1')

        self.r.subnet_set_priority('20.0.2.0/24', 1, pool='pool')
        l = self.r.ippool_get_subnets('pool')
        self.assertDictSubset(l[0], {'subnet': '20.0.2.0/24', 'priority': 1})
        self.assertDictSubset(l[1], {'subnet': '20.0.0.0/24', 'priority': 2})
        self.assertDictSubset(l[2], {'subnet': '20.0.3.0/24', 'priority': 4})

        self.r.ippool_add_subnet('pool', '20.0.1.0/24')
        self.r.ippool_add_subnet('pool', '20.0.4.0/24')
        self.r.subnet_set_priority('20.0.4.0/24', 3, pool='pool')
        l = self.r.ippool_get_subnets('pool')
        self.assertDictSubset(l[0], {'subnet': '20.0.2.0/24', 'priority': 1})
        self.assertDictSubset(l[1], {'subnet': '20.0.0.0/24', 'priority': 2})
        self.assertDictSubset(l[2], {'subnet': '20.0.4.0/24', 'priority': 3})
        self.assertDictSubset(l[3], {'subnet': '20.0.3.0/24', 'priority': 4})
        self.assertDictSubset(l[4], {'subnet': '20.0.1.0/24', 'priority': 5})

        self.r.subnet_set_priority('20.0.4.0/24', 1, pool='pool')
        l = self.r.ippool_get_subnets('pool')
        self.assertDictSubset(l[0], {'subnet': '20.0.4.0/24', 'priority': 1})
        self.assertDictSubset(l[1], {'subnet': '20.0.2.0/24', 'priority': 2})
        self.assertDictSubset(l[2], {'subnet': '20.0.0.0/24', 'priority': 3})
        self.assertDictSubset(l[3], {'subnet': '20.0.3.0/24', 'priority': 4})
        self.assertDictSubset(l[4], {'subnet': '20.0.1.0/24', 'priority': 5})

    def test_free_total(self):
        self.r.ipblock_create('0::/8', status='Container')
        self.r.ippool_create("pool")
        self.r.ippool_add_subnet("pool", '12::/16')
        total = 2 ** (128 - 16)
        free = total - 1
        l = self.r.ippool_get_subnets('pool')
        assert l[0]['total'] == total
        assert l[0]['free'] == free

        self.r.ipblock_create('12:13::/32', status='Delegation')
        free -= 2 ** (128 - 32)
        assert self.r.ippool_get_subnets('pool')[0]['free'] == free

        for i in range(3):
            self.r.ipblock_create('12:2:%d::/48' % i, status='Delegation')
        free -= 3 * 2 ** (128 - 48)
        assert self.r.ippool_get_subnets('pool')[0]['free'] == free

        self.r.ip_mark('12::1')
        free -= 1
        assert self.r.ippool_get_subnets('pool')[0]['free'] == free

    def test_get_subnets_without_usage(self):
        self.r.ipblock_create('0::/8', status='Container')
        self.r.ippool_create("pool")
        self.r.ippool_add_subnet("pool", '12::/16')
        self.r.ippool_add_subnet("pool", '16::/16')
        assert [s['subnet'] for s in self.r.ippool_get_subnets('pool')] == ['12::/16', '16::/16']

    def test_get_delegations(self):
        self.r.ipblock_create('0::/8', status='Container')
        self.r.ippool_create("pool")
        self.r.ippool_add_subnet("pool", '12::/16', dont_reserve_network_broadcast=True)
        self.assertEqual(self.r.ippool_get_delegations("pool"), [])

        self.r.ippool_get_delegation("pool", 120)
        self.r.ippool_get_delegation("pool", 120)
        self.assertEqual(self.r.ippool_get_delegations("pool"),
                         [{'delegation': '12::/120', 'total': 256, 'free': 256},
                          {'delegation': '12::100/120', 'total': 256, 'free': 256}])

    def test_favorite(self):
        self.r.ippool_create('p')
        self.r.ippool_create('p2')
        assert not self.r.ippool_favorite('p')
        assert len(self.r.ippool_list(favorite_only=True)) == 0

        self.r.ippool_favorite_add('p')
        assert self.r.ippool_favorite('p')
        assert not self.r.ippool_favorite('p2')
        favorites = self.r.ippool_list(favorite_only=True)
        assert favorites[0]['name'] == 'p'
        assert len(favorites) == 1
        assert len(self.r.ippool_list(favorite_only=False)) == 2

        self.r.ippool_favorite_remove('p')
        assert not self.r.ippool_favorite('p')
        assert len(self.r.ippool_list(favorite_only=True)) == 0
