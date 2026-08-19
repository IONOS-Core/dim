import dim.rpc as rpc
from .dns_test import rrs
from dim import db
from dim.errors import PermissionDeniedError, InvalidGroupError, DimError
from dim.models import User
from tests.util import DatabaseTest, raises


class RightsTest(DatabaseTest):
    def setUp(self):
        DatabaseTest.setUp(self)
        db.session.add_all([User('net'),
                            User('user')])
        db.session.commit()
        self.admin = rpc.TRPC('admin')
        self.net = rpc.TRPC('net')
        self.user = rpc.TRPC('user')

        self.admin.group_create('networkgroup')
        self.admin.group_grant_access('networkgroup', 'network_admin')
        self.admin.group_add_user('networkgroup', 'net')

    def test_list_users(self):
        assert self.user.user_list(include_groups=False) == [
            {'name': 'admin'},
            {'name': 'net'},
            {'name': 'user'},
            ]
        assert self.user.user_list(include_groups=True) == [
            {'name': 'admin', 'groups': ['all_users']},
            {'name': 'net', 'groups': ['all_users', 'networkgroup']},
            {'name': 'user', 'groups': ['all_users']},
            ]

    def test_create_add(self):
        with raises(PermissionDeniedError):
            self.user.group_create('usergroup')
        self.net.group_create('usergroup')
        with raises(PermissionDeniedError):
            self.user.group_add_user('usergroup', 'user')
        self.net.group_add_user('usergroup', 'user')
        with raises(PermissionDeniedError):
            self.user.group_delete('usergroup')

    def test_delete_group(self):
        assert set(self.user.group_list()) == set(['networkgroup', 'all_users'])
        assert set(self.user.user_get_groups('net')) == set(['networkgroup', 'all_users'])
        assert self.user.group_get_users('networkgroup') == ['net']
        self.admin.group_delete('networkgroup')
        assert self.user.group_list() == ['all_users']
        assert self.user.user_get_groups('net') == ['all_users']
        with raises(InvalidGroupError):
            assert self.user.group_get_users('networkgroup')
        with raises(InvalidGroupError):
            self.admin.group_delete('networkgroup')

    def test_delete_group2(self):
        self.net.group_create('usergroup')
        self.net.group_add_user('usergroup', 'user')
        assert set(self.user.user_get_groups('user')) == set(['usergroup', 'all_users'])
        self.net.group_delete('usergroup')
        assert self.user.user_get_groups('user') == ['all_users']
        self.net.group_create('usergroup')
        assert self.user.user_get_groups('user') == ['all_users']
        self.net.group_add_user('usergroup', 'user')
        assert set(self.user.user_get_groups('user')) == set(['usergroup', 'all_users'])

    def test_remove_user(self):
        self.admin.group_remove_user('networkgroup', 'net')
        assert self.user.group_get_users('networkgroup') == []
        assert self.user.user_get_groups('net') == ['all_users']
        self.admin.group_remove_user('networkgroup', 'net')

    def test_grant(self):
        assert self.user.group_get_access('networkgroup') == [['network_admin', 'all']]
        self.net.group_create('usergroup')
        assert self.user.group_get_access('usergroup') == []

        self.net.ippool_create('pool')
        self.net.group_add_user('usergroup', 'user')
        self.net.group_grant_access('usergroup', 'allocate', 'pool')
        self.net.group_grant_access('usergroup', 'allocate', 'pool')
        assert self.user.group_get_access('usergroup') == [['allocate', 'pool']]

        with raises(PermissionDeniedError):
            self.net.group_grant_access('usergroup', 'network_admin')
        with raises(PermissionDeniedError):
            self.net.group_add_user('networkgroup', 'user')

        self.net.group_revoke_access('usergroup', 'allocate', 'pool')
        assert self.user.group_get_access('usergroup') == []
        self.net.group_revoke_access('usergroup', 'allocate', 'pool')

        # 1. Test for the attr revoke bugfix (with dot)
        self.net.group_grant_access('usergroup', 'attr', ['audit.', 'pool'])
        assert self.user.ippool_get_access('pool') == [{'action': 'attr.audit.', 'group': 'usergroup', 'object': 'pool'}]
        self.net.group_revoke_access('usergroup', 'attr', ['audit.', 'pool'])
        assert self.user.ippool_get_access('pool') == []

        # 2. Test for robustness (symmetry with direct 'attr.' prefix)
        self.net.group_grant_access('usergroup', 'attr', ['attr.audit.', 'pool'])
        assert self.user.ippool_get_access('pool') == [{'action': 'attr.audit.', 'group': 'usergroup', 'object': 'pool'}]
        self.net.group_revoke_access('usergroup', 'attr', ['attr.audit.', 'pool'])
        assert self.user.ippool_get_access('pool') == []

        # 3. Test for attr revoke (without dot)
        self.net.group_grant_access('usergroup', 'attr', ['audit', 'pool'])
        assert self.user.ippool_get_access('pool') == [{'action': 'attr.audit', 'group': 'usergroup', 'object': 'pool'}]
        self.net.group_revoke_access('usergroup', 'attr', ['audit', 'pool'])
        assert self.user.ippool_get_access('pool') == []

        # 4. Independence test (scenario from ticket 2)
        # Grant both rights (with and without dot)
        self.net.group_grant_access('usergroup', 'attr', ['audit', 'pool'])
        self.net.group_grant_access('usergroup', 'attr', ['audit.', 'pool'])
        rights = self.user.ippool_get_access('pool')
        assert {'action': 'attr.audit', 'group': 'usergroup', 'object': 'pool'} in rights
        assert {'action': 'attr.audit.', 'group': 'usergroup', 'object': 'pool'} in rights
        
        # Delete ONLY the right without dot
        self.net.group_revoke_access('usergroup', 'attr', ['audit', 'pool'])
        # The right with dot MUST still be there!
        assert self.user.ippool_get_access('pool') == [{'action': 'attr.audit.', 'group': 'usergroup', 'object': 'pool'}]
        
        # Delete also the right with dot
        self.net.group_revoke_access('usergroup', 'attr', ['audit.', 'pool'])
        assert self.user.ippool_get_access('pool') == []


    def test_group_rename(self):
        self.net.group_create('usergroup1')
        self.net.group_rename('usergroup1', 'usergroup')
        assert 'usergroup' in set(self.user.group_list())
        assert 'usergroup1' not in set(self.user.group_list())
        with raises(PermissionDeniedError):
            self.net.group_rename('networkgroup', 'test')
        with raises(PermissionDeniedError):
            self.user.group_rename('networkgroup', 'test')

    def test_allocate(self):
        with raises(PermissionDeniedError):
            self.user.ippool_create('pool')
        self.net.ippool_create('pool')
        self.net.ipblock_create('12.0.0.0/8', status='Container')
        self.net.ippool_add_subnet('pool', '12.0.0.0/24')
        self.net.ippool_get_ip('pool')
        self.user.ippool_list(pool='pool')

        self.net.group_create('usergroup')
        self.net.group_add_user('usergroup', 'user')
        self.net.group_grant_access('usergroup', 'allocate', 'pool')
        self.user.ippool_get_ip('pool')
        self.user.ipblock_set_attrs('12.0.0.0', {'key': 'value'})
        assert self.user.ipblock_get_attrs('12.0.0.0')['key'] == 'value'
        self.user.ipblock_delete_attrs('12.0.0.0', ['key'])
        self.user.ip_free('12.0.0.1')
        delegation = self.user.ippool_get_delegation('pool', 27)
        self.user.ipblock_remove(delegation[0]['ip'])

        self.net.group_revoke_access('usergroup', 'allocate', 'pool')
        with raises(PermissionDeniedError):
            self.user.ippool_get_ip('pool')
        self.net.group_grant_access('usergroup', 'allocate', 'pool')
        self.net.group_remove_user('usergroup', 'user')
        with raises(PermissionDeniedError):
            self.user.ippool_get_ip('pool')

    def test_delete_pool(self):
        self.net.group_create('usergroup')
        self.net.ippool_create('pool')
        self.net.group_grant_access('usergroup', 'allocate', 'pool')
        self.net.ippool_delete('pool')

        self.net.ippool_create('pool2')
        self.net.group_grant_access('usergroup', 'allocate', 'pool2')

    def test_delete_subnet2(self):
        self.net.group_create('usergroup')
        self.net.ippool_create('pool')
        self.net.ipblock_create('12.0.0.0/8', status='Container')
        self.net.ippool_add_subnet('pool', '12.0.0.0/24')
        self.net.group_add_user('usergroup', 'user')
        self.net.group_grant_access('usergroup', 'allocate', 'pool')
        self.admin.zone_delete('0.0.12.in-addr.arpa')
        with raises(PermissionDeniedError):
            self.user.ipblock_remove('12.0.0.0/24', force=True, recursive=True)

    def test_rename_pool(self):
        self.net.group_create('usergroup')
        self.net.ippool_create('pool')
        self.net.group_grant_access('usergroup', 'allocate', 'pool')
        self.net.ippool_rename('pool', 'newname')
        assert self.net.group_get_access('usergroup') == [['allocate', 'newname']]

    def test_list_pools_writable(self):
        self.net.group_create('usergroup')
        self.net.group_add_user('usergroup', 'user')
        self.net.group_create('usergroup2')
        self.net.group_add_user('usergroup2', 'user')
        self.net.ippool_create('pool1')
        self.net.ippool_create('pool2')
        self.net.ippool_create('pool3')
        self.net.group_grant_access('usergroup2', 'allocate', 'pool1')
        self.net.group_grant_access('usergroup', 'allocate', 'pool1')
        self.net.group_grant_access('usergroup', 'allocate', 'pool2')
        assert set(p['name'] for p in self.admin.ippool_list(can_allocate=True)) == set(['pool1', 'pool2', 'pool3'])
        assert set(p['name'] for p in self.net.ippool_list(can_allocate=True)) == set(['pool1', 'pool2', 'pool3'])
        assert set(p['name'] for p in self.user.ippool_list(can_allocate=True)) == set(['pool1', 'pool2'])
        assert set(p['name'] for p in self.user.ippool_list()) == set(['pool1', 'pool2', 'pool3'])

    def test_add_twice(self):
        self.net.group_create('testgroup')
        self.net.group_add_user('testgroup', 'user')
        self.net.group_add_user('testgroup', 'user')


class ErrorChecker(object):
    '''
    Proxy for a TRPC object.

    If *must_fail* the proxy will verify the call raises and retry it as
    'admin'. This allows future calls to depend on the side effects of
    the previous calls.
    '''
    def __init__(self, user, must_fail):
        self.user = user
        self.must_fail = must_fail

    def __getattr__(self, attr):
        def wrapper(*args, **kwargs):
            if self.must_fail:
                with raises(DimError):
                    getattr(rpc.TRPC(self.user), attr)(*args, **kwargs)
                ret = getattr(rpc.TRPC('admin'), attr)(*args, **kwargs)
            else:
                ret = getattr(rpc.TRPC(self.user), attr)(*args, **kwargs)
            return ret
        return wrapper


class RightsMatrixTest(DatabaseTest):
    def setUp(self):
        DatabaseTest.setUp(self)
        db.session.add_all([User('net'),
                            User('dns'),
                            User('user'),
                            User('granted'),
                            User('netdns')])
        db.session.commit()
        self.admin = rpc.TRPC('admin')

        self.admin.group_create('network_admin_group')
        self.admin.group_grant_access('network_admin_group', 'network_admin')
        self.admin.group_add_user('network_admin_group', 'net')

        self.admin.group_create('dns_admin_group')
        self.admin.group_grant_access('dns_admin_group', 'dns_admin')
        self.admin.group_add_user('dns_admin_group', 'dns')

        self.admin.group_add_user('network_admin_group', 'netdns')
        self.admin.group_add_user('dns_admin_group', 'netdns')

        self.admin.group_create('granted')
        self.admin.group_add_user('granted', 'granted')

    def user_proxies(self, allowed):
        for user in ('granted', 'user', 'dns', 'net'):
            yield ErrorChecker(user, user not in allowed)
        yield ErrorChecker('netdns', 'dns' not in allowed and 'net' not in allowed)

    def who(self, u):
        '''Helper for RightsMatrixTest returning self.admin instead of u if u is a network_admin.'''
        return u if u.user != 'net' else self.admin

    def grant_revoke(self, allowed, group, right, obj=None):
        for u in self.user_proxies(allowed):
            u.group_grant_access(group, right, obj)
            u.group_revoke_access(group, right, obj)

    def test_grant_revoke(self):
        self.admin.group_create('group')
        self.grant_revoke((), 'group', 'network_admin')
        self.grant_revoke((), 'group', 'dns_admin')

        self.admin.ippool_create('pool')
        self.grant_revoke(('net', ), 'group', 'allocate', 'pool')

        self.admin.zone_create('test.com')
        self.grant_revoke(('dns', ), 'group', 'create_rr', ['test.com', []])
        self.grant_revoke(('dns', ), 'group', 'delete_rr', ['test.com', []])

    def test_dual_admin(self):
        '''user with both dns_admin and network_admin can grant ip rights'''
        self.admin.ippool_create('pool')
        self.admin.group_create('group')
        netdns = rpc.TRPC('netdns')
        netdns.group_grant_access('group', 'allocate', 'pool')

    def test_create_modify_delete_group(self):
        '''create/modify/delete group'''
        for u in self.user_proxies(('dns', 'net')):
            u.group_create('testgroup')
            u.group_add_user('testgroup', 'user')
            u.group_remove_user('testgroup', 'user')
            u.group_rename('testgroup', 'testgroup1')
            u.group_delete('testgroup1')

    def test_create_delete_container(self):
        '''create/delete container'''
        for u in self.user_proxies(('net', )):
            u.ipblock_create('1.0.0.0/24')
            u.ipblock_remove('1.0.0.0/24')

    def test_modify_container_attributes(self):
        '''modify container attributes'''
        self.admin.ipblock_create('1.0.0.0/24')
        for u in self.user_proxies(('dns', 'net')):
            u.ipblock_set_attrs('1.0.0.0/24', attributes={'a': 'b'})
            u.ipblock_delete_attrs('1.0.0.0/24', ['a'])

    def test_create_modify_delete_pool(self):
        '''create/modify/delete pool'''
        self.admin.ipblock_create('12.0.0.0/8', status='Container')
        for u in self.user_proxies(('net', )):
            u.ippool_create('pool')
            u.ippool_add_subnet('pool', '12.0.0.0/24')
            u.ippool_set_vlan('pool', 1)
            u.ippool_remove_vlan('pool')
            self.admin.zone_delete('0.0.12.in-addr.arpa')
            u.ipblock_remove('12.0.0.0/24', force=True, recursive=True)
            u.ippool_rename('pool', 'pool1')
            u.ippool_delete('pool1')

    def test_modify_pool_attributes(self):
        '''modify pool attributes'''
        self.admin.ippool_create('pool')
        for u in self.user_proxies(('dns', 'net')):
            u.ippool_set_attrs('pool', attributes={'a': 'b'})
            u.ippool_delete_attrs('pool', ['a'])

    def test_modify_rr(self):
        '''set ttl and comment'''
        self.admin.zone_create('test.com')
        rr = dict(name='test.com.', type='TXT', strings=['a'])
        self.admin.rr_create(**rr)
        self.admin.group_grant_access('granted', 'delete_rr', ['test.com', []])
        for u in self.user_proxies(('dns', 'granted', 'net')):
            u.rr_set_attrs(ttl=10, **rr)
            u.rr_set_comment(comment='a', **rr)
            u.rr_set_ttl(ttl=11, **rr)

    def test_create_delete_forward_zones(self):
        '''create/delete fwd-zones'''
        self.admin.group_grant_access('granted', 'zone_create')
        for u in self.user_proxies(('dns', 'granted', 'net')):
            self.who(u).zone_create('test.com')
            self.who(u).rr_create(name='a.test.com.', type='NS', nsdname='x.test.com.')
            u.rr_delete(name='a.test.com.', type='NS', nsdname='x.test.com.')
            self.who(u).zone_delete('test.com')

    def test_create_reverse_zones(self):
        '''create rev-zones'''
        for u in self.user_proxies(('dns', 'net')):
            u.zone_create('1.1.1.in-addr.arpa')
            self.admin.zone_delete('1.1.1.in-addr.arpa')

    def test_delete_reverse_zones(self):
        '''delete rev-zones'''
        for u in self.user_proxies(('dns', 'net')):
            self.admin.zone_create('1.1.1.in-addr.arpa')
            u.zone_delete('1.1.1.in-addr.arpa')

    def test_create_modify_delete_zone_profiles(self):
        '''create/modify/delete zone-profiles'''
        for u in self.user_proxies(('dns', 'net')):
            self.who(u).zone_create('profile', profile=True)
            self.who(u).rr_create(name='a.profile.', type='NS', nsdname='a.test.com.')
            u.rr_delete(name='a.profile.', type='NS', nsdname='a.test.com.')
            self.who(u).zone_delete('profile', profile=True)

    def test_create_delete_zone_groups(self):
        '''create/delete zone-groups'''
        self.admin.zone_create('test.com')
        for u in self.user_proxies(('dns', )):
            u.zone_group_create('zg')
            u.zone_group_rename('zg', 'zg1')
            u.zone_group_delete('zg1')

    def test_create_modify_delete_output(self):
        '''create/modify/delete output'''
        self.admin.zone_group_create('zg')
        for u in self.user_proxies(('dns', )):
            u.output_create('out', plugin='bind')
            u.output_set_comment('out', 'test')
            u.output_add_group('out', 'zg')
            u.output_remove_group('out', 'zg')
            u.output_get_attrs('out')
            u.output_delete('out')

    def test_create_rr_everywhere(self):
        '''create/delete rr in every zone (fwd and rev)'''
        self.admin.ipblock_create('1.0.0.0/8', status='Container')
        self.admin.zone_create('test.com')
        self.admin.ippool_create('pool')
        self.admin.ippool_add_subnet('pool', '1.0.0.0/24')
        rpc.TRPC('dns').rr_create(name='a.test.com.', type='A', ip='1.0.0.1')
        for u in self.user_proxies(('dns', 'net')):
            self.who(u).rr_create(name='a.test.com.', type='A', ip='1.0.0.1')
            u.rr_delete(name='a.test.com.', type='A', ip='1.0.0.1', free_ips=True)
            self.who(u).rr_create(name='b.test.com.', type='NS', nsdname='c.test.com.')
            u.rr_delete(name='b.test.com.', type='NS', nsdname='c.test.com.')

    def test_create_rr_ptr(self):
        '''create/delete PTR rr in every rev-zone'''
        self.admin.zone_create('test.com')
        self.admin.ipblock_create('1.0.0.0/8', status='Container')
        self.admin.ippool_create('pool')
        self.admin.ippool_add_subnet('pool', '1.0.0.0/24')
        self.admin.group_create('group')
        self.admin.group_grant_access('group', 'create_rr', ['test.com', []])
        self.admin.group_grant_access('group', 'delete_rr', ['test.com', []])
        self.admin.group_grant_access('group', 'allocate', 'pool')
        self.admin.group_add_user('group', 'user')
        self.admin.group_add_user('group', 'net')
        for u in self.user_proxies(('user', 'dns', 'net')):
            u.rr_create(ptrdname='a.test.com.', type='PTR', ip='1.0.0.1')
            u.rr_delete(ptrdname='a.test.com.', type='PTR', ip='1.0.0.1')

    def test_granted_allocate(self):
        self.admin.ippool_create('pool')
        self.admin.group_grant_access('granted', 'allocate', 'pool')
        self.admin.ipblock_create('12.0.0.0/8', status='Container')
        for u in self.user_proxies(('dns', 'net', 'granted')):
            self.admin.ippool_add_subnet('pool', '12.0.0.0/24')
            ip = u.ippool_get_ip('pool')['ip']
            u.ip_free(ip)
            delegation = u.ippool_get_delegation('pool', 30)[0]['ip']
            u.ipblock_remove(delegation)
            self.admin.ipblock_remove('12.0.0.0/24', force=True, recursive=True)

    def test_create_rr(self):
        self.admin.zone_create('test.com')
        self.admin.group_grant_access('granted', 'create_rr', ['test.com', []])
        for u in self.user_proxies(('dns', 'granted')):
            u.rr_create(name='test.com.', type='TXT', strings=['test'])
            self.admin.rr_delete(name='test.com.', type='TXT')

    def test_rr_reverse_zone(self):
        self.admin.zone_create('1.1.1.in-addr.arpa')
        self.admin.group_grant_access('granted', 'create_rr', ['1.1.1.in-addr.arpa', []])
        self.admin.group_grant_access('granted', 'delete_rr', ['1.1.1.in-addr.arpa', []])
        for u in self.user_proxies(('dns', 'granted', 'net')):
            self.who(u).rr_create(name='1.1.1.in-addr.arpa.', type='TXT', strings=['test'])
            u.rr_delete(name='1.1.1.in-addr.arpa.', type='TXT')

    def test_granted_create_rr_ptr(self):
        def test(users):
            for u in self.user_proxies(users):
                u.rr_create(ptrdname='a.test.com.', type='PTR', ip='1.0.0.1')
                self.admin.rr_delete(ptrdname='a.test.com.', type='PTR', ip='1.0.0.1')
                self.admin.ip_free('1.0.0.1')
        self.admin.ipblock_create('1.0.0.0/8', status='Container')
        self.admin.ippool_create('pool')
        self.admin.ippool_add_subnet('pool', '1.0.0.0/24')
        self.admin.group_grant_access('granted', 'allocate', 'pool')
        test(('granted', 'net', 'dns'))
        self.admin.zone_create('test.com')
        test(('dns', ))
        self.admin.group_grant_access('granted', 'create_rr', ['test.com', []])
        test(('dns', 'granted'))

    def test_create_rr_overwrite(self):
        def test_overwrite_a(users):
            for u in self.user_proxies(users):
                self.admin.rr_create(name='a.test.com.', type='A', ip='1.0.0.1')
                u.rr_create(name='a.test.com.', type='A', ip='1.0.0.2', overwrite_ptr=True, overwrite_a=True)
                self.admin.rr_delete(name='a.test.com.', type='A', ip='1.0.0.2')
        self.admin.zone_create('test.com')
        self.admin.ipblock_create('1.0.0.0/8', status='Container')
        self.admin.ippool_create('pool')
        self.admin.ippool_add_subnet('pool', '1.0.0.0/24')
        self.admin.group_grant_access('granted', 'allocate', 'pool')
        self.admin.group_grant_access('granted', 'create_rr', ['test.com', []])
        for u in self.user_proxies(('granted', 'dns')):
            u.rr_create(name='a.test.com.', type='A', ip='1.0.0.1')
            u.rr_create(name='a.test.com.', type='A', ip='1.0.0.2', overwrite_ptr=True)
            self.admin.rr_delete(name='a.test.com.', type='A', ip='1.0.0.2')
        test_overwrite_a(('dns', ))
        self.admin.group_grant_access('granted', 'delete_rr', ['test.com', []])
        test_overwrite_a(('dns', 'granted'))

    def test_delete_rr(self):
        self.admin.zone_create('test.com')
        self.admin.group_grant_access('granted', 'delete_rr', ['test.com', []])
        for u in self.user_proxies(('dns', 'granted', 'net')):
            self.admin.rr_create(name='test.com.', type='TXT', strings=['test'])
            u.rr_delete(name='test.com.', type='TXT')

    def test_delete_rr_ptr(self):
        def test_delete_ptr(users):
            for u in self.user_proxies(users):
                self.admin.rr_create(ptrdname='a.test.com.', type='PTR', ip='1.0.0.1')
                u.rr_delete(ptrdname='a.test.com.', type='PTR', ip='1.0.0.1')
        self.admin.ipblock_create('1.0.0.0/8', status='Container')
        self.admin.ippool_create('pool')
        self.admin.ippool_add_subnet('pool', '1.0.0.0/24')
        test_delete_ptr(('user', 'dns', 'net', 'granted'))
        self.admin.zone_create('test.com')
        self.admin.group_grant_access('granted', 'delete_rr', ['test.com', []])
        test_delete_ptr(('dns', 'granted', 'net'))

    def test_zone_list(self):
        self.admin.zone_create('oneview.com')
        self.admin.zone_create('delete.com')
        self.admin.zone_create('twoviews.com')
        self.admin.zone_create_view('twoviews.com', 'second')
        self.admin.group_grant_access('granted', 'create_rr', ['oneview.com', []])
        self.admin.group_grant_access('granted', 'delete_rr', ['delete.com', []])
        self.admin.group_grant_access('granted', 'delete_rr', ['twoviews.com', ['second']])
        self.admin.group_grant_access('granted', 'create_rr', ['twoviews.com', ['default']])
        a = ErrorChecker('admin', False)
        u = ErrorChecker('user', False)
        g = ErrorChecker('granted', False)
        assert g.zone_count(can_create_rr=True) == 2
        assert g.zone_count(can_delete_rr=True) == 2
        assert g.zone_count(can_delete_rr=True, can_create_rr=True) == 3
        assert u.zone_count(can_delete_rr=True, can_create_rr=True) == 0

        def names(zones):
            return [z['name'] for z in zones]
        assert set(names(u.zone_list(can_create_rr=True, can_delete_rr=True))) == set()
        assert set(names(g.zone_list(can_create_rr=True))) == set(['oneview.com', 'twoviews.com'])
        assert set(names(g.zone_list(can_delete_rr=True))) == set(['delete.com', 'twoviews.com'])
        assert rrs(u.zone_list(fields=True),
                   fields=('name', 'can_create_rr', 'can_delete_rr')) == rrs(
            [('oneview.com', False, False),
             ('twoviews.com', False, False),
             ('delete.com', False, False)
             ])
        assert rrs(g.zone_list(fields=True),
                   fields=('name', 'can_create_rr', 'can_delete_rr')) == rrs(
            [('oneview.com', True, False),
             ('twoviews.com', True, True),
             ('delete.com', False, True)
             ])
        assert rrs(a.zone_list(fields=True),
                   fields=('name', 'can_create_rr', 'can_delete_rr')) == rrs(
            [('oneview.com', True, True),
             ('twoviews.com', True, True),
             ('delete.com', True, True)
             ])

    def _zone_view_rights_setup(self):
        self.admin.zone_create('test.com')
        self.admin.zone_create_view('test.com', 'create')
        self.admin.zone_create_view('test.com', 'delete')
        self.admin.zone_create_view('test.com', 'createdelete')
        self.admin.group_grant_access('granted', 'create_rr', ['test.com', ['create', 'createdelete']])
        self.admin.group_grant_access('granted', 'delete_rr', ['test.com', ['delete', 'createdelete']])
        self.u = ErrorChecker('user', False)
        self.a = ErrorChecker('admin', False)
        self.g = ErrorChecker('granted', False)

    def test_zone_list_views(self):
        self._zone_view_rights_setup()

        def names(zones):
            return [z['name'] for z in zones]
        assert set(names(self.g.zone_list_views('test.com'))) == set(['default', 'create', 'delete', 'createdelete'])
        assert set(names(self.g.zone_list_views('test.com', can_create_rr=True))) == set(['create', 'createdelete'])
        assert set(names(self.g.zone_list_views('test.com', can_delete_rr=True))) == set(['delete', 'createdelete'])
        assert set(names(self.g.zone_list_views('test.com', can_create_rr=True, can_delete_rr=True))) \
            == set(['create', 'delete', 'createdelete'])
        assert rrs(self.g.zone_list_views('test.com', fields=True),
                   fields=('name', 'can_create_rr', 'can_delete_rr')) == rrs(
            [('default', False, False),
             ('create', True, False),
             ('delete', False, True),
             ('createdelete', True, True)
             ])
        assert rrs(self.a.zone_list_views('test.com', fields=True),
                   fields=('name', 'can_create_rr', 'can_delete_rr')) == rrs(
            [('default', True, True),
             ('create', True, True),
             ('delete', True, True),
             ('createdelete', True, True)
             ])
        assert rrs(self.u.zone_list_views('test.com', fields=True),
                   fields=('name', 'can_create_rr', 'can_delete_rr')) == rrs(
            [('default', False, False),
             ('create', False, False),
             ('delete', False, False),
             ('createdelete', False, False)
             ])

    def test_rr_list(self):
        self._zone_view_rights_setup()
        views = {'default': [False, False],
                 'create': [True, False],
                 'delete': [False, True],
                 'createdelete': [True, True]}
        self.admin.rr_create(name='a.test.com.', type='txt', strings='', views=list(views.keys()))
        for rr in self.g.rr_list(type='txt', fields=True):
            assert rr['can_create_rr'] == views[rr['view']][0]
            assert rr['can_delete_rr'] == views[rr['view']][1]

    def test_ippool_list(self):
        self.admin.ippool_create('p')
        self.admin.ippool_create('p2')
        self.admin.group_grant_access('granted', 'allocate', 'p')

        def pools(p, p2):
            return [{'name': 'p', 'can_allocate': p, 'vlan': None, 'layer3domain': 'default'},
                    {'name': 'p2', 'can_allocate': p2, 'vlan': None, 'layer3domain': 'default'}]
        assert ErrorChecker('granted', False).ippool_list(fields=True, include_subnets=False) == pools(True, False)
        assert ErrorChecker('user', False).ippool_list(fields=True, include_subnets=False) == pools(False, False)
        assert self.admin.ippool_list(fields=True, include_subnets=False) == pools(True, True)

    def test_rr_list_ptr_rights(self):
        self.admin.ipblock_create('1.0.0.0/8', status='Container')
        self.admin.ippool_create('p')
        self.admin.ippool_add_subnet('p', '1.1.1.0/24')
        self.admin.rr_create(ip='1.1.1.1', type='PTR', ptrdname='a.de.')
        self.admin.rr_create(name='gigi.1.1.1.in-addr.arpa.', type='TXT', strings=['s'])
        self.user = rpc.TRPC('user')
        rr = self.user.rr_list(zone='1.1.1.in-addr.arpa', type='PTR', fields=True)[0]
        assert rr['can_create_rr']
        assert rr['can_delete_rr']
        rr = self.user.rr_list(zone='1.1.1.in-addr.arpa', type='TXT', fields=True)[0]
        assert not rr['can_create_rr']
        assert not rr['can_delete_rr']

    def test_create_a_rr_unmanaged_ip(self):
        # ND-58
        self.admin.zone_create('test.com')
        self.admin.group_grant_access('granted', 'create_rr', ['test.com', []])
        for u in self.user_proxies(('dns', 'granted')):
            u.rr_create(name='test.com.', type='A', ip='1.2.3.4')
            self.admin.rr_delete(name='test.com.', type='A', ip='1.2.3.4')

    def test_zombie_rights(self):
        # ND-73
        self.admin.ippool_create('p')
        self.admin.zone_create('test.com')
        self.admin.group_grant_access('granted', 'create_rr', ['test.com', []])
        self.admin.group_grant_access('granted', 'delete_rr', ['test.com', []])
        self.admin.zone_delete('test.com')
        for u in self.user_proxies(('net', )):
            u.group_grant_access('granted', 'allocate', 'p')
        self.admin.ippool_delete('p')

    def test_proxied_user(self):
        self.net = rpc.TRPC('net', 'smth')
        with raises(PermissionDeniedError):
            self.net.zone_create('a.de')

    def test_manage_zone(self):
        self.admin.zone_create('a.de')
        self.admin.zone_group_create('zg')
        self.admin.registrar_account_create('ra', 'autodns3', 'url', 'u', 'p', 's')
        self.admin.group_grant_access('granted', 'zone_admin', 'a.de')
        for u in self.user_proxies(('dns', 'granted')):
            u.zone_set_attrs('a.de', {'a': 'b'})
            u.zone_delete_attrs('a.de', ['a'])
            u.zone_create_view('a.de', 'a')
            u.zone_rename_view('a.de', 'a', 'b')
            u.zone_delete_view('a.de', 'b')
            u.zone_set_owner('a.de', 'granted')
            u.zone_set_soa_attrs('a.de', {'ttl': 60})
            u.zone_dnssec_enable('a.de')
            u.zone_create_key('a.de', 'ksk')
            u.zone_dnssec_disable('a.de')
            u.zone_group_add_zone('zg', 'a.de')
            u.zone_group_remove_zone('zg', 'a.de')
            u.registrar_account_add_zone('ra', 'a.de')
            u.registrar_account_delete_zone('ra', 'a.de')

    def test_delete_rr_by_id(self):
        self.admin.zone_create('a.de')
        self.admin.rr_create(name='a.de.', type='NS', nsdname='b.de.')
        self.admin.zone_create('b.de')
        self.admin.group_grant_access('granted', 'delete_rr', ['b.de', []])
        # rr_delete by id with references
        for u in self.user_proxies(('dns', 'net')):
            self.admin.rr_create(name='b.de.', type='A', ip='1.2.3.4')
            rr_id = self.admin.rr_get_references(name='b.de.', type='A', ip='1.2.3.4')['root']
            u.rr_delete(ids=[rr_id], references='delete')
        # rr_delete by id without references
        for u in self.user_proxies(('dns', 'net', 'granted')):
            self.admin.rr_create(name='b.de.', type='A', ip='1.2.3.4')
            rr_id = self.admin.rr_get_references(name='b.de.', type='A', ip='1.2.3.4')['root']
            u.rr_delete(ids=[rr_id], references='ignore')
        # rr_delete by id with no pool rights
        for u in self.user_proxies(('dns', 'net', 'granted')):
            self.admin.rr_create(name='b.de.', type='A', ip='1.2.3.4')
            rr_id = self.admin.rr_get_references(name='b.de.', type='A', ip='1.2.3.4')['root']
            u.rr_delete(ids=[rr_id], references='ignore', free_ips=True)
