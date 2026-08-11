from tests.util import RPCTest
from sqlalchemy.exc import IntegrityError
import pytest


def ips(d):
    return set(o['ip'] for o in d)


class AllocatorTest(RPCTest):
    def test_delegation1(self):
        self.r.ippool_create('pool')
        self.r.ipblock_create('12.0.0.0/8', status='Container')
        self.r.ippool_add_subnet('pool', '12.0.0.0/29')
        assert ips(self.r.ippool_get_delegation('pool', 30, maxsplit=1)) == \
            set(['12.0.0.2/31', '12.0.0.4/31'])

    def test_delegation2(self):
        '''Don't allow hosts to be returned from ippool_get_delegation'''
        #  .8 __*_
        # .16 _*_*
        self.r.ippool_create('pool')
        self.r.ipblock_create('12.0.0.0/8', status='Container')
        self.r.ippool_add_subnet('pool', '12.0.0.8/29', dont_reserve_network_broadcast=True)
        self.r.ip_mark('12.0.0.10')
        self.r.ip_mark('12.0.0.13')
        self.r.ip_mark('12.0.0.15')
        assert not self.r.ippool_get_delegation('pool', 30, maxsplit=2)

    def test_delegation3(self):
        # .16 ____
        # .20 *___
        # .24 ___*
        # .28 *__*
        self.r.ippool_create('pool')
        self.r.ipblock_create('12.0.0.0/8', status='Container')
        self.r.ippool_add_subnet('pool', '12.0.0.16/28', dont_reserve_network_broadcast=True)
        self.r.ip_mark('12.0.0.20')
        self.r.ip_mark('12.0.0.27')
        self.r.ip_mark('12.0.0.28')
        self.r.ip_mark('12.0.0.31')
        assert ips(self.r.ippool_get_delegation('pool', 29, maxsplit=2)) == \
            set(['12.0.0.16/30', '12.0.0.22/31', '12.0.0.24/31'])

    def test_delegation4(self):
        # R*__
        # _**_
        # __*_
        # ____
        self.r.ippool_create('pool')
        self.r.ipblock_create('12.0.0.0/8', status='Container')
        self.r.ippool_add_subnet('pool', '12.0.0.0/28', dont_reserve_network_broadcast=True)
        self.r.ip_mark('12.0.0.1', pool='pool')
        self.r.ip_mark('12.0.0.5', pool='pool')
        self.r.ip_mark('12.0.0.6', pool='pool')
        self.r.ip_mark('12.0.0.10', pool='pool')
        d = self.r.ippool_get_delegation('pool', 29, maxsplit=2, attributes={'country': 'de'})
        assert len(d) == 3
        self.assertDictSubset(
            d[0],
            {'country': 'de',
             'pool': 'pool',
             'status': 'Delegation',
             'ip': '12.0.0.12/30',
             'subnet': '12.0.0.0/28',
             'mask': '255.255.255.240',
             })
        self.assertDictSubset(
            d[1],
            {'country': 'de',
             'pool': 'pool',
             'status': 'Delegation',
             'ip': '12.0.0.2/31',
             'subnet': '12.0.0.0/28',
             'mask': '255.255.255.240',
             })
        self.assertDictSubset(
            d[2],
            {'country': 'de',
             'pool': 'pool',
             'status': 'Delegation',
             'ip': '12.0.0.8/31',
             'subnet': '12.0.0.0/28',
             'mask': '255.255.255.240',
             })
        assert self.r.ippool_get_ip('pool')['ip'] == '12.0.0.0'
        assert self.r.ippool_get_ip('pool')['ip'] == '12.0.0.4'
        assert self.r.ippool_get_ip('pool')['ip'] == '12.0.0.7'
        assert self.r.ippool_get_ip('pool')['ip'] == '12.0.0.11'
        assert not self.r.ippool_get_ip('pool')

    def test_delegation_no_subnets(self):
        self.r.ippool_create('pool')
        assert not self.r.ippool_get_delegation('pool', 30)

    def test_delegation_full(self):
        self.r.ippool_create('pool')
        self.r.ipblock_create('12.0.0.0/8', status='Container')
        self.r.ippool_add_subnet('pool', '12.0.0.8/29', dont_reserve_network_broadcast=True)
        self.r.ipblock_create('12.0.0.8/30', status='Delegation')
        self.r.ipblock_create('12.0.0.12/30', status='Delegation')
        assert not self.r.ippool_get_delegation('pool', 31)

    def test_delegation_ipv6(self):
        self.r.ipblock_create('2001::/32', status='Container')
        self.r.ippool_create('pool')
        self.r.ippool_add_subnet('pool', '2001::/64')
        assert ips(self.r.ippool_get_delegation('pool', 96)) == set(['2001::1:0:0/96'])
        assert ips(self.r.ippool_get_delegation('pool', 96)) == set(['2001::2:0:0/96'])
        assert self.r.ipblock_get_ip('2001::1:0:0/96')['ip'] == '2001::1:0:0'

    def test_random(self):
        self.r.ipblock_create('12.0.0.0/8', status='Container')
        self.r.ippool_create('pool')
        self.r.ippool_set_attrs('pool', {'allocation_strategy': 'random'})
        self.r.ippool_add_subnet('pool', '12.0.0.32/28', dont_reserve_network_broadcast=True)
        delegations = []
        for _ in range(4):
            d = self.r.ippool_get_delegation('pool', 30)
            d_ip = d[0]['ip']
            delegations.extend(d)
            self.r.ipblock_set_attrs(d_ip, {'allocation_strategy': 'first'})
            ip1 = self.r.ipblock_get_ip(d_ip)['ip']
            ip2 = self.r.ipblock_get_ip(d_ip)['ip']
            assert ip1 < ip2
        assert len(delegations) == 4
        assert ips(delegations) == set(['12.0.0.32/30', '12.0.0.36/30', '12.0.0.40/30', '12.0.0.44/30'])
        assert not self.r.ippool_get_delegation('pool', 30)

    def test_random_big(self):
        self.r.ipblock_create('12::/32', status='Container')
        self.r.ippool_create('pool')
        self.r.ippool_set_attrs('pool', {'allocation_strategy': 'random'})
        self.r.ippool_add_subnet('pool', '12::/64')
        assert self.r.ippool_get_ip('pool')['ip']

    def test_fill_pool(self):
        self.r.ipblock_create('12.0.0.0/8', status='Container')
        self.r.ippool_create('pool')
        self.r.ippool_add_subnet('pool', '12.0.0.0/31', dont_reserve_network_broadcast=True)
        assert self.r.ippool_get_ip('pool')['ip'] == '12.0.0.0'
        assert self.r.ippool_get_ip('pool')['ip'] == '12.0.0.1'
        assert self.r.ippool_get_ip('pool') is None

    def test_priorities(self):
        self.r.ipblock_create('192.0.0.0/8', status='Container')
        self.r.ippool_create('testpool')
        self.r.ippool_add_subnet('testpool', '192.168.0.0/24')
        self.r.ippool_add_subnet('testpool', '192.168.1.0/24')
        assert self.r.ippool_get_ip('testpool')['ip'] == '192.168.0.1'
        self.r.subnet_set_priority('192.168.1.0/24', 1)
        assert self.r.ippool_get_ip('testpool')['ip'] == '192.168.1.1'

    @pytest.mark.xfail(raises=IntegrityError, reason='https://github.com/ionos-core/dim/issues/205')
    def test_delegation_not_subnet(self):
        self.r.ipblock_create('10::/32', status='Container')
        self.r.ippool_create('pool')
        self.r.ippool_add_subnet('pool', '10::/64', dont_reserve_network_broadcast=True)
        assert not self.r.ippool_get_delegation('pool', 64)
        assert ips(self.r.ippool_get_delegation('pool', 64, maxsplit=1)) == \
            set(['10::/65', '10::8000:0:0:0/65'])

    def test_pool_noversion(self):
        self.r.ippool_create('pool')
        assert self.r.ippool_get_ip('pool') is None
        assert self.r.ippool_get_delegation('pool', 30) == []
