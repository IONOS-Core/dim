"""Ancestor lookup at prefix 0.

A /0 has no block above it, so the ancestor query has to produce an empty
result. It used to build its WHERE clause by joining one term per prefix
length above the block -- an empty string at prefix 0 -- and wrapped that in
parentheses, which MariaDB rejected with a 1064 syntax error. Callers were
then given a `prefix != 0` guard each, which left the broken query in place
for anyone who added a caller later.

These tests go at the query directly rather than through the API, so they fail
if the guards come back instead of the cause being fixed.
"""
from dim.ipaddr import IP
from dim.models import Ipblock, Layer3Domain
from tests.util import RPCTest


class AncestorsPrefixZeroTest(RPCTest):
    def setUp(self):
        RPCTest.setUp(self)
        self.layer3domain = Layer3Domain.query.filter_by(name='default').one()

    def ancestors(self, cidr, include_self=False):
        return Ipblock._ancestors_noparent(IP(cidr), self.layer3domain,
                                           include_self=include_self)

    def test_v4_default_route_has_no_ancestors(self):
        assert self.ancestors('0.0.0.0/0') == []

    def test_v6_default_route_has_no_ancestors(self):
        assert self.ancestors('::/0') == []

    def test_default_route_finds_itself_with_include_self(self):
        self.r.ipblock_create('0.0.0.0/0', status='Container', layer3domain='default')
        found = self.ancestors('0.0.0.0/0', include_self=True)
        assert [str(b.ip) for b in found] == ['0.0.0.0/0']

    def test_default_route_is_an_ancestor_of_a_block_below_it(self):
        self.r.ipblock_create('0.0.0.0/0', status='Container', layer3domain='default')
        self.r.ipblock_create('10.0.0.0/8', status='Container', layer3domain='default')
        assert [str(b.ip) for b in self.ancestors('10.0.0.0/8')] == ['0.0.0.0/0']

    def test_default_route_becomes_the_parent_of_blocks_below_it(self):
        '''_tree_update() has to see the /0 as a possible parent.

        This is what the guard in _tree_update() used to skip: it returned
        parent = None for the /0 itself, which is correct, but only because the
        query underneath would have crashed.
        '''
        self.r.ipblock_create('0.0.0.0/0', status='Container', layer3domain='default')
        self.r.ipblock_create('10.0.0.0/8', status='Container', layer3domain='default')
        root = Ipblock.query_ip(IP('0.0.0.0/0'), self.layer3domain).one()
        child = Ipblock.query_ip(IP('10.0.0.0/8'), self.layer3domain).one()
        assert root.parent is None
        assert child.parent is not None and child.parent.id == root.id

    def test_v4_and_v6_default_routes_do_not_see_each_other(self):
        '''Both carry address 0 and prefix 0; only the version separates them.'''
        self.r.ipblock_create('0.0.0.0/0', status='Container', layer3domain='default')
        assert self.ancestors('::/0', include_self=True) == []

    def test_container_layer3domain_is_guessed_for_a_default_route(self):
        '''ipblock_create() passes its guess_function unconditionally now.

        With more than one layer3domain and none given, the guess finds no
        parent for a /0 and the call has to fail with the regular DIM error --
        not with a SQL syntax error, and not by skipping the guess entirely.
        '''
        self.r.layer3domain_create('other', 'vrf', rd='8560:2')
        try:
            self.r.ipblock_create('0.0.0.0/0', status='Container')
        except Exception as e:
            assert 'A layer3domain is needed' in str(e), str(e)
        else:
            raise AssertionError('expected the call to require a layer3domain')
