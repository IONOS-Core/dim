

import datetime
import collections
import logging
import os
import sys
import re

import flask.json as json
from flask import current_app as app, g
from sqlalchemy import between, and_, or_, not_, select, String, desc
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import aliased, joinedload, contains_eager
from sqlalchemy.orm.session import make_transient
from sqlalchemy.sql.expression import literal, func, FunctionElement, distinct

import dim.dns
import dim.ldap_sync
from dim import db, __version__
from dim.allocator import allocate_delegation, allocate_ip
from dim.autodns3 import get_pretty_error_message
from dim.crypto import generate_RSASHA256_key_pair
from dim.dns import (subnet_reverse_zones, rr_delete, delete_with_references,
                     get_zone, get_view, delete_ipblock_rrs, get_rr_zone, create_subzone,
                     orphaned_references, delete_single_rr, free_ipblocks, ZONE_OBJ_NAME,
                     zone_group_add_zone, get_subzones, get_parent_zone,
                     create_ds_rr, delete_ds_rr, get_parent_zone_view)
from dim.errors import (DimError, InvalidPoolError, InvalidIPError,
                        InvalidVLANError, InvalidStatusError, InvalidGroupError, InvalidUserError,
                        AlreadyExistsError, NotInPoolError, NotInDelegationError,
                        InvalidParameterError, InvalidPriorityError, InvalidAccessRightError,
                        HasChildrenError, MultipleViewsError)
from dim.ipaddr import IP, valid_block
from dim.messages import Messages
from dim.models import (Pool, PoolAttr, FavoritePool, Ipblock, IpblockAttr, IpblockAttrName,
                        IpblockStatus, Vlan, Group, User, AccessRight, GroupRight, Zone, ZoneView,
                        FavoriteZoneView, RR, ZoneGroup, Output, OutputUpdate, GroupMembership,
                        ZoneKey, Department, RegistrarAccount, RegistrarAction,
                        Layer3Domain)
from dim.models.history import HistorySelect, record_history
from dim.rrtype import validate_mail, RRType
from dim.transaction import TransactionProxy
from dim.util import is_reverse_zone, make_fqdn

RROrder = collections.namedtuple('RROrder', ['fn', 'rr_order_by', 'soa_order_by'])


class trim_trailing(FunctionElement):
    type = String()
    name = 'trim_trailing'


@compiles(trim_trailing, 'mysql')
def mysql_trim_trailing(element, compiler, **kw):
    suffix, string = list(element.clauses)
    return "TRIM(TRAILING %s FROM %s)" % (compiler.process(suffix), compiler.process(string))


def readonly(f):
    f.readonly = True
    return f


def updating(f):
    f.readonly = False
    return f


def updating_delayed_lock(f):
    f.readonly = False
    f.delayed_lock = True
    return f


class RPC(object):
    def __init__(self, username=None, tool=None, ip=None):
        self.username = username
        # Tool that authenticated the user
        self.tool = tool
        # IP that sent the request
        self.ip = ip
        if self.ip is not None:
            try:
                self.ip = IP(ip)
            except:
                self.ip = None

    @property
    def user(self):
        if not hasattr(self, '_user'):
            self._user = get_user(self.username)
        return self._user

    @readonly
    def get_username(self):
        return self.user.username

    @readonly
    def protocol_version(self):
        return 17

    @readonly
    def server_info(self):
        d = {}
        prefix = 'SERVER_INFO_'
        for k, v in list(app.config.items()):
            if k.startswith(prefix):
                d[k[len(prefix):].lower()] = v
        u = os.uname()
        url = db.engine.url
        d.update(dict(version=__version__,
                      host=u[1],
                      os='%s %s %s' % (u[0], u[4], u[3]),
                      python=sys.version.split()[0],
                      db='%s://%s%s/%s' % (url.drivername, url.host, (':' + str(url.port) if url.port else ''), url.database)))
        return d

    @updating
    def group_create(self, group, department_number=None):
        self.user.can_create_groups()
        if department_number is not None:
            group = _check_department_number(department_number)
        if Group.query.filter_by(name=group).count():
            raise AlreadyExistsError("A group named '%s' already exists" % group)
        db.session.add(Group(name=group, department_number=department_number))

    @updating
    def group_set_department_number(self, group, department_number):
        self.user.can_create_groups()
        group = get_group(group)
        if group.department_number != department_number:
            if department_number is not None:
                _check_department_number(department_number)
            group.department_number = department_number
            record_history(group, action='department_number', department_number=department_number)

    @updating
    def group_delete(self, group):
        group = get_group(group)
        self.user.can_edit_group(group)
        db.session.delete(group)

    @updating
    def group_rename(self, group, new_name):
        group = get_group(group)
        self.user.can_edit_group(group)
        group.name = new_name
        group.update_modified()

    @readonly
    def group_get_attrs(self, group):
        group = get_group(group)
        attrs = dict(created=group.created,
                     created_by=group.created_by,
                     modified=group.modified,
                     modified_by=group.modified_by,
                     name=group.name)
        if group.department_number is not None:
            attrs.update(dict(department_number=group.department_number))
        return attrs

    @readonly
    def group_list(self):
        return [group.name for group in Group.query.order_by(Group.name)]

    @updating
    def group_add_user(self, group, user):
        group = get_group(group)
        self.user.can_edit_group(group)
        user = get_user(user)
        # Check if this user already gets zone_create from another user-group
        would_get_zone_create = GroupRight.query.filter_by(group=group).join(AccessRight).filter_by(
            access='zone_create').count() != 0
        if would_get_zone_create and user.has_any_access([('zone_create', None)]):
            raise InvalidParameterError('An user can be granted the zone_create right from a single user-group')
        group.users.add(user)

    @updating
    def group_remove_user(self, group, user):
        group = get_group(group)
        self.user.can_edit_group(group)
        user = get_user(user)
        if user in group.users:
            group.remove_user(user)

    @updating
    def group_grant_access(self, group, access, object=None):
        group = get_group(group)
        self.user.can_grant_access(group, access)
        _group_grant_access(group, access, object)

    @updating
    def group_revoke_access(self, group, access, object=None):
        group = get_group(group)
        self.user.can_grant_access(group, access)
        rights = get_rights(access, object)
        for (access, object) in rights:
            object_id, object_class = get_object_id_class(access, object)
            ar = AccessRight.query.filter_by(access=access,
                                             object_id=object_id,
                                             object_class=object_class).first()
            if ar and ar in group.rights:
                group.rights.remove(ar)

    @readonly
    def user_get_groups(self, user):
        user = get_user(user)
        return [group.name for group in user.groups]

    @readonly
    def user_list(self, include_groups=False):
        if include_groups:
            query = User.query.options(joinedload(*User.groups.attr))
        else:
            query = User.query
        query = query.order_by(User.username)
        if include_groups:
            return [dict(name=user.username, groups=[group.name for group in user.groups])
                    for user in query]
        else:
            return [dict(name=user.username) for user in query]

    @readonly
    def user_get_attrs(self, username):
        user = get_user(username)
        return {'username': user.username,
                'ldap_uid': user.ldap_uid,
                'ldap_cn': user.ldap_cn,
                'department_number': user.department_number}

    @readonly
    def user_get_rights(self, username):
        user = get_user(username)
        return {'username': user.username,
                'rights': user.get_rights()}

    @readonly
    def user_get_preferences(self):
        prefs = get_user(self.username).preferences
        return json.loads(prefs) if prefs is not None else {}

    @updating
    def user_set_preferences(self, preferences):
        user = get_user(self.username)
        user.preferences = json.dumps(preferences)

    @readonly
    def group_get_users(self, group, include_ldap=False):
        group = get_group(group)
        if not include_ldap:
            return [user.username for user in group.users]
        memberships = GroupMembership.query.filter_by(group=group).all()
        return [{'username': m.user.username,
                 'ldap_cn': m.user.ldap_cn,
                 'ldap_uid': m.user.ldap_uid,
                 'department_number': m.user.department_number} for m in memberships]

    @readonly
    def group_get_access(self, group):
        group = get_group(group)
        pools = db.session.query(Pool.name.distinct())\
            .join(AccessRight, and_(AccessRight.access == 'allocate',
                                    AccessRight.object_class == 'Ippool',
                                    AccessRight.object_id == Pool.id))\
            .join(GroupRight).filter(GroupRight.group == group)\
            .order_by(Pool.name)
        views = db.session.query(Zone, ZoneView.name, AccessRight.access)\
            .join(ZoneView)\
            .join(AccessRight, and_(or_(AccessRight.access == 'create_rr', AccessRight.access == 'delete_rr'),
                                    AccessRight.object_class == 'ZoneView',
                                    AccessRight.object_id == ZoneView.id))\
            .join(GroupRight).filter(GroupRight.group == group)\
            .order_by(AccessRight.access, Zone.name, ZoneView.name)
        zones = db.session.query(Zone.name)\
            .join(AccessRight, and_(AccessRight.access == 'zone_admin',
                                    AccessRight.object_class == 'Zone',
                                    AccessRight.object_id == Zone.id)) \
            .join(GroupRight).filter(GroupRight.group == group) \
            .order_by(Zone.name)
        ret = [['allocate', pool[0]] for pool in pools]
        ret += [['zone_admin', z[0]] for z in zones]
        ret += [[view[2], _zone_view_display_string(view[0], view[1])] for view in views]
        ret += [[ar.access, ar.object_class] for ar in group.rights if ar.object_class == 'all']
        return ret

    @readonly
    def department_list(self, unused=True):
        used = db.session.query(Group.department_number).filter(Group.department_number != None)  # noqa
        return [{'name': d.name, 'department_number': d.department_number}
                for d in Department.query.filter(Department.department_number.notin_(used)).all()]

    @readonly
    def department_number(self, name):
        dept = Department.query.filter(Department.name == name).first()
        if dept:
            return dept.department_number
        else:
            return None

    @updating
    def layer3domain_create(self, name, type, comment=None, **options):
        self.user.can_network_admin()
        if name == "all":
            raise InvalidParameterError("Name 'all' is reserved")
        if Layer3Domain.query.filter_by(name=name).count():
            raise AlreadyExistsError("A layer3domain named '%s' already exists" % name)
        if type in Layer3Domain.TYPES:
            for attr_name in Layer3Domain.TYPES[type]:
                if options.get(attr_name) is None:
                    raise InvalidParameterError('Type %s requires a %s' % (type, attr_name))
        elif type not in Layer3Domain.TYPES and len(options) > 0:
            raise InvalidParameterError('Type %s does not support attributes' % (type))

        layer3domain = Layer3Domain(name=name, type=type, comment=comment)
        if type == Layer3Domain.VRF:
            set_rd(layer3domain, options['rd'])
        db.session.add(layer3domain)

    @updating
    def layer3domain_set_comment(self, layer3domain, comment):
        self.user.can_network_admin()
        layer3domain = get_layer3domain(layer3domain)
        layer3domain.set_comment(comment)

    @updating
    def layer3domain_set_attrs(self, layer3domain, rd=None):
        self.user.can_network_admin()
        layer3domain = get_layer3domain(layer3domain)
        if layer3domain.type == Layer3Domain.VRF:
            set_rd(layer3domain, rd)

    @updating
    def layer3domain_set_type(self, layer3domain, type, rd=None):
        self.user.can_network_admin()
        layer3domain = get_layer3domain(layer3domain)
        layer3domain.type = type
        if layer3domain.type == Layer3Domain.VRF:
            set_rd(layer3domain, rd)

    @readonly
    def layer3domain_get_attrs(self, layer3domain):
        layer3domain = get_layer3domain(layer3domain)
        result = dict(created=layer3domain.created,
                      created_by=layer3domain.created_by,
                      modified=layer3domain.modified,
                      modified_by=layer3domain.modified_by)
        if layer3domain.type == Layer3Domain.VRF:
            result['rd'] = layer3domain.display_rd
        if layer3domain.comment is not None:
            result['comment'] = layer3domain.comment

        return result

    @readonly
    def layer3domain_list(self):
        layer3domains = []
        for layer3domain in Layer3Domain.query.all():
            l3d = dict(name=layer3domain.name, type=layer3domain.type, comment=layer3domain.comment)
            if layer3domain.type == Layer3Domain.VRF:
                l3d['properties'] = dict(rd=layer3domain.display_rd)
            layer3domains.append(l3d)
        return layer3domains

    @updating
    def layer3domain_delete(self, name):
        self.user.can_network_admin()
        layer3domain = get_layer3domain(name)
        if Pool.query.filter_by(layer3domain=layer3domain).count() > 0:
            raise DimError('layer3domain %s still contains pools' % layer3domain.name)
        if Ipblock.query.filter_by(layer3domain=layer3domain).count() > 0:
            raise DimError('layer3domain %s still contains objects' % layer3domain.name)
        db.session.delete(layer3domain)

    @updating
    def layer3domain_rename(self, old_name, new_name):
        self.user.can_network_admin()
        layer3domain = get_layer3domain(old_name)
        layer3domain.name = new_name
        views = ZoneView.query.filter_by(name=old_name).join(Zone).filter(Zone.profile == False,
                                                                          or_(Zone.name.endswith('.in-addr.arpa'),
                                                                              Zone.name.endswith('.ip6.arpa'))).all()
        for view in views:
            view.name = new_name

    @readonly
    def ippool_get_access(self, pool):
        rights = db.session.query(AccessRight.access, Group.name)\
            .join(Pool, AccessRight.object_id == Pool.id)\
            .join(GroupRight).join(Group)\
            .filter(Pool.name == pool)\
            .filter(AccessRight.object_class == 'Ippool')\
            .order_by(Group.name)
        return [{'action': r[0],
                 'group': r[1],
                 'object': pool}
                for r in rights]

    @readonly
    def zone_get_access(self, zone, view=None):
        query = db.session.query(AccessRight.access, Zone, ZoneView.name, Group.name)\
            .join(ZoneView, AccessRight.object_id == ZoneView.id)\
            .join(Zone)\
            .join(GroupRight).join(Group)\
            .filter(Zone.name == zone)\
            .filter(AccessRight.object_class == 'ZoneView')\
            .order_by(Zone.name, ZoneView.name, Group.name, AccessRight.access)
        if view is not None:
            query = query.filter(ZoneView.name == view)
        return [{'action': r[0],
                 'group': r[3],
                 'object': _zone_view_display_string(r[1], r[2])}
                for r in query]

    @updating
    def ipblock_create(self, block_str, attributes=None, status='Container', disallow_children=False,
                       layer3domain=None, allow_overlap=False, **options):
        if status == 'Subnet':
            raise InvalidParameterError('Use ippool_add_subnet to create subnets')

        def find_parent():
            parents = Ipblock._ancestors_noparent_query(parse_ip(block_str), None) \
                .filter_by(status=get_status('Container')).all()
            if parents and all([p.layer3domain == parents[0].layer3domain for p in parents]):
                return parents[0].layer3domain

        layer3domain = _get_layer3domain_arg(layer3domain, options,
                                            guess_function=find_parent if status == 'Container' and parse_ip(block_str).prefix !=0  else None)
        ip = check_ip(parse_ip(block_str), layer3domain, options)
        ipblock = Ipblock.query_ip(ip, layer3domain).first()
        pool = self._can_change_ip(ipblock or ip, layer3domain=layer3domain)
        if ipblock:
            raise AlreadyExistsError("%s already exists in layer3domain %s with status %s" % (
                ipblock, ipblock.layer3domain.name, ipblock.status.name))
        if disallow_children:
            if Ipblock.query\
                    .filter(inside(Ipblock.address, ip))\
                    .filter_by(layer3domain=layer3domain)\
                    .count():
                raise HasChildrenError("%s from layer3domain %s has children" % (ip, layer3domain.name))
        args = dict(address=ip.address,
                    prefix=ip.prefix,
                    version=ip.version,
                    layer3domain=layer3domain,
                    status=get_status(status))
        if status in ('Subnet', 'Static'):
            ipblock = _ipblock_create(allow_overlap=allow_overlap, **args)
        else:
            ipblock = Ipblock.create(**args)
        if pool:
            record_history(pool, action='create ' + ipblock.status.name.lower(), **_cidr(ipblock))
        ipblock.set_attrs(attributes)
        return self.ipblock_get_attrs(ipblock)

    @updating
    def ipblock_remove(self, block_str, layer3domain=None, force=False, recursive=False, include_messages=False, **options):
        layer3domain = _get_layer3domain_arg(layer3domain, options, guess_function=lambda: _find_ip(block_str))
        block = check_block(get_block(block_str, layer3domain), layer3domain, options)
        pool = self._can_change_ip(block)
        if not force:
            if block.children\
                    .join(IpblockStatus)\
                    .filter(~IpblockStatus.name.in_(['Available', 'Reserved'])).count():
                raise DimError("%s %s from layer3domain %s still contains objects" % (
                    block.status.name, block_str, layer3domain.name))
        if block.pool:
            block.pool.update_modified()
        if block.status.name == 'Container':
            Messages.info('Deleting container %s from layer3domain %s' % (block, block.layer3domain.name))
        if recursive:
            delete_ipblock_rrs(db.session.query(Ipblock.id)
                               .filter(Ipblock.version == block.version)
                               .filter(Ipblock.layer3domain_id == block.layer3domain_id)
                               .filter(Ipblock.prefix == (32 if block.version == 4 else 128))
                               .filter(inside(Ipblock.address, block.ip)),
                               user=self.user)
            block.delete_subtree()
            if block.status.name == 'Subnet':
                self._delete_reverse_zones(block, force=True)
        else:
            if block.is_host:
                delete_ipblock_rrs([block.id], user=self.user)
            block.delete()
            if block.status.name == 'Subnet':
                for rip in subnet_reserved_ips(block.ip, False,False):
                    free_if_reserved(rip, layer3domain)
                if not force:
                    self._delete_reverse_zones(block, force=False)
        if pool is not None:
            action = 'delete ' + block.status.name.lower()
            record_history(pool, action=action, **_cidr(block))
        # Returning the success of the operation is useless since exceptions are used to signal failure.
        # Still here for backwards compatibility.
        if include_messages:
            return dict(messages=Messages.get(), deleted=1)
        else:
            return 1

    @readonly
    def ipblock_get_attrs(self, ipblock, layer3domain=None, full=False, **options):
        if not isinstance(ipblock, Ipblock):
            ip = parse_ip(ipblock)
            layer3domain = _get_layer3domain_arg(layer3domain)
            ipblock = Ipblock.query_ip(ip, layer3domain).first()
        else:
            ip = ipblock.ip
            layer3domain = ipblock.layer3domain
        check_block(ipblock, layer3domain, options, ip=ip)
        if not ipblock and not ip.is_host:
            raise InvalidIPError("Ipblock %s does not exist in layer3domain %s" % (ip, layer3domain.name))
        attrs = {'ip': ip.label(expanded=full)}
        ancestors = Ipblock._ancestors_noparent(ip, layer3domain, include_self=True)
        if ipblock:
            attrs.update(ipblock.get_attrs())
            attrs['status'] = ipblock.status.name
            attrs['created'] = ipblock.created
            attrs['modified'] = ipblock.modified
            attrs['modified_by'] = ipblock.modified_by
            attrs['layer3domain'] = ipblock.layer3domain.name
        elif ip.is_host:
            attrs['status'] = 'Available' if ancestors else 'Unmanaged'
        if ancestors:
            delegation = ancestors[0].delegation
            subnet = ancestors[0].subnet
            if delegation and delegation != ipblock:
                attrs['delegation'] = delegation.ip.label(expanded=full)
            if subnet:
                if ipblock != subnet:
                    attrs['subnet'] = subnet.ip.label(expanded=full)
                if ip.version == 4:
                    attrs['mask'] = IP(subnet.ip.netmask, version=ip.version).label(expanded=full)
                elif ip.version == 6:
                    attrs['prefixlength'] = subnet.prefix
                else:
                    raise Exception('Invalid ip version %r' % ip.version)
                if subnet.pool:
                    attrs['pool'] = subnet.pool.name
                if subnet.gateway:
                    attrs['gateway'] = subnet.gateway_ip.label(expanded=full)
                rname = dim.dns.get_ptr_name(ipblock or ip)
                zone = Zone.find(rname)
                if zone:
                    attrs['reverse_zone'] = zone.name
                    if ipblock:
                        ptr = db.session.query(RR.target).filter_by(type='PTR', ipblock_id=ipblock.id).first()
                        if ptr:
                            attrs['ptr_target'] = ptr.target
        return attrs

    @updating
    def ipblock_move_to(self, block, layer3domain, to_layer3domain, **options):
        layer3domain = _get_layer3domain_arg(layer3domain, options)
        to_layer3domain = _get_layer3domain_arg(to_layer3domain, options)
        block = check_block(get_block(block, layer3domain), layer3domain, options)
        self._can_change_ip_attributes(block)

        if block.status.name != 'Container':
            raise DimError('block is not a container')

        if db.session.query(Ipblock). \
                filter(Ipblock.address == block.address). \
                filter(Ipblock.layer3domain == to_layer3domain). \
                count() > 0:
            raise DimError('container %s already exists in layer3domain %s' % (block.ip, to_layer3domain.name))

        # fix all pools first
        pools = db.session.query(Ipblock). \
                join(IpblockStatus).filter(IpblockStatus.name != 'Static'). \
                filter(Ipblock.parent == block). \
                filter(Ipblock.ippool_id is not None).all()
        if block.parent is None and len(pools) > 0:
            raise DimError('block has no parent but pools that need reassignment')
        for pool in pools:
            Messages.info("moving pool %s from parent %s to parent %s" % (pool, pool.parent.ip, block.parent.ip))
            pool.parent = block.parent

        # move all static IPs in container to the new layer3domain
        ips = db.session.query(Ipblock).filter(Ipblock.parent==block). \
                join(IpblockStatus).filter(IpblockStatus.name == 'Static').all()
        block.layer3domain = to_layer3domain
        for ip in ips:
            Messages.info("moving static ip %s to layer3domain %s" % (ip.ip, to_layer3domain.name))
            ip.layer3domain = to_layer3domain

        # update parent and child settings of surrounding new containers and pools
        block._tree_update()
        return dict(messages=Messages.get())

    @updating
    def ipblock_set_attrs(self, block, attributes, layer3domain=None, **options):
        layer3domain = _get_layer3domain_arg(layer3domain, options)
        block = check_block(get_block(block, layer3domain), layer3domain, options)
        self._can_change_ip_attributes(block)
        block.set_attrs(attributes)

    @updating
    def ipblock_delete_attrs(self, block, attributes, layer3domain=None, **options):
        layer3domain = _get_layer3domain_arg(layer3domain, options)
        block = check_block(get_block(block, layer3domain), layer3domain, options)
        self._can_change_ip_attributes(block)
        block.delete_attrs(attributes)

    @updating
    def ip_mark(self, ip_str, layer3domain=None, attributes=None, full=False, **options):
        options['host'] = True
        layer3domain = _get_layer3domain_arg(layer3domain, options)
        ip = check_ip(parse_ip(ip_str), layer3domain, options)
        ipblock = self._ip_mark(ip, attributes, layer3domain=layer3domain)
        if ipblock:
            return self.ipblock_get_attrs(ipblock, full=full)

    @updating
    def ip_free(self, ip, layer3domain=None, reserved=False, include_messages=False, **options):
        ip = parse_ip(ip)
        layer3domain = _get_layer3domain_arg(layer3domain, options)
        options['host'] = 1
        pool = options.get('pool')
        if pool:
            pool = get_pool(pool)
        check_ip(ip, layer3domain, options)
        block = Ipblock.query_ip(ip, layer3domain).first()
        self._can_change_ip(block or ip, pool=pool, layer3domain=layer3domain)
        freed = None
        if block:
            if block.status.name == 'Reserved' and not reserved:
                freed = -1
            else:
                delete_ipblock_rrs([block.id], user=self.user)
                block.delete()
                freed = 1
            if freed == 0 or freed == 1:
                if pool is not None:
                    record_history(pool, action='delete static', **_cidr(block))
        else:
            freed = 0
        if include_messages:
            return dict(messages=Messages.get(), freed=freed)
        else:
            return freed

    @readonly
    def ip_list(self, type='all', pool=None, vlan=None, cidr=None, full=False, after=None, limit=None,
                attributes=None, offset=None, layer3domain=None):
        if layer3domain:
            layer3domain = _get_layer3domain_arg(layer3domain)
            return self._ip_list(layer3domain, type=type, pool=pool, vlan=None, cidr=cidr, full=full, after=after,
                                 limit=limit, attributes=attributes, offset=offset)
        else:
            ips = []
            for layer3domain in Layer3Domain.query.all():
                layer_ips = self._ip_list(layer3domain, type=type, pool=pool, vlan=vlan, cidr=cidr, full=full,
                                          after=after, limit=limit, attributes=attributes, offset=offset)
                for layer_ip in layer_ips:
                    layer_ip['layer3domain'] = layer3domain.name
                ips += layer_ips
            ips = sorted(ips, key=lambda ip: (parse_ip(ip['ip']).address, ip['layer3domain']))
            if limit:
                ips = ips[:limit]
            return ips

    @readonly
    def ip_list2(self, type='all', pool=None, vlan=None, cidr=None, full=False, after=None, limit=None,
                attributes=None, offset=None, layer3domain=None):
        ips = self.ip_list(type=type, pool=pool, vlan=vlan, cidr=cidr, full=full, after=after, limit=limit,
                           attributes=attributes, offset=offset, layer3domain=layer3domain)
        if layer3domain:
            layer3domain = _get_layer3domain_arg(layer3domain)
            total_count = self._ip_list_count(layer3domain, type=type, pool=pool, vlan=vlan, cidr=cidr, full=full)
        else:
            total_count = 0
            for layer3domain in Layer3Domain.query.all():
                total_count += self._ip_list_count(layer3domain, type=type, pool=pool, vlan=vlan, cidr=cidr, full=full)
        return {'count': total_count, 'data': ips}

    def _ip_list(self, layer3domain, type='all', pool=None, vlan=None, cidr=None, full=False, after=None, limit=None,
                attributes=None, offset=None):
        max_limit = 500000      # TODO: configuration option?
        if limit is None or limit > max_limit:
            limit = max_limit
        if type not in ('all', 'free', 'used'):
            raise InvalidParameterError('type must be one of: all, free, used')
        if int(cidr is not None) + int(pool is not None) + int(vlan is not None) > 1:
            raise InvalidParameterError('pool, cidr, vlan and owner parameters are mutually exclusive')
        if after is not None:
            after = parse_ip(after)

        parents = db.session.query(Ipblock.address, Ipblock.prefix, Ipblock.version) \
            .filter_by(layer3domain=layer3domain) \
            .join(IpblockStatus).filter_by(name='Subnet')
        if pool is not None:
            parents = parents.join(Pool).filter(Pool.name.like(make_wildcard(pool)))
        if vlan is not None:
            vlan = validate_vlan(vlan)
            pools = db.session.query(Pool.name).join(Vlan).filter(Vlan.vid == vlan)
            if pools.count() > 1:
                raise DimError('Multiple pools with vlan %d exist: %s' % (vlan, ' '.join(sorted([p.name for p in pools.all()]))))
            parents = parents.join(Vlan).filter(Vlan.vid == vlan)
        if cidr is not None:
            cidr = parse_ip(cidr)
            parents = [[cidr.address, cidr.prefix, cidr.version]]
            # Hack for DIM-259
            # Restrict the offset option to only work with the host cidrs
            if offset is not None and not cidr.is_host:
                raise InvalidParameterError('the offset option works only with host cidrs')
        else:
            parents = parents.order_by(Ipblock.version, Ipblock.address).all()

        # The code below is run only when the offset option is used
        # The offset option was added specifically for the old dim frontend
        # Won't bother fixing it since the dim frontend only uses host cidrs
        # Ideally the offset feature should be deleted as it's slow; the after option should be used instead
        if after is None and offset is not None and int(offset) > 0:
            offset = int(offset)
            start_block = None
            for (pos, (address, prefix, version)) in enumerate(parents):
                block = db.session.query(Ipblock).filter_by(address=int(address), prefix=prefix, version=version,
                                                            layer3domain=layer3domain).one()
                if type == 'all':
                    size = block.total
                elif type == 'free':
                    size = block.free
                else:
                    size = block.used
                if offset >= size:
                    offset -= size
                    if offset == 0:
                        after = block.ip.broadcast
                        break
                else:
                    start_block = block
                    break
            if start_block is not None:
                ips, _ = _get_ip_list(version=version,
                                      layer3domain=layer3domain,
                                      start=start_block.ip.address,
                                      end=start_block.ip.broadcast.address,
                                      ip_type=type,
                                      limit=offset,
                                      full=True,
                                      extra_columns=[],
                                      id_map_needed=False)
                if len(ips):
                    after = parse_ip(ips[-1]['ip'])

        ips = []
        found_after = False
        for address, prefix, version in parents:
            block = IP(int(address), prefix, version)
            if after and not found_after:
                if after in block:
                    start = after.address + 1
                    found_after = True
                else:
                    continue
            else:
                start = block.address
            end = block.broadcast.address
            block_ips = get_subnet_ips(start=start,
                                       end=end,
                                       version=block.version,
                                       layer3domain=layer3domain,
                                       ip_type=type,
                                       limit=limit - len(ips),
                                       full=full,
                                       attributes=attributes)
            # Fill 'pool' attribute if necessary
            # TODO this doesn't work if block is not inside a subnet
            if attributes is not None and 'pool' in attributes:
                pool_name = None
                ipblock = Ipblock.query.filter_by(address=address, prefix=prefix, version=version,
                                                  layer3domain=layer3domain) \
                    .join(IpblockStatus).filter_by(name='Subnet').first()
                if ipblock is not None and ipblock.pool:
                    pool_name = ipblock.pool.name
                else:
                    subnet = get_subnet(block, layer3domain)
                    if subnet is not None and subnet.pool:
                        pool_name = subnet.pool.name
                if pool_name is not None:
                    for ip in block_ips:
                        ip['pool'] = pool_name
                        if 'delegation' in attributes:
                            q_ip = parse_ip(ip['ip'])
                            parent = Ipblock._ancestors_noparent_query(q_ip, layer3domain, include_self=False)\
                                .join(IpblockStatus).filter_by(name='Delegation').limit(1)
                            if parent.count():
                                ip['delegation'] = str(parent.first().ip)
            ips.extend(block_ips)
            if len(ips) >= limit:
                break
        return ips

    def _ip_list_count(self, layer3domain, type='all', pool=None, vlan=None, cidr=None, full=False):
        if type not in ('all', 'free', 'used'):
            raise InvalidParameterError('type must be one of: all, free, used')
        if int(cidr is not None) + int(pool is not None) + int(vlan is not None) > 1:
            raise InvalidParameterError('pool, cidr, vlan and owner parameters are mutually exclusive')

        parents = db.session.query(Ipblock.address, Ipblock.prefix, Ipblock.version) \
            .filter_by(layer3domain=layer3domain) \
            .join(IpblockStatus).filter_by(name='Subnet')
        if pool is not None:
            parents = parents.join(Pool).filter(Pool.name.like(make_wildcard(pool)))
        if vlan is not None:
            vlan = validate_vlan(vlan)
            pools = db.session.query(Pool.name).join(Vlan).filter(Vlan.vid == vlan)
            if pools.count() > 1:
                raise DimError('Multiple pools with vlan %d exist: %s' % (vlan, ' '.join(sorted([p.name for p in pools.all()]))))
            parents = parents.join(Vlan).filter(Vlan.vid == vlan)
        if cidr is not None:
            cidr = parse_ip(cidr)
            parents = [[cidr.address, cidr.prefix, cidr.version]]
        else:
            parents = parents.all()

        ips_count = 0
        for address, prefix, version in parents:
            block = IP(int(address), prefix, version)
            start = block.address
            end = block.broadcast.address
            block_ips = get_subnet_ips(start=start,
                                       end=end,
                                       version=block.version,
                                       layer3domain=layer3domain,
                                       ip_type=type,
                                       limit=500000,
                                       full=full,
                                       attributes=[])
            ips_count += len(block_ips)
        return ips_count

    @readonly
    def container_list(self, container=None, layer3domain=None, full=False, include_messages=False):
        def sort_and_label(block_list):
            block_list = sorted(block_list, key=lambda x: x['ip'].address)
            for block in block_list:
                block['ip'] = block['ip'].label(expanded=full)
            return block_list

        def explore(block):
            item = {'ip': block.ip,
                    'status': block.status.name,
                    'attributes': dict((a.name.name, a.value) for a in block.attributes)}
            if block.pool:
                item['pool'] = block.pool.name
            if item['status'] == 'Container':
                children = Ipblock.query.filter_by(parent_id=block.id, layer3domain=block.layer3domain)\
                                        .options(joinedload('pool'))\
                                        .options(joinedload('attributes'))\
                                        .order_by(Ipblock.address)
                item['children'] = sort_and_label(
                    [explore(c) for c in children] +
                    [{'ip': f, 'status': 'Available'} for f in block.free_space])
            return item

        layer3domain = _get_layer3domain_arg(layer3domain)
        if container is not None:
            block = _find_ipblock(container, layer3domain, status=['Container'])
            if block is None:
                raise InvalidIPError(
                    "No containers matching '%s' exist in layer3domain %s" % (container, layer3domain.name))
            if block.status.name != 'Container':
                raise InvalidStatusError('%s from layer3domain %s is not a Container' % (block, layer3domain.name))
            blocks = [block]
        else:
            blocks = Ipblock.query.filter_by(parent_id=None, status=get_status('Container'),
                                             layer3domain=layer3domain).all()
        containers = sort_and_label(explore(block) for block in blocks)
        if include_messages:
            return {'containers': containers,
                    'messages': Messages.get()}
        else:
            return containers

    @readonly
    def ipblock_list(self, ipblock=None, ipversion=None, depth=1, status=None, attributes=None, layer3domain=None,
                     include_attributes=False, include_messages=False, include_ipblock=False):
        if status:
            status = [s.capitalize() for s in status]
        id2dict = {}
        requested_attrs = set(attributes) if include_attributes else set()
        need_pool = include_attributes and 'pool' in requested_attrs
        need_ptr_target = include_attributes and 'ptr_target' in requested_attrs
        need_attributes = (include_attributes and
                           (set(requested_attrs) - set(['pool', 'ptr_target']) or len(requested_attrs) == 0))
        del attributes

        def get_children(block=None, version=None):
            q = Ipblock.query.filter_by(parent_id=block.id if block else None)
            if version:
                q = q.filter_by(version=version)
            if status:
                q = q.join(IpblockStatus).filter(IpblockStatus.name.in_(status))
            if need_pool:
                q = q.options(joinedload('pool'))
            if need_attributes:
                q = q.options(joinedload('attributes'))
            return q.order_by(Ipblock.address).all()

        def get_free_space(parent, children):
            # parent won't have a status if it's the fake ipblock created to explore the whole IPvX space
            if parent.status and parent.status.name in ('Delegation', 'Subnet'):
                return [{'ip': f, 'status': 'Available'} for f in parent.free_space]
            else:
                free_space = Ipblock.free_ranges(parent, children)
                return [{'ip': IP(address=f[0], version=parent.ip.version),
                         'status': 'Available',
                         'number': f[1] - f[0] + 1}
                        for f in free_space]

        def sort_and_label(block_list):
            block_list = sorted(block_list, key=lambda x: (x['ip'].version, x['ip'].address))
            for block in block_list:
                block['ip'] = block['ip'].label(expanded=True)
            return block_list

        def explore(block, depth):
            item = {'ip': block.ip,
                    'status': block.status.name}
            # only return attributes if it's not empty
            if need_attributes and block.attributes:
                tmp = dict((a.name.name, a.value)
                           for a in block.attributes
                           if requested_attrs is None or a.name.name in requested_attrs)
                if tmp:
                    item['attributes'] = tmp
            if need_pool and block.pool:
                item.setdefault('attributes', {})['pool'] = block.pool.name
            if depth > 0:
                item['children'] = add_children(block, get_children(block=block), depth)
            if need_ptr_target and block.status.name == 'Static':
                id2dict[block.id] = item
            return item

        def add_children(parent, children, depth):
            return sort_and_label([explore(block, depth - 1) for block in children] +
                                  (get_free_space(parent, children) if (status is None or 'Available' in status) else []))

        if ipblock is None:
            if ipversion:
                result = add_children(Ipblock(version=ipversion, address=0, prefix=0),
                                      get_children(version=ipversion), depth)
            else:
                result = add_children(Ipblock(version=4, address=0, prefix=0), get_children(version=4), depth) + \
                         add_children(Ipblock(version=6, address=0, prefix=0), get_children(version=6), depth)
        else:
            layer3domain = _get_layer3domain_arg(layer3domain)
            block = _find_ipblock(ipblock, layer3domain, status)
            if block is None:
                raise InvalidIPError("No ipblocks matching '%s' exist" % ipblock)
            ipblock = block
            if status and ipblock.status.name not in status:
                raise InvalidStatusError('%s is not a %s' % (ipblock, status))
            ip = ipblock.ip
            if ipversion is None:
                ipversion = ip.version
            elif ipversion != ip.version:
                raise InvalidParameterError('ipblock and ipversion do not match')
            if include_ipblock:
                result = sort_and_label([explore(ipblock, depth)])
            else:
                blocks = get_children(block=ipblock)
                result = add_children(ipblock, blocks, depth)
        if id2dict:
            ptrs = db.session.query(Ipblock.id, RR.target).filter(RR.type == 'PTR') \
                .filter(Ipblock.id.in_(list(id2dict.keys()))).join(RR).all()
            for id, target in ptrs:
                if id in id2dict and target:
                    id2dict[id].setdefault('attributes', {})['ptr_target'] = target
        if include_messages:
            return {'ipblocks': result,
                    'messages': Messages.get()}
        else:
            return result

    @updating
    def ippool_create(self, name, vlan=None, attributes=None, owner=None, layer3domain=None):
        self.user.can_network_admin()
        if owner is not None:
            owner = get_group(owner)
        if Pool.query.filter_by(name=name).count():
            raise AlreadyExistsError("A pool named '%s' already exists" % name)
        layer3domain = _get_layer3domain_arg(layer3domain)
        pool = Pool(name=name, owner=owner, layer3domain=layer3domain)
        if vlan is not None:
            pool.vlan = make_vlan(vlan)
        db.session.add(pool)
        pool.set_attrs(attributes)

    @updating
    def ippool_delete(self, name, force=False, delete_subnets=False):
        self.user.can_network_admin()
        pool = get_pool(name)
        if force or not pool.subnets:
            if delete_subnets:
                for block in pool.subnets:
                    self.ipblock_remove(str(block), force=True, recursive=True)
            self._delete_rights_for('Ippool', pool.id)
            db.session.delete(pool)
            return 1
        else:
            return 0

    @updating
    def ippool_rename(self, old_name, new_name):
        self.user.can_network_admin()
        if Pool.query.filter_by(name=new_name).count():
            raise AlreadyExistsError("A pool named '%s' already exists" % new_name)
        get_pool(old_name).name = new_name

    @readonly
    def ippool_count(self, pool=None, vlan=None, cidr=None, can_allocate=None, owner=None, layer3domain=None):
        return self._ippool_query(pool, vlan=vlan, cidr=cidr, can_allocate=can_allocate, owner=owner, layer3domain=layer3domain).count()

    @readonly
    def ippool_list(self, pool=None, vlan=None, cidr=None, full=False, include_subnets=True,
                    can_allocate=None, owner=None, favorite_only=False, limit=None, offset=0,
                    layer3domain=None, fields=False, attributes=['name', 'vlan', 'subnets', 'layer3domain']):
        if len(attributes) == 0:
            raise DimError('no attributes selected to return')
        ids = self._ippool_query(pool, vlan, cidr, can_allocate, owner, layer3domain)
        if favorite_only:
            ids = ids.join(FavoritePool).filter(FavoritePool.user_id == self.user.id)
        if limit is not None:
            ids = ids.order_by(Pool.name).offset(offset).limit(limit)
        else:
            ids = ids.order_by(Pool.name)
        ids = select([ids.subquery().c.id])
        ids = select([ids.alias()])

        custom_attr = set(attributes) - set(Pool.AttrNameClass.reserved)
        qfields = [Pool.name, Pool.created, Pool.modified, Pool.modified_by, Vlan.vid, Layer3Domain.name.label('layer3domain')]
        if fields:
            if self.user.is_super_admin:
                qfields.append(literal(True).label('can_allocate'))
            else:
                rights = db.session.query(func.count(AccessRight.id)).correlate(Pool)\
                    .filter(or_(AccessRight.access == 'network_admin',
                                and_(AccessRight.access == 'allocate',
                                     AccessRight.object_class == 'Ippool',
                                     AccessRight.object_id == Pool.id)))\
                    .outerjoin(GroupRight).outerjoin(Group).outerjoin(*Group.users.attr)\
                    .filter(User.username == self.username).scalar_subquery()
                qfields.append(and_(rights > 0).label('can_allocate'))
        if include_subnets:
            qfields.extend([Ipblock.address, Ipblock.prefix, Ipblock.version])
        q = db.session.query(*qfields)
        if include_subnets:
            q = q.outerjoin(Pool.subnets)
        q = q.outerjoin(Pool.vlan).filter(Pool.id.in_(ids))
        q = q.join(Pool.layer3domain)

        pools = {}
        for row in q:
            cpool = pools.setdefault(row.name, {})
            cpool['name'] = row.name
            if 'vlan' in attributes:
                cpool['vlan'] = row.vid
            if 'layer3domain' in attributes:
                cpool['layer3domain'] = row.layer3domain
            if 'created' in attributes:
                cpool['created'] = row.created
            if 'modified' in attributes:
                cpool['modified'] = row.modified
            if 'modified_by' in attributes:
                cpool['modified_by'] = row.modified_by
            if include_subnets:
                subnets = cpool.setdefault('subnets', [])
                if row.address is not None:
                    subnets.append(IP(int(row.address), row.prefix, row.version).label(expanded=full))
            if fields:
                cpool['can_allocate'] = row.can_allocate

        if custom_attr:
            attrs = db.session.query(Pool.name, Pool.AttrNameClass.name, PoolAttr.value) \
                   .join(PoolAttr, Pool.id == PoolAttr.ippool_id) \
                   .join(Pool.AttrNameClass, PoolAttr.name_id == Pool.AttrNameClass.id) \
                   .filter(Pool.AttrNameClass.name.in_(custom_attr))
            for pool_name, attr_name, attr_val in attrs:
                if pool_name in pools:
                    pools[pool_name][attr_name] = attr_val
        return [pools[name] for name in sorted(pools.keys())]

    @readonly
    def ippool_list2(self, pool=None, vlan=None, cidr=None, full=False, include_subnets=True, order='asc',
                    can_allocate=None, owner=None, favorite_only=False, limit=None, offset=0, fields=False,
                    layer3domain=None):
        ids = self._ippool_query(pool, vlan, cidr, can_allocate, owner, layer3domain)
        if favorite_only:
            ids = ids.join(FavoritePool).filter(FavoritePool.user_id == self.user.id)
        total_count = ids.count()
        if order == 'desc':
            ids = ids.order_by(desc(Pool.name))
        else:
            ids = ids.order_by(Pool.name)
        if limit is not None:
            ids = ids.offset(offset).limit(limit)
        ids = select([ids.subquery().c.id])
        ids = select([ids.alias()])

        qfields = [Pool.name, Vlan.vid, Pool.version]
        if fields:
            if self.user.is_super_admin:
                qfields.append(literal(True).label('can_allocate'))
            else:
                rights = db.session.query(func.count(AccessRight.id)).correlate(Pool) \
                    .filter(or_(AccessRight.access == 'network_admin',
                                and_(AccessRight.access == 'allocate',
                                     AccessRight.object_class == 'Ippool',
                                     AccessRight.object_id == Pool.id))) \
                    .outerjoin(GroupRight).outerjoin(Group).outerjoin(*Group.users.attr) \
                    .filter(User.username == self.username).scalar_subquery()
                qfields.append(and_(rights > 0).label('can_allocate'))
        if include_subnets:
            qfields.extend([Ipblock.address, Ipblock.prefix, Ipblock.version])
        q = db.session.query(*qfields)
        if include_subnets:
            q = q.outerjoin(Pool.subnets)
        q = q.outerjoin(Pool.vlan).filter(Pool.id.in_(ids))

        pools = {}
        for row in q:
            cpool = pools.setdefault(row.name, {})
            cpool['name'] = row.name
            cpool['vlan'] = row.vid
            cpool['version'] = row.version
            if include_subnets:
                subnets = cpool.setdefault('subnets', [])
                if row.address is not None:
                    subnets.append(IP(int(row.address), row.prefix, row.version).label(expanded=full))
            if fields:
                cpool['can_allocate'] = row.can_allocate
        reverse = order == 'desc'
        return {'count': total_count, 'data': [pools[name] for name in sorted(list(pools.keys()), reverse=reverse)]}

    @updating
    def ippool_add_subnet(self, pool, cidr,
                          attributes=None,
                          gateway=None,
                          allow_move=False,
                          allow_overlap=False,
                          dont_reserve_network_broadcast=False,
                          no_reserve_class_c_boundaries=False,
                          include_messages=False):
        self.user.can_network_admin()
        pool = get_pool(pool)
        ip = parse_ip(cidr)
        created = 0
        block = Ipblock.query_ip(ip, pool.layer3domain).first()
        if block:
            if block.status.name != 'Subnet':
                raise InvalidStatusError("'%s' from layer3domain %s cannot be added to a pool because it is a %s" % (
                    ip, block.layer3domain.name, block.status.name))
            if block.pool:
                if block.pool == pool:
                    raise AlreadyExistsError(
                        "The subnet '%s' from layer3domain '%s' is already part of the pool '%s'" % (
                        ip, block.layer3domain.name, block.pool.name))
                elif not allow_move:
                    raise AlreadyExistsError(
                        "The subnet '%s' from layer3domain '%s' cannot be added to the pool '%s' because it is part of the pool '%s'"
                        % (ip, block.layer3domain.name, pool.name, block.pool.name))
            block.vlan_id = pool.vlan_id
            self._add_block(pool, block)
            block.update_modified()
        else:
            block = _ipblock_create(address=ip.address,
                                    prefix=ip.prefix,
                                    version=ip.version,
                                    status=get_status('Subnet'),
                                    layer3domain=pool.layer3domain,
                                    allow_overlap=allow_overlap,
                                    vlan=pool.vlan)
            self._add_block(pool, block)
            self._create_reverse_zones(block)
            created = 1
        # Setting the attribute before getting another instance from the ORM prevents the 'set' event
        # from being triggered. This behavior changed unexpectedly in SQLAlchemy 1.0.
        self._set_gateway(block, gateway)
        for rip in subnet_reserved_ips(ip, dont_reserve_network_broadcast, no_reserve_class_c_boundaries):
            try:
                reserve(rip, pool.layer3domain)
            except DimError as e:
                Messages.info(str(e))
        block.set_attrs(attributes)
        record_history(pool, action='create subnet', **_cidr(block))
        if include_messages:
            return {'messages': Messages.get(), 'created': created, 'layer3domain': pool.layer3domain.name}
        else:
            return created

    @readonly
    def ippool_get_subnets(self, pool, full=False, include_usage=True):
        pool = get_pool(pool)
        subnets = []
        for subnet in pool.subnets:
            properties = {'priority': subnet.priority,
                          'subnet': subnet.ip.label(expanded=full),
                          'gateway': subnet.gateway_ip.label(expanded=full) if subnet.gateway else None}
            if include_usage:
                properties['free'] = subnet.free
                properties['static'] = subnet.static
                properties['total'] = subnet.total
            subnets.append(properties)
        return subnets

    @readonly
    def ippool_get_delegations(self, name, full=False, include_usage=True):
        pool = get_pool(name)
        subnet = aliased(Ipblock)
        delegations = Ipblock.query\
            .join(IpblockStatus).filter_by(name='Delegation')\
            .join(subnet, Ipblock.parent_id == subnet.id)\
            .filter(subnet.ippool_id == pool.id)\
            .order_by(Ipblock.address)
        result = []
        for delegation in delegations:
            properties = {'delegation': delegation.label(expanded=full)}
            if include_usage:
                properties['free'] = delegation.free
                properties['total'] = delegation.total
            result.append(properties)
        return result

    @updating
    def ippool_set_vlan(self, pool, vlan):
        self.user.can_network_admin()
        pool = get_pool(pool)
        vlan = make_vlan(vlan)
        if pool.vlan and pool.vlan == vlan:
            return
        for subnet in pool.subnets:
            if subnet.vlan != vlan:
                subnet.vlan = vlan
                subnet.update_modified()
        pool.vlan = vlan

    @updating
    def ippool_remove_vlan(self, pool):
        self.user.can_network_admin()
        pool = get_pool(pool)
        if pool.vlan is None:
            return
        for subnet in pool.subnets:
            if subnet.vlan is not None:
                subnet.vlan = None
                subnet.update_modified()
        pool.vlan = None

    @updating
    def ippool_set_layer3domain(self, pool, layer3domain):
        self.user.can_network_admin()
        pool = get_pool(pool)
        from_layer3domain = pool.layer3domain
        layer3domain = get_layer3domain(layer3domain)
        if from_layer3domain.name == layer3domain.name:
            raise DimError('Pool %s is already in layer3domain %s' % (pool.name, layer3domain.name))
        logging.info("moving pool %s from layer3domain %s to layer3domain %s" % (pool.name, from_layer3domain.name, layer3domain.name))

        pool.layer3domain = layer3domain
        # check overlaps in target layer3domain and check for new parent
        for subnet in pool.subnets:
            parent = db.session.query(Ipblock) \
                    .filter(Ipblock.layer3domain == layer3domain) \
                    .filter(Ipblock.address <= subnet.address) \
                    .filter(Ipblock.prefix < subnet.prefix) \
                    .join(IpblockStatus).filter(IpblockStatus.name == 'Container') \
                    .order_by(Ipblock.address.desc(), Ipblock.prefix) \
                    .limit(1)
            if parent.first() is None:
                raise DimError('could not find new parent for subnet "%s" in new layer3domain' % (subnet.ip))

            overlaps = db.session.query(Ipblock) \
                    .filter(Ipblock.layer3domain == layer3domain) \
                    .filter(Ipblock.address.between(subnet.address, subnet.ip.broadcast.address)) \
                    .filter(Ipblock.prefix >= subnet.prefix) \
                    .count()
            if overlaps > 0:
                raise DimError('''subnet %s can't be moved, %d existing containers, subnets or static entries are in the way''' % (subnet.ip, overlaps))

            overlaps = db.session.query(Ipblock) \
                    .filter(Ipblock.layer3domain == layer3domain) \
                    .filter(Ipblock.address <= subnet.address) \
                    .filter(Ipblock.prefix < subnet.prefix) \
                    .join(IpblockStatus).filter(IpblockStatus.name != 'Container') \
                    .order_by(Ipblock.address.desc(), Ipblock.prefix) \
                    .limit(1)
            if (overlaps is not None and overlaps.first() is not None
                    and subnet.parent is not None):
                    overlap = overlaps.first()
                    if (overlap.address >= subnet.address and overlap.address <= subnet.ip.broadcast.address) or \
                       (overlap.ip.broadcast.address >= subnet.address and overlap.ip.broadcast.address <= subnet.ip.broadcast.address):
                        raise DimError('''subnet %s can't be moved, %s already exists and is a %s''' % (subnet.ip, overlaps.first().ip, overlaps.first().status.name))

            subnet.layer3domain = layer3domain
            subnet.parent = parent.first()
            Messages.info("Changing subnet %s to new parent %s in layer3domain %s" % (subnet.ip, parent.first().ip, layer3domain.name))

            self._create_reverse_zones(subnet)
            children = subnet.children.all()
            for child in children:
                logging.info("Changing child %s of subnet %s to layer3domain %s" % (child.ip, subnet.ip, layer3domain.name))
                Messages.info("Changing child %s of subnet %s to layer3domain %s" % (child.ip, subnet.ip, layer3domain.name))
                if child.status.name == 'Static':
                    child.layer3domain = layer3domain
                    rr_name = dim.dns.get_ptr_name(child.ip)
                    rr_zone = dim.dns.get_rr_zone(rr_name, None, None)
                    new_view = db.session.query(ZoneView).filter(ZoneView.zone == rr_zone).filter(ZoneView.name == layer3domain.name).first()
                    if new_view is None:
                        raise DimError('view for layer3domain %s does not exist for zone %s' % (layer3domain.name, rr_zone.name))

                    rr = db.session.query(RR).filter(RR.ipblock_id == child.id).filter(RR.type == 'PTR').first()
                    if rr is not None:
                        rr.delete(send_delete_rr_event = True)
                        Messages.info("Deleting RR %s from %s" % (rr.bind_str(relative=True), rr.view))
                        make_transient(rr)
                        rr.id = None
                        rr.view = new_view
                        rr.insert()
                        Messages.info("Creating RR {rr}{comment_msg} in {view_msg}".format(
                            rr=rr.bind_str(relative=True),
                            comment_msg=' comment {0}'.format(rr.comment),
                            view_msg=rr.view))

                elif child.status.name == 'Delegation':
                    children += child.children.all()
                    child.layer3domain = layer3domain
                elif child.status.name == 'Reserved':
                    child.layer3domain = layer3domain
                else:
                    raise DimError("unhandled state '%s' for IP %s" % (child.status.name, child.ip))

            # create a copy of the subnet to delete possible existing reverse zones
            # _delete_reverse_zone expects the block to not exist anymore, thats why
            # we need a copy.
            logging.info('trying to delete old reverse zone for subnet %s' % (subnet))
            subnet_to_delete = Ipblock(
                    version = subnet.version,
                    address = subnet.address,
                    prefix = subnet.prefix,
                    layer3domain = from_layer3domain)
            self._delete_reverse_zones(subnet_to_delete, force=True)

        logging.info("finished moving pool %s from layer3domain %s to layer3domain %s" % (pool.name, from_layer3domain.name, layer3domain.name))
        return {'messages': Messages.get()}

    @readonly
    def ippool_get_attrs(self, pool):
        pool = get_pool(pool)
        attrs = pool.get_attrs()
        attrs.update(
            {'name': pool.name,
             'created': pool.created,
             'modified': pool.modified,
             'modified_by': pool.modified_by,
             'layer3domain': pool.layer3domain.name})
        if pool.vlan:
            attrs['vlan'] = pool.vlan.vid
        if pool.owner:
            attrs['owner'] = pool.owner.name
            if pool.owner.department_number:
                attrs['department_number'] = pool.owner.department_number
        return attrs

    @updating
    def ippool_set_attrs(self, name, attributes):
        pool = get_pool(name)
        for key in attributes:
            if not self.user.can_set_attribute(pool, key):
                self.user.can_modify_pool_attributes()
        get_pool(name).set_attrs(attributes)

    @updating
    def ippool_set_owner(self, poolname, owner):
        self.user.can_modify_pool_attributes()
        get_pool(poolname).owner = get_group(owner)

    @updating
    def ippool_unset_owner(self, poolname):
        self.user.can_modify_pool_attributes()
        get_pool(poolname).owner = None

    @updating
    def ippool_delete_attrs(self, name, attribute_names):
        pool = get_pool(name)
        for key in attribute_names:
            if not self.user.can_set_attribute(pool, key):
                self.user.can_modify_pool_attributes()
        get_pool(name).delete_attrs(attribute_names)

    @readonly
    def ippool_favorite(self, pool):
        '''Returns true iff pool is favorite'''
        return FavoritePool.query.filter_by(user_id=self.user.id)\
                           .join(Pool).filter(Pool.name == pool).count() > 0

    @updating
    def ippool_favorite_add(self, pool):
        pool = get_pool(pool)
        db.session.merge(FavoritePool(user_id=self.user.id, ippool_id=pool.id))

    @updating
    def ippool_favorite_remove(self, pool):
        pool = get_pool(pool)
        FavoritePool.query.filter_by(user_id=self.user.id).filter_by(ippool_id=pool.id).delete()

    @updating
    def subnet_set_priority(self, subnet, priority, layer3domain=None, **options):
        self.user.can_network_admin()
        layer3domain = _get_layer3domain_arg(layer3domain, options)
        subnet = get_block(subnet, layer3domain)
        options['status'] = 'Subnet'
        check_block(subnet, layer3domain, options)
        if subnet.pool is None:
            raise NotInPoolError("%s from layer3domain %s is not part of any pool" % (subnet, subnet.layer3domain.name))
        try:
            if not isinstance(priority, int):
                priority = int(priority)
        except:
            raise InvalidPriorityError("Invalid priority: %r" % priority)
        if priority <= 0:
            raise InvalidPriorityError("Invalid priority: %r" % priority)
        if subnet.priority == priority:
            return
        demote_prio = priority
        for s in subnet.pool.subnets:
            if s == subnet:
                continue
            if s.priority == demote_prio:
                demote_prio += 1
                s.priority = demote_prio
        subnet.priority = priority
        subnet.update_modified()

    @updating
    def subnet_set_gateway(self, subnet, gateway, layer3domain=None, **options):
        self.user.can_network_admin()
        layer3domain = _get_layer3domain_arg(layer3domain, options)
        subnet = get_block(subnet, layer3domain)
        options['status'] = 'Subnet'
        check_block(subnet, layer3domain, options)
        self._set_gateway(subnet, gateway)

    @updating
    def subnet_remove_gateway(self, subnet, layer3domain=None, **options):
        return self.subnet_set_gateway(subnet, None, **options)

    @updating
    def ippool_get_ip(self, pool, attributes=None, full=False):
        pool = get_pool(pool)
        ipblock = self._ip_mark(allocate_ip(pool), layer3domain=pool.layer3domain, attributes=attributes, pool=pool)
        if ipblock:
            return self.ipblock_get_attrs(ipblock, full=full)

    @updating
    def ippool_get_delegation(self, pool, prefix, maxsplit=0, attributes=None, full=False):
        pool = get_pool(pool)
        self.user.can_allocate(pool)
        addresses = allocate_delegation(pool, prefix, maxsplit)
        for addr in addresses:
            record_history(pool, action='create delegation', **_cidr(addr))
        return self._delegation_mark(addresses,
                                     layer3domain=pool.layer3domain,
                                     attributes=attributes,
                                     full=full)

    @updating
    def ipblock_get_ip(self, block, layer3domain=None, attributes=None, full=False, **options):
        layer3domain = _get_layer3domain_arg(layer3domain, options)
        block = check_block(get_block(block, layer3domain), layer3domain, options)
        ipblock = self._ip_mark(allocate_ip(block), layer3domain=layer3domain, attributes=attributes)
        if ipblock:
            return self.ipblock_get_attrs(ipblock, full=full)

    @updating
    def ipblock_get_delegation(self, block, prefix, layer3domain=None, maxsplit=0, attributes=None, full=False, **options):
        layer3domain = _get_layer3domain_arg(layer3domain, options)
        block = check_block(get_block(block, layer3domain), layer3domain, options)
        pool = self._can_change_ip(block)
        addresses = allocate_delegation(block, prefix, maxsplit)
        if pool:
            for addr in addresses:
                record_history(pool, action='create delegation', **_cidr(addr))
        return self._delegation_mark(addresses,
                                     layer3domain=pool.layer3domain,
                                     attributes=attributes,
                                     full=full)

    @updating
    def zone_create(self, zone, profile=False, from_profile=None, soa_attributes=None, attributes=None,
                    empty_profile_warning=True, view_name='default', owner=None,
                    inherit_rights=True, inherit_zone_groups=True, inherit_owner=True):
        if profile:
            self.user.can_dns_admin()
        zone_name = zone
        zone = Zone.from_display_name(zone)
        if owner is not None:
            owner = get_group(owner)
        if is_reverse_zone(zone):
            self.user.can_create_reverse_zones()
        elif not profile:
            self.user.can_create_forward_zones()
        validate_soa_attrs(soa_attributes)
        parent_zone = dim.dns.zone_available_check(zone, profile)

        if from_profile:
            profile_name = from_profile
            from_profile = get_zone(from_profile, profile=True)
            Messages.info('Creating {obj} {name} with profile {profile}'.format(
                obj=ZONE_OBJ_NAME[profile].lower(),
                name=zone_name,
                profile=profile_name))
        else:
            if empty_profile_warning:
                Messages.warn('Creating {obj} {name} without profile'.format(
                    obj=ZONE_OBJ_NAME[profile].lower(),
                    name=zone_name))
                if not soa_attributes or 'primary' not in soa_attributes:
                    Messages.warn('Primary NS for this Domain is now localhost.')
        if owner is None and inherit_owner and parent_zone is not None and parent_zone.owner:
            owner = parent_zone.owner
        new_zone = Zone.create(name=zone, profile=profile, attributes=attributes, owner=owner)
        if not profile and parent_zone is not None:
            # Create subzone: move rrs from parent zone and inherit stuff
            create_subzone(new_zone, parent_zone, from_profile, soa_attributes, self.user,
                           inherit_rights=inherit_rights, inherit_zone_groups=inherit_zone_groups)
        else:
            ZoneView.create(zone=new_zone, name=view_name, from_profile=from_profile, soa_attributes=soa_attributes)
            if not inherit_rights or not inherit_zone_groups or not inherit_owner:
                Messages.warn('Zone %s does not have a parent zone. Inherit options ignored' % zone_name)

        # Copy NS
        for view in new_zone.views:
            dim.dns.copy_ns_rrs(new_zone, view, self.user)

        # If user has zone_create, grant zone_admin for zone to user-group
        zone_create_group = Group.query.filter(Group.users.any(id=self.user.id)) \
            .filter(Group.rights.any(access='zone_create')).first()
        if zone_create_group:
            _group_grant_access(zone_create_group, 'zone_admin', zone_name)
        return {'messages': Messages.get()}

    @readonly
    def zone_delete_check(self, zone, profile=False):
        self._check_zone_delete(zone=zone, profile=profile)

    @readonly
    def zone_delete_view_check(self, zone, view):
        self._check_zone_delete_view(zone=zone, view=view)

    @updating
    def zone_delete(self, zone, profile=False, cleanup=False):
        zone = self._check_zone_delete(zone=zone, profile=profile)
        self._zone_delete_view(zone.views[0], cleanup, also_deleting_zone=True)
        parent_zone = get_parent_zone(zone.name)
        if parent_zone is not None:
            for key in zone.keys:
                delete_ds_rr(key, parent_zone=parent_zone, user=self.user)
        self._delete_rights_for('Zone', zone.id)
        db.session.delete(zone)
        return {'messages': Messages.get()}

    @updating
    def zone_rename(self, zone, new_name, profile=False):
        # This does not update rr values
        self.user.can_network_admin()
        if not profile:
            raise InvalidParameterError('Zone rename not implemented')
        new_name = Zone.from_display_name(new_name)
        zone_name = zone
        zone = get_zone(zone_name, profile=profile)
        dim.dns.zone_available_check(new_name, profile)
        zone.name = new_name
        for view in zone.views:
            for rr in RR.query.filter_by(view=view):
                rr.name = rr.name[:-len(zone_name) - 1] + new_name + '.'

    @updating
    def zone_create_view(self, zone, view, from_profile=None, soa_attributes=None):
        zone = get_zone(zone, profile=False)
        self.user.can_manage_zone(zone)
        if is_reverse_zone(zone.name):
            if Layer3Domain.query.filter_by(name=view).count() == 0:
                raise DimError('No layer3domain named %s' % view)
        if ZoneView.query.filter_by(zone=zone, name=view).count():
            raise AlreadyExistsError('View {0} already exists for zone {1}'.format(view, zone.display_name))
        if from_profile:
            from_profile = get_zone(from_profile, profile=True)
        view = ZoneView.create(zone, view, from_profile=from_profile, soa_attributes=soa_attributes)
        dim.dns.copy_ns_rrs(zone, view, self.user)
        _check_parent_view(view)
        return {'messages': Messages.get()}

    @updating
    def zone_rename_view(self, zone, view, new_name):
        zone = get_zone(zone, profile=False)
        self.user.can_manage_zone(zone)
        if ZoneView.query.filter_by(zone=zone, name=new_name).count():
            raise AlreadyExistsError("A zone view named '%s' already exists for the zone %s" % (new_name, zone.display_name))
        view = get_view(zone, view)
        view.name = new_name

    @updating
    def zone_delete_view(self, zone, view, cleanup=False):
        view = self._check_zone_delete_view(zone, view)
        self._zone_delete_view(view, cleanup, also_deleting_zone=False)
        return {'messages': Messages.get()}

    @readonly
    def zone_count(self, pattern='*', profile=False, alias=False, can_create_rr=None, can_delete_rr=None):
        pattern = make_wildcard(pattern)
        q = db.session.query(Zone.name).filter(Zone.name.like(pattern)).filter_by(profile=profile)
        if (can_delete_rr or can_create_rr) and not self.user.is_super_admin:
            views = self._changeable_views(can_create_rr=can_create_rr, can_delete_rr=can_delete_rr)
            q = q.filter(0 < views)
        return q.count()

    @readonly
    def zone_list(self, pattern='*', limit=None, profile=False, owner=None, alias=False, fields=False, offset=0,
                  can_create_rr=None, can_delete_rr=None, exclude_reverse=False):
        pattern = make_wildcard(pattern)
        qfields = []
        if fields:
            views_stmt = db.session.query(
                    ZoneView.zone_id,
                    func.count(distinct(ZoneView.id)).label('views'),
                    func.count(ZoneGroup.id).label('zone_groups')
                    ).select_from(ZoneView)\
                            .outerjoin(ZoneGroup, ZoneView.groups)\
                            .group_by(ZoneView.zone_id)\
                            .subquery()
            qfields.append(views_stmt.c.views)
            qfields.append(views_stmt.c.zone_groups)
            if self.user.is_super_admin:
                qfields.extend([literal(True).label('can_create_rr'), literal(True).label('can_delete_rr')])
            else:
                qfields.append(and_(0 < self._changeable_views(can_create_rr=True, can_delete_rr=False)).label('can_create_rr'))
                qfields.append(and_(0 < self._changeable_views(can_create_rr=False, can_delete_rr=True)).label('can_delete_rr'))
        zones = db.session.query(Zone.name.label('name'), *qfields).filter(Zone.name.like(pattern)).filter(Zone.profile==profile)\
            .order_by(Zone.name)
        if owner is not None:
            zones = zones.join(Group).filter(Zone.owner == get_group(owner))
        if exclude_reverse:
            zones = zones.filter(Zone.name.notlike('%in-addr.arpa')).filter(Zone.name.notlike('%ip6.arpa'))
        if (can_delete_rr or can_create_rr) and not self.user.is_super_admin:
            zones = zones.filter(0 < self._changeable_views(can_create_rr=can_create_rr, can_delete_rr=can_delete_rr))
        if fields:
            zones = zones.outerjoin(views_stmt, Zone.id == views_stmt.c.zone_id)
        zones = zones.order_by('name')
        if limit:
            offset = int(offset)
            zones = zones[offset:offset + int(limit)]

        def result_dict(zone, fields):
            res = {'name': zone.name if zone.name != '' else '.'}
            if fields:
                res.update({'views': zone.views, 'zone_groups': zone.zone_groups,
                            'can_create_rr': zone.can_create_rr, 'can_delete_rr': zone.can_delete_rr})
            return res
        return [result_dict(zone, fields) for zone in zones]

    @readonly
    def zone_list2(self, pattern='*', profile=False, owner=None, limit=None, offset=0, fields=False,
                   favorite_only=False, order='asc',
                   can_create_rr=None, can_delete_rr=None,
                   forward_zones=True, ipv4_reverse_zones=False, ipv6_reverse_zones=False):
        pattern = make_wildcard(pattern)
        qfields = []
        if fields:
            views_stmt = db.session.query(
                    ZoneView.zone_id,
                    func.count(distinct(ZoneView.id)).label('views'),
                    func.count(ZoneGroup.id).label('zone_groups')
                    ).select_from(ZoneView)\
                            .outerjoin(ZoneGroup, ZoneView.groups)\
                            .group_by(ZoneView.zone_id)\
                            .subquery()
            qfields.append(views_stmt.c.views)
            qfields.append(views_stmt.c.zone_groups)
            if self.user.is_super_admin:
                qfields.extend([literal(True).label('can_create_rr'), literal(True).label('can_delete_rr')])
            else:
                qfields.append(and_(0 < self._changeable_views(can_create_rr=True, can_delete_rr=False)).label('can_create_rr'))
                qfields.append(and_(0 < self._changeable_views(can_create_rr=False, can_delete_rr=True)).label('can_delete_rr'))
        zones = db.session.query(Zone.name.label('name'), Zone.id, Zone.keys.any().label('dnssec').label('dnssec'),
                                 *qfields).filter(Zone.name.like(pattern)).filter(Zone.profile==profile)
        if owner is not None:
            zones = zones.join(Group).filter(Zone.owner == get_group(owner))

        if (can_delete_rr or can_create_rr) and not self.user.is_super_admin:
            zones = zones.filter(0 < self._changeable_views(can_create_rr=can_create_rr, can_delete_rr=can_delete_rr))

        if fields:
            zones = zones.outerjoin(views_stmt, Zone.id == views_stmt.c.zone_id)

        # Filter based on zone type (forward/reverse)
        if not forward_zones and not ipv4_reverse_zones and not ipv6_reverse_zones:
            return {'count': 0, 'data': []}
        ipv4_reverse_filter = Zone.name.like('%in-addr.arpa')
        ipv6_reverse_filter = Zone.name.like('%ip6.arpa')
        if forward_zones:
            if not ipv4_reverse_zones:
                zones = zones.filter(not_(ipv4_reverse_filter))
            if not ipv6_reverse_zones:
                zones = zones.filter(not_(ipv6_reverse_filter))
        else:
            reverse_filter = []
            if ipv4_reverse_zones:
                reverse_filter.append(ipv4_reverse_filter)
            if ipv6_reverse_zones:
                reverse_filter.append(ipv6_reverse_filter)
            zones = zones.filter(or_(*reverse_filter))

        if favorite_only:
            zones = zones.join(ZoneView).join(FavoriteZoneView).filter(FavoriteZoneView.user_id == self.user.id).distinct()

        if order == 'desc':
            zones = zones.order_by('name desc')
        else:
            zones = zones.order_by('name')
        count = zones.count()
        if limit:
            offset = int(offset)
            zones = zones[offset:offset + int(limit)]

        by_id = collections.defaultdict(list)
        # Join views with selected zones
        if count:
            views = db.session.query(ZoneView.zone_id,
                                     ZoneView.name,
                                     *self._zone_view_rights())\
                              .filter(ZoneView.zone_id.in_(z.id for z in zones))\
                              .order_by(ZoneView.name)
            if favorite_only:
                views = views.join(FavoriteZoneView).filter_by(user_id=self.user.id)
            for view in views:
                by_id[view.zone_id].append({
                    'name': view.name,
                    'can_create_rr': view.can_create_rr,
                    'can_delete_rr': view.can_delete_rr,
                })

        def result_dict(zone, by_id, fields):
            res = {
                'name': zone.name if zone.name != '' else '.',
                'dnssec': bool(zone.dnssec),
                'views': []
            }
            if fields:
                res.update({'can_create_rr': zone.can_create_rr, 'can_delete_rr': zone.can_delete_rr})
            if zone.id in by_id:
                res['views'] = by_id[zone.id]
            return res

        return {'count': count, 'data': [result_dict(zone, by_id, fields) for zone in zones]}

    @readonly
    def zone_dump(self, zone, view=None, profile=False):
        zone_name = zone
        zone = get_zone(zone_name, profile)
        view = get_view(zone, view)
        rrs = db.session.query(RR.name, RR.ttl, RR.type, RR.value, RR.target).filter(RR.zoneview_id == view.id).order_by(RR.name).all()
        rrs_info = [(Zone.from_display_name(zone_name) + '.', view.ttl, 'IN', 'SOA', view.soa_value())]
        for rr in rrs:
            rr_name = rr.name
            rrs_info.append([rr_name,
                             rr.ttl or view.ttl,
                             'IN',
                             rr.type,
                             RR.get_class(rr.type).fqdn_target(rr.value, zone_name)])
        return '\n'.join(['\t'.join(str(f) for f in rr if f is not None) for rr in rrs_info])

    @readonly
    def zone_list_zone_groups(self, zone, view=None):
        zone = get_zone(zone, profile=False)
        if view is not None:
            views = [get_view(zone, view)]
        else:
            views = zone.views
        result = []
        for view in views:
            for group in view.groups:
                result.append({'view': view.name, 'zone_group': group.name})
        return result

    @readonly
    def zone_list_views(self, zone, can_create_rr=None, can_delete_rr=None, fields=False):
        zone = get_zone(zone, profile=False)
        qfields = []
        if fields:
            qfields.extend(self._zone_view_rights())
        views = db.session.query(ZoneView.name, *qfields).filter_by(zone=zone).group_by(ZoneView.id, ZoneView.name, *qfields).order_by(ZoneView.name)
        if (can_create_rr or can_delete_rr) and not self.user.is_super_admin:
            views = self._filter_views(views, can_create_rr=can_create_rr, can_delete_rr=can_delete_rr)

        def result(view):
            res = {'name': view.name}
            if fields:
                res.update({'can_create_rr': view.can_create_rr, 'can_delete_rr': view.can_delete_rr})
            return res
        return [result(v) for v in views.all()]

    @readonly
    def zone_favorite(self, zone, view=None):
        '''Returns true iff zone/view is favorite'''
        q = FavoriteZoneView.query.filter(FavoriteZoneView.user_id == self.user.id) \
            .join(ZoneView).join(Zone).filter(Zone.name == zone)
        if view is not None:
            q = q.filter(ZoneView.name == view)
        return q.count() > 0

    @updating
    def zone_favorite_add(self, zone, view=None):
        zone = get_zone(zone, profile=False)
        view = get_view(zone, view)
        db.session.merge(FavoriteZoneView(user_id=self.user.id, zoneview_id=view.id))

    @updating
    def zone_favorite_remove(self, zone, view=None):
        zone = get_zone(zone, profile=False)
        view = get_view(zone, view)
        FavoriteZoneView.query.filter_by(user_id=self.user.id).filter_by(zoneview_id=view.id).delete()

    @readonly
    def zone_list_keys(self, zone_name):
        zone = get_zone(zone_name, profile=False)
        return [{'label': key.label,
                 'type': key.type.upper(),
                 'flags': key.flags,
                 'tag': key.tag(),
                 'algorithm': key.algorithm,
                 'bits': key.bits,
                 'created': key.created,
                 'pubkey': key.pubkey}
                for key in zone.keys]

    @readonly
    def zone_list_delegation_signers(self, zone_name):
        zone = get_zone(zone_name, profile=False)
        result = []
        for key in zone.keys:
            if key.type == 'ksk':
                for digest_type in (1, 2, 4):
                    result.append({'tag': key.tag(),
                                   'algorithm': key.algorithm,
                                   'digest_type': digest_type,
                                   'digest': key.ds(digest_type)})
        return result

    @readonly
    def name_list_views(self, zone):
        zone = get_rr_zone(zone, None, None, None)
        if zone is not None:
            return [{'name': v.name} for v in zone.views]
        return []

    @readonly
    def zone_get_attrs(self, zone, profile=False):
        zone = get_zone(zone, profile)
        attributes = zone.get_attrs()
        attributes.update(name=zone.display_name,
                          created=zone.created,
                          created_by=zone.created_by,
                          modified=zone.modified,
                          modified_by=zone.modified_by)
        if zone.owner:
            attributes.update(owner=zone.owner.name)
            if zone.owner.department_number:
                attributes.update(department_number=zone.owner.department_number)
        if zone.nsec3_algorithm:
            attributes.update(nsec3_algorithm=zone.nsec3_algorithm,
                              nsec3_iterations=zone.nsec3_iterations,
                              nsec3_salt=zone.nsec3_salt)
        if zone.registrar_account:
            attributes['registrar-account'] = zone.registrar_account.name
        if not zone.profile:
            attributes.update(views=db.session.query(ZoneView.id).filter_by(zone=zone).count())
            groups = 0
            for view in zone.views:
                groups += len(view.groups)
            if groups > 0:
                attributes['zone_groups'] = groups
        return attributes

    @updating
    def zone_set_attrs(self, zone, attributes, profile=False):
        zone = get_zone(zone, profile)
        self.user.can_manage_zone(zone)
        zone.set_attrs(attributes)

    @updating
    def zone_delete_attrs(self, zone, attribute_names, profile=False):
        zone = get_zone(zone, profile)
        self.user.can_manage_zone(zone)
        zone.delete_attrs(attribute_names)

    @readonly
    def zone_get_soa_attrs(self, zone, profile=False, view=None):
        zone = get_zone(zone, profile)
        view = get_view(zone, view)
        result = dict((k, getattr(view, k)) for k in ZoneView.soa_attrs)
        return result

    @readonly
    def zone_view_get_attrs(self, zone, view):
        zone = get_zone(zone, profile=False)
        view = get_view(zone, view)
        attrs = dict(created=view.created,
                     created_by=view.created_by,
                     modified=view.modified,
                     modified_by=view.modified_by,
                     name=zone.display_name,
                     view=view.name)
        if len(view.groups) > 0:
            attrs['zone_groups'] = len(view.groups)
        return attrs

    @updating
    def zone_set_soa_attrs(self, zone, attributes, profile=False, view=None):
        if not attributes:
            return
        if not isinstance(attributes, dict):
            raise Exception("Attributes must be a map")
        attributes = validate_soa_attrs(attributes)
        zone = get_zone(zone, profile)
        self.user.can_manage_zone(zone)
        view = get_view(zone, view)
        view.set_soa_attrs(attributes)

    @updating
    def zone_set_owner(self, zone, owner):
        zone = get_zone(zone, False)
        self.user.can_manage_zone(zone)
        owner = get_group(owner)
        zone.owner = owner

    @updating_delayed_lock
    def zone_dnssec_enable(self, zone_name, algorithm=8, ksk_bits=2048, zsk_bits=1024,
                           nsec3_algorithm=0, nsec3_iterations=0, nsec3_salt='-'):
        zone = get_zone(zone_name, False)
        self.user.can_manage_zone(zone)
        Zone.check_nsec3params(nsec3_algorithm, nsec3_iterations, nsec3_salt)
        defaults = dict(default_algorithm=algorithm,
                        default_ksk_bits=ksk_bits,
                        default_zsk_bits=zsk_bits)
        ksk = self._zone_generate_key(zone_name, 'ksk', defaults)
        zsk = self._zone_generate_key(zone_name, 'zsk', defaults)
        g.rollback_and_get_lock()
        zone = get_zone(zone_name, False)
        zone.set_attrs(dict(default_algorithm=algorithm,
                            default_ksk_bits=ksk_bits,
                            default_zsk_bits=zsk_bits))
        if nsec3_algorithm:
            zone.set_nsec3params(nsec3_algorithm, nsec3_iterations, nsec3_salt)
        self._zone_add_key(zone_name, ksk)
        self._zone_add_key(zone_name, zsk)
        # Warn about unsigned subzones
        subzones = get_subzones(zone)
        unsigned_subzones = [s.name for s in subzones if not s.is_signed()]
        if len(unsigned_subzones) > 0:
            Messages.warn('unsigned child zone(s) exit: ' + ', '.join(unsigned_subzones))
        # Create DS records for signed child zones
        signed_subzones = [s for s in subzones if s.is_signed()]
        for subzone in signed_subzones:
            for key in subzone.keys:
                create_ds_rr(key, parent_zone=zone, user=self.user)
        record_history(zone, action='dnssec enabled')
        return dict(messages=Messages.get())

    @updating_delayed_lock
    def zone_create_key(self, zone_name, key_type):
        self.user.can_manage_zone(get_zone(zone_name, False))
        key_info = self._zone_generate_key(zone_name, key_type)
        g.rollback_and_get_lock()
        self._zone_add_key(zone_name, key_info)
        return dict(messages=Messages.get())

    def _zone_generate_key(self, zone_name, key_type, defaults=None):
        '''Generate a zone key. Returns a dict with key info'''
        if key_type not in ('ksk', 'zsk'):
            raise InvalidParameterError('Invalid key type: %s' % key_type)
        zone = get_zone(zone_name, False)
        if defaults is None:
            attributes = zone.get_attrs()
        else:
            attributes = defaults
        if attributes.get('default_algorithm') is None:
            raise InvalidParameterError('Zone %s is not dnssec enabled' % zone.display_name)
        algorithm = int(attributes.get('default_algorithm'))
        if algorithm is None:
            raise InvalidParameterError('Missing zone attribute default_algorithm')
        bits_name = 'default_' + key_type + '_bits'
        bits = attributes.get(bits_name)
        if bits is None:
            raise InvalidParameterError('Missing zone attribute %s' % bits_name)
        label = '_'.join([zone_name, key_type, datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S%f')])
        pubkey, privkey = generate_RSASHA256_key_pair(int(bits))
        return dict(bits=bits,
                    type=key_type,
                    label=label,
                    algorithm=algorithm,
                    pubkey=pubkey,
                    privkey=privkey)

    def _zone_add_key(self, zone_name, key_info):
        zone = get_zone(zone_name, False)
        if zone.valid_begin is None:
            zone.set_validity()
        key = ZoneKey(zone=zone, **key_info)
        db.session.add(key)
        OutputUpdate.send_dnssec_update(key.zone, "add " + key.label)
        Messages.info("Created key %s for zone %s" % (key.label, key.zone.display_name))
        create_ds_rr(key, user=self.user)
        return key

    @updating
    def zone_delete_key(self, zone_name, key_label):
        zone = get_zone(zone_name, profile=False)
        self.user.can_manage_zone(zone)
        key = ZoneKey.query.filter(ZoneKey.zone_id == zone.id).filter(ZoneKey.label == key_label).first()
        if key is None:
            raise DimError('Key %s does not exist for zone %s' % (key_label, zone_name))
        self._zone_delete_key(key)
        return dict(messages=Messages.get())

    def _zone_delete_key(self, key):
        if key.registrar_action is not None:
            raise DimError('A command that uses the key %s at the registrar is in progress' % key.label)
        delete_ds_rr(key, user=self.user)
        OutputUpdate.send_dnssec_update(key.zone, "delete " + key.label)
        db.session.delete(key)

    @updating
    def zone_dnssec_disable(self, zone_name):
        zone = get_zone(zone_name, profile=False)
        self.user.can_manage_zone(zone)
        signed_subzones = [s for s in get_subzones(zone) if s.is_signed()]
        for subzone in signed_subzones:
            for key in subzone.keys:
                delete_ds_rr(key, parent_zone=zone, user=self.user)
        for key in zone.keys:
            self._zone_delete_key(key)
        if zone.nsec3_algorithm:
            zone.set_nsec3params(0, 0, '-')
        zone.valid_begin = zone.valid_end = None
        record_history(zone, action='dnssec disabled')
        return dict(messages=Messages.get())

    @updating
    def registrar_account_create(self, name, plugin, url, user, password, subaccount):
        self.user.can_dns_admin()
        if plugin != RegistrarAccount.AUTODNS3:
            raise InvalidParameterError('Plugin must be %s' % RegistrarAccount.AUTODNS3)

        ra = RegistrarAccount.query.filter_by(name=name).first()
        if ra is not None:
            raise InvalidParameterError('Registrar-account %s already exists' % name)
        ra = RegistrarAccount(name=name, plugin=plugin, url=url, username=user, password=password,
                              subaccount=subaccount)
        db.session.add(ra)

    @updating
    def registrar_account_delete(self, name):
        self.user.can_dns_admin()
        ra = get_registrar_account(name)
        for zone in ra.zones:
            _check_no_ongoing_actions(zone)
            Messages.warn('Zone %s has no registrar-account' % zone.display_name)
        # TODO delete all operations for RA?
        db.session.delete(ra)
        return {'messages': Messages.get()}

    @readonly
    def registrar_account_get_attrs(self, name):
        ra = get_registrar_account(name)
        attributes = dict(url=ra.url,
                          plugin=ra.plugin,
                          subaccount=ra.subaccount,
                          managed_zones=len(ra.zones))
        # only dns admins can see username & password
        try:
            self.user.can_dns_admin()
            attributes.update(dict(password=ra.password, username=ra.username))
        except:
            pass
        return attributes

    @readonly
    def registrar_account_list(self, include_actions=False):
        registrar_accounts = RegistrarAccount.query.all()
        accounts = []
        for ra in registrar_accounts:
            account = dict(name=ra.name, plugin=ra.plugin, username=ra.username)
            if include_actions:
                account['total_actions'] = sum([len(self.zone_registrar_actions(zone.name)) for zone in ra.zones])
            accounts.append(account)
        return accounts

    @readonly
    def registrar_account_list_zones(self, name, include_status=False):
        ra = get_registrar_account(name)
        zones = []
        for zone in ra.zones:
            z = dict(zone=zone.name)
            if include_status:
                last_action = RegistrarAction.query.filter_by(zone=zone) \
                    .filter(RegistrarAction.status.in_(['done', 'failed'])) \
                    .order_by(RegistrarAction.completed.desc()).first()
                if last_action:
                    z['last_run'] = last_action.completed
                    z['status'] = last_action.status
                    if last_action.status == 'failed':
                        z['error'] = get_pretty_error_message(last_action.error)
            zones.append(z)
        return zones

    @updating
    def registrar_account_add_zone(self, name, zone):
        zone = get_zone(zone, False)
        self.user.can_manage_zone(zone)
        ra = get_registrar_account(name)
        if zone.registrar_account:
            raise DimError('Zone %s is already added to registrar-account %s' % (
                zone.display_name, zone.registrar_account.name))
        zone.registrar_account = ra
        record_history(ra, action='add zone', zone=zone.display_name)

    @updating
    def registrar_account_delete_zone(self, name, zone):
        zone = get_zone(zone, False)
        self.user.can_manage_zone(zone)
        ra = get_registrar_account(name)
        _check_no_ongoing_actions(zone)
        if zone.registrar_account == ra:
            if zone.has_registrar_keys():
                Messages.warn('Zone %s has published keys at registrar %s' % (zone.display_name, name))
            zone.update_registrar_keys([])
            RegistrarAction.query.filter_by(zone=zone).delete(synchronize_session=False)
            zone.registrar_account = None
            record_history(ra, action='remove zone', zone=zone.display_name)
        else:
            raise DimError('Zone %s is not added to registrar-account %s' % (zone.display_name, name))
        return {'messages': Messages.get()}

    @readonly
    def zone_registrar_actions(self, zone):
        zone = get_zone(zone, False)
        zone_keys = _get_ksk_keys(zone)
        dim_set = set([(key.algorithm, key.pubkey) for key in zone_keys])
        registrar_set = set([(key.algorithm, key.pubkey) for key in zone.registrar_keys])
        result = []
        if dim_set == registrar_set:
            return result
        ongoing = _ongoing_actions_query(zone)
        if ongoing:
            result.append(dict(action='set-ds', status='in progress',
                               data=' '.join([key.ds(2) for key in zone_keys if key.registrar_action is not None])))
        if [key for key in zone_keys if key.registrar_action is None] or (not ongoing and registrar_set - dim_set):
            result.append(dict(action='set-ds', status='waiting', data=' '.join([key.ds(2) for key in zone_keys])))
        return result

    @updating
    def registrar_account_update_zone(self, zone):
        zone = get_zone(zone, False)
        self.user.can_manage_zone(zone)
        if zone.registrar_account is None:
            raise InvalidParameterError('Zone %s has not registrar-account associated' % zone.display_name)
        _check_no_ongoing_actions(zone)
        if len(self.zone_registrar_actions(zone.display_name)) == 0:
            raise DimError('Nothing to do for zone %s' % zone.display_name)
        _update_registrar_keys(zone)

    @updating
    def registrar_account_update_zones(self, name):
        self.user.can_dns_admin()
        ra = get_registrar_account(name)
        for zone in ra.zones:
            if _ongoing_actions_query(zone):
                continue
            if len(self.zone_registrar_actions(zone.display_name)) == 0:
                continue
            _update_registrar_keys(zone)

    @updating
    def rr_create(self, zone=None, views=None, ttl=None, comment=None, profile=False, allow_overlap=False, **kwargs):        
        if 'property_value' in kwargs and isinstance(kwargs['property_value'], str) and kwargs['type'] == 'CAA':
            kwargs['property_value'] = kwargs['property_value'].lower()
        self._rr_create(zone=zone, views=views, ttl=ttl, comment=comment, profile=profile, allow_overlap=allow_overlap,
                        **kwargs)
        return {'messages': Messages.get()}

    @updating
    def rr_create_from_pool(self, name, pool, ttl=None, comment=None, full=False, attributes=None, views=None):
        pool = get_pool(pool)
        ipblock = self._ip_mark(allocate_ip(pool), attributes=attributes, pool=pool)
        if ipblock:
            type = 'A'
            if ipblock.version == 6:
                type = 'AAAA'
            result = self.rr_create(name=name, type=type, ttl=ttl, comment=comment, ip=str(ipblock), views=views,
                                    layer3domain=ipblock.layer3domain.name)
            result['attributes'] = self.ipblock_get_attrs(ipblock, full=full)
        else:
            result = dict(attributes=None)
            result['messages'] = Messages.get()
        return result

    @updating
    def rr_delete(self, ids=None, zone=None, views=None, profile=False, free_ips=False, references='error', **kwargs):
        if ids is not None:
            if type(ids) != list:
                raise InvalidParameterError('invalid ids: must be an array')
            if kwargs or zone or views or profile:
                raise InvalidParameterError('the ids option cannot be used with the zone, views or profile options')
            rrs = RR.query.filter(RR.id.in_(ids))
            delete_with_references(rrs, free_ips=free_ips, references=references, user=self.user)
        else:
            if 'type' in kwargs:
                self._check_rr_parameters(kwargs, creating=False)
                self._parse_ip_from_rr_parameters(kwargs, zone=zone, creating=False)
                # Get rid of layer3domain so we're left only with the rr value in kwargs
                # layer3domain was used in _parse_ip_from_rr_parameters to get the ipblock
                if kwargs.get('layer3domain'):
                    del kwargs['layer3domain']
            else:
                if 'name' not in kwargs:
                    raise InvalidParameterError('Missing required parameter: name')
                unknown = set(kwargs.keys()) - set(['name'])
                if unknown:
                    raise InvalidParameterError('Unknown parameters: %s' % ' '.join(unknown))
            zone = get_rr_zone(kwargs['name'], zone, profile)
            if zone is None:
                raise DimError('Zone for %s not found' % kwargs['name'])
            for view in views or [None]:
                view = get_view(zone, view)
                rr_delete(zone=zone, view=view, profile=profile, free_ips=free_ips,
                          references=references, user=self.user, **kwargs)
        return {'messages': Messages.get()}

    @readonly
    def rr_get_attrs(self, view=None, **kwargs):
        rr, zone = self._get_rr_and_zone(view=view, **kwargs)
        result = dict(created=rr.created,
                      created_by=rr.created_by,
                      modified=rr.modified,
                      modified_by=rr.modified_by,
                      zone=zone.display_name,
                      rr=rr.bind_str(True))
        if rr.comment is not None:
            result['comment'] = rr.comment
        if len(zone.views) > 1:
            result['view'] = rr.view.name
        if rr.ttl is not None:
            result['ttl'] = rr.ttl
        return result

    @updating
    def rr_set_attrs(self, view=None, **kwargs):
        if 'ttl' not in kwargs and 'comment' not in kwargs:
            raise InvalidParameterError('At least one of comment or ttl must be present')
        args = {}
        if 'comment' in kwargs:
            args['comment'] = kwargs.pop('comment')
        if 'ttl' in kwargs:
            args['ttl'] = kwargs.pop('ttl')
        rr, zone = self._get_rr_and_zone(view=view, **kwargs)
        self.user.can_delete_rr(rr.view, rr.type)
        if 'comment' in args:
            rr.set_comment(args['comment'])
        if 'ttl' in args:
            rr.set_ttl(args['ttl'])
        if 'comment' in args:
            return self.rr_get_attrs(view=view, **kwargs)

    @updating
    def rr_set_comment(self, comment=None, view=None, **kwargs):
        rr, zone = self._get_rr_and_zone(view=view, **kwargs)
        self.user.can_delete_rr(rr.view, rr.type)
        rr.set_comment(comment)

    @updating
    def rr_set_ttl(self, ttl=None, view=None, **kwargs):
        rr, zone = self._get_rr_and_zone(view=view, **kwargs)
        self.user.can_delete_rr(rr.view, rr.type)
        rr.set_ttl(ttl)

    @readonly
    def rr_list(self, pattern=None, type=None, zone=None, view=None, profile=False, limit=None, offset=0,
                value_as_object=False, fields=False, created_by=None, modified_by=None, layer3domain=None,
                exact=False):
        qfields = [RR.name, Zone.name.label('zone'), ZoneView.name.label('view'), RR.ttl, RR.type, RR.value,
                   Layer3Domain.name.label('layer3domain')]
        if fields:
            qfields.extend(self._zone_view_rights())
            qfields.append(RR.comment)
        rr_query = db.session.query(*qfields)\
            .join(RR.view).join(ZoneView.zone).filter(Zone.profile == profile)\
            .outerjoin(RR.ipblock).outerjoin(Ipblock.layer3domain)
        soa_query = ZoneView.query.join(ZoneView.zone).options(contains_eager(ZoneView.zone)).filter(Zone.profile == profile)
        if zone:
            zone = get_zone(zone, profile)
            view = get_view(zone, view)
            rr_query = rr_query.filter(RR.view == view)
            soa_query = soa_query.filter(ZoneView.id == view.id)
            reverse_zone_sorting = is_reverse_zone(view.zone.name)
        else:
            reverse_zone_sorting = False
        if type:
            rr_query = rr_query.filter(RR.type == type)
            if type != 'SOA':
                soa_query = None
        if layer3domain:
            layer3domain = get_layer3domain(layer3domain)
            rr_query = rr_query.filter(Ipblock.layer3domain == layer3domain)
            soa_query = None
        if created_by:
            rr_query = rr_query.filter(RR.created_by == created_by)
            if soa_query:
                soa_query = soa_query.filter(ZoneView.created_by == created_by)
        if modified_by:
            rr_query = rr_query.filter(RR.modified_by == modified_by)
            if soa_query:
                soa_query = soa_query.filter(ZoneView.modified_by == modified_by)
        ip = None
        if pattern is not None and not exact:
            try:
                ip = IP(pattern)
            except:
                ip = None
        if ip:
            rr_query = rr_query.filter(inside(Ipblock.address, ip))
            soa_query = None
        elif pattern is not None:
            columns = [RR.name, RR.target]
            search_value = pattern
            if len(search_value) > 0 and search_value[-1] not in ['*', '?', '.']:
                search_value += '.'
            if exact:
                rr_query = rr_query.filter(or_(*[col == search_value for col in columns]))
                if soa_query is not None:
                    soa_query = soa_query.filter(or_((Zone.name + '.') == search_value,
                                                     ZoneView.primary == search_value))
            else:
                wildcard = make_wildcard(search_value)
                rr_query = rr_query.filter(or_(*[col.like(wildcard) for col in columns]))
                if soa_query is not None:
                    soa_query = soa_query.filter(or_((Zone.name + '.').like(wildcard),
                                                     ZoneView.primary.like(wildcard)))
        # We can't limit reverse zones records because we can't do the same sorting in the query
        if limit is not None and not reverse_zone_sorting:
            limit = int(limit)
            rr_query = rr_query.order_by(Zone.name,
                                         trim_trailing(Zone.name + '.', RR.name),
                                         ZoneView.name).limit(limit + offset)
            if soa_query is not None:
                soa_query = soa_query.order_by(Zone.name, ZoneView.name).limit(limit + offset)

        rrs = []
        for rr in rr_query.all():
            v = dict(record=RR.record_name(rr.name, rr.zone),
                     zone=Zone.to_display_name(rr.zone),
                     name=rr.name,
                     view=rr.view,
                     ttl=rr.ttl,
                     type=rr.type,
                     value=rr.value)
            if rr.layer3domain:
                v['layer3domain'] = rr.layer3domain
            if fields:
                can_create_rr = rr.can_create_rr
                can_delete_rr = rr.can_delete_rr
                if rr.type == 'PTR' and is_reverse_zone(v['zone']):
                    can_create_rr = can_delete_rr = True
                v.update(dict(can_create_rr=can_create_rr,
                              can_delete_rr=can_delete_rr))
                if rr.comment is not None:
                    v['comment'] = rr.comment
            rrs.append(v)

        def soa_value(view):
            if value_as_object:
                attrs = ['primary', 'mail', 'serial', 'refresh', 'retry', 'expire', 'minimum']
                return dict([(attr, getattr(view, attr)) for attr in attrs])
            return view.soa_value()
        if soa_query is not None:
            for view in soa_query.all():
                rr = dict(record='@',
                          zone=view.zone.display_name,
                          name=view.zone.name + '.',
                          view=view.name,
                          ttl=view.ttl,
                          type='SOA',
                          value=soa_value(view))
                if fields:
                    rr.update(dict(can_create_rr=False,
                                   can_delete_rr=False))
                rrs.append(rr)

        if reverse_zone_sorting:
            result = [x for x in rrs if x['type'] == 'SOA']
            result += [x for x in rrs if x['record'].startswith('_')]
            result += [x for x in rrs if (x['record'].startswith('@') and x['type'] != 'SOA')]
            result += [x for x in rrs if x['record'].startswith('*')]

            def split_by_ip(dict_list):
                """
                This function will check if the zone name value from each dictionary has a
                valid IP, in order to sort these zones by their IP address
                :param dict_list: List of dictionaries
                :return: 2 lists of dictionaries. The first with valid IPs , the second with invalid.
                """
                valid_ip = []
                invalid_ip = []
                for d in dict_list:
                    try:
                        IP(dim.dns.get_ip_from_ptr_name(d['name'], strict=False))
                        valid_ip.append(d)
                    except:
                        invalid_ip.append(d)
                return valid_ip, invalid_ip

            remaining_rrs = [x for x in rrs if (not x['record'].startswith(('_', '@', '*')) and x['type'] != 'SOA')]
            valid_ips, invalid_ips = split_by_ip(remaining_rrs)
            result += sorted(valid_ips, key=lambda x: (IP(dim.dns.get_ip_from_ptr_name(x['name'], strict=False)).address, x['record']))
            result += sorted(invalid_ips)
        else:
            result = sorted(rrs, key=lambda x: (x['zone'], x['record'] != '@', x['record'], x['view'], x['type'] != 'SOA'))

        if limit is None:
            limit = len(result)
        result = result[offset:offset+limit]
        for row in result:
            del row['name']
        if value_as_object:
            for rr in result:
                if rr['type'] != 'SOA':
                    rr['value'] = RR.get_class(rr['type']).fields_from_value(rr['value'])
        return result

    @readonly
    def rr_list2(self, pattern=None, type=None, zone=None, view=None, profile=False, limit=None, offset=0,
                value_as_object=False, fields=False, created_by=None, modified_by=None, layer3domain=None,
                type_sort='asc', sort_by='record', order='asc', exact=False):
        qfields = [RR.name, Zone.name.label('zone'), ZoneView.name.label('view'), RR.ttl, RR.type, RR.value,
                   Layer3Domain.name.label('layer3domain')]
        if fields:
            qfields.extend(self._zone_view_rights())
            qfields.append(RR.comment)
        rr_query = db.session.query(*qfields) \
            .join(RR.view).join(ZoneView.zone).filter(Zone.profile == profile) \
            .outerjoin(RR.ipblock).outerjoin(Ipblock.layer3domain)
        soa_query = ZoneView.query.join(ZoneView.zone).options(contains_eager(ZoneView.zone)).filter(Zone.profile == profile)
        if zone:
            zone = get_zone(zone, profile)
            view = get_view(zone, view)
            rr_query = rr_query.filter(RR.view == view)
            soa_query = soa_query.filter(ZoneView.id == view.id)
            reverse_zone_sorting = is_reverse_zone(view.zone.name)
        else:
            reverse_zone_sorting = False
        if type:
            rr_query = rr_query.filter(RR.type == type)
            if type != 'SOA':
                soa_query = None
        if layer3domain:
            layer3domain = get_layer3domain(layer3domain)
            rr_query = rr_query.filter(Ipblock.layer3domain == layer3domain)
            soa_query = None
        if created_by:
            rr_query = rr_query.filter(RR.created_by == created_by)
            if soa_query:
                soa_query = soa_query.filter(ZoneView.created_by == created_by)
        if modified_by:
            rr_query = rr_query.filter(RR.modified_by == modified_by)
            if soa_query:
                soa_query = soa_query.filter(ZoneView.modified_by == modified_by)
        ip = None
        if pattern is not None and not exact:
            try:
                ip = IP(pattern)
            except:
                ip = None
        if ip:
            rr_query = rr_query.filter(inside(Ipblock.address, ip))
            soa_query = None
        elif pattern is not None:
            columns = [RR.name, RR.target]
            search_value = pattern
            if len(search_value) > 0 and search_value[-1] not in ['*', '?', '.']:
                search_value += '.'
            if exact:
                rr_query = rr_query.filter(or_(*[col == search_value for col in columns]))
                if soa_query is not None:
                    soa_query = soa_query.filter(or_((Zone.name + '.') == search_value,
                                                     ZoneView.primary == search_value))
            else:
                wildcard = make_wildcard(search_value)
                rr_query = rr_query.filter(or_(*[col.like(wildcard) for col in columns]))
                if soa_query is not None:
                    soa_query = soa_query.filter(or_((Zone.name + '.').like(wildcard),
                                                     ZoneView.primary.like(wildcard)))

        rr_count = fast_count(rr_query)
        soa_count = 0
        if soa_query:
            soa_count = fast_count(soa_query)
        count = rr_count + soa_count

        order_columns = {
            'record': RROrder(lambda x: (x['zone'], x['record'] != '@', x['record'], x['view'], x['type'] != 'SOA'),
                              [Zone.name, trim_trailing(Zone.name + '.', RR.name), ZoneView.name],
                              [Zone.name, ZoneView.name]),
            'ttl': RROrder(lambda x: x['ttl'], [RR.ttl], [ZoneView.ttl]),
            'value': RROrder(lambda x: x.get('value'), [RR.value], [ZoneView.primary]),
        }

        if sort_by not in order_columns:
            raise InvalidParameterError('Invalid sort by')

        # We can't limit reverse zones records because we can't do the same sorting in the query
        if not reverse_zone_sorting:
            if order == 'desc':
                rr_query = rr_query.order_by(*[desc(column) for column in order_columns[sort_by].rr_order_by])
            else:
                rr_query = rr_query.order_by(*order_columns[sort_by].rr_order_by)

            if type_sort == 'desc':
                rr_query = rr_query.order_by(desc(RR.type))
            else:
                rr_query = rr_query.order_by(RR.type)

            if soa_query is not None:
                if order == 'desc':
                    soa_query.order_by(*[desc(column) for column in order_columns[sort_by].soa_order_by])
                else:
                    soa_query.order_by(*order_columns[sort_by].soa_order_by)

                if offset < soa_count and offset < soa_count + limit:
                    soa_query = soa_query.slice(offset, soa_count)
                    rr_query = rr_query.limit(offset + limit - soa_count)
                elif soa_count < offset or not soa_count:
                    soa_query = None
                    rr_query = rr_query.slice(offset - soa_count, offset - soa_count + limit)
                else:
                    soa_query = soa_query.slice(offset, offset + limit)
                    rr_query = None
            else:
                rr_query = rr_query.slice(offset, offset + limit)

        rrs = []

        def soa_value(view):
            if value_as_object:
                attrs = ['primary', 'mail', 'serial', 'refresh', 'retry', 'expire', 'minimum']
                return dict([(attr, getattr(view, attr)) for attr in attrs])
            return view.soa_value()
        if soa_query is not None:
            for view in soa_query.all():
                rr = dict(record='@',
                          zone=view.zone.display_name,
                          name=view.zone.name + '.',
                          view=view.name,
                          ttl=view.ttl,
                          type='SOA',
                          value=soa_value(view))
                if fields:
                    rr.update(dict(can_create_rr=False,
                                   can_delete_rr=False))
                rrs.append(rr)

        if rr_query is not None:
            for rr in rr_query.all():
                v = dict(record=RR.record_name(rr.name, rr.zone),
                         zone=Zone.to_display_name(rr.zone),
                         name=rr.name,
                         view=rr.view,
                         ttl=rr.ttl,
                         type=rr.type,
                         value=rr.value)
                if rr.layer3domain:
                    v['layer3domain'] = rr.layer3domain
                if fields:
                    can_create_rr = rr.can_create_rr
                    can_delete_rr = rr.can_delete_rr
                    if rr.type == 'PTR' and is_reverse_zone(v['zone']):
                        can_create_rr = can_delete_rr = True
                    v.update(dict(can_create_rr=can_create_rr,
                                  can_delete_rr=can_delete_rr))
                    if rr.comment is not None:
                        v['comment'] = rr.comment
                rrs.append(v)

        if reverse_zone_sorting:
            result = [x for x in rrs if x['type'] == 'SOA']
            result += [x for x in rrs if x['record'].startswith('_')]
            result += [x for x in rrs if (x['record'].startswith('@') and x['type'] != 'SOA')]
            result += [x for x in rrs if x['record'].startswith('*')]

            def split_by_ip(dict_list):
                """
                This function will check if the zone name value from each dictionary has a
                valid IP, in order to sort these zones by their IP address
                :param dict_list: List of dictionaries
                :return: 2 lists of dictionaries. The first with valid IPs , the second with invalid.
                """
                valid_ip = []
                invalid_ip = []
                for d in dict_list:
                    try:
                        IP(dim.dns.get_ip_from_ptr_name(d['name'], strict=False))
                        valid_ip.append(d)
                    except:
                        invalid_ip.append(d)
                return valid_ip, invalid_ip

            remaining_rrs = [x for x in rrs if (not x['record'].startswith(('_', '@', '*')) and x['type'] != 'SOA')]
            valid_ips, invalid_ips = split_by_ip(remaining_rrs)
            result += sorted(valid_ips, key=lambda x: (IP(dim.dns.get_ip_from_ptr_name(x['name'], strict=False)).address, x['record']))
            result += sorted(invalid_ips)
            result = result[offset:offset+limit]
        else:
            result = rrs

        for row in result:
            del row['name']
        if value_as_object:
            for rr in result:
                if rr['type'] != 'SOA':
                    rr['value'] = RR.get_class(rr['type']).fields_from_value(rr['value'])
        return {'count': count, 'data': result}

    @readonly
    def rr_get_references(self, view=None, delete=True, **kwargs):
        '''
        Return an oriented graph of references to the rr **kwargs.
        The graph is specified by a set of rr nodes and a map (node, children).
        '''
        root, zone = self._get_rr_and_zone(view=view, **kwargs)

        def rr_object(rr):
            return dict(name=rr.name,
                        zone=rr.view.zone.name,
                        view=rr.view.name,
                        id=rr.id,
                        type=rr.type,
                        value=rr.value)
        records = {}
        graph = {}
        nodes = [root]
        while len(nodes):
            rr = nodes.pop()
            if rr in records:
                continue
            records[rr.id] = rr
            graph[rr.id] = []
            IP_RELATED = ['PTR', 'A', 'AAAA']
            for ref in orphaned_references(rr):
                if not (ref.type == root.type and root.type in IP_RELATED) and\
                        not (not delete and ref.type not in IP_RELATED and rr.type not in IP_RELATED and root.type in IP_RELATED):
                    nodes.append(ref)
                    graph[rr.id].append(ref.id)
        return {'records': sorted([rr_object(records[r]) for r in records], key=lambda x: x['id']), 'graph': graph, 'root': root.id}

    @updating
    def rr_edit(self, id, views=None, references=None, **kwargs):
        id = int(id)
        rr = RR.query.filter(RR.id == id).first()
        if rr is None:
            raise InvalidParameterError('Invalid rr id %d.' % id)
        rr_fields = RR.get_class(rr.type).fields
        extra_parameters = set(kwargs.keys()) - set(rr_fields) - set(['name', 'ttl', 'comment'])
        if rr.type == 'PTR':
            extra_parameters = extra_parameters - set(['ip'])
        if extra_parameters:
            raise InvalidParameterError('Unknown parameters: %s' % ' '.join(extra_parameters))
        if 'ttl' in kwargs:
            rr.set_ttl(kwargs.pop('ttl'))
        if 'comment' in kwargs:
            rr.set_comment(kwargs.pop('comment'))
        if 'name' in kwargs and kwargs['name'] == rr.name:
            kwargs.pop('name')
        value_fields = RR.get_class(rr.type).fields_from_value(rr.value)
        for field in rr_fields:
            if field in kwargs and value_fields[field] == kwargs[field]:
                kwargs.pop(field)
        return self._rr_edit(rr, views, references, user=self.user, **kwargs)

    @readonly
    def rr_get_zone(self, name):
        '''
        Return the name of the zone where a rr with name *name* would be placed.

        Name must be an absolute rr name.
        '''
        zone = get_rr_zone(name, zone=None, profile=False, notfound_message=True)
        if zone is not None:
            zone = zone.name
        return zone

    @updating
    def zone_group_create(self, group, comment=None):
        self.user.can_dns_admin()
        if ZoneGroup.query.filter_by(name=group).count():
            raise AlreadyExistsError("A zone-group named '%s' already exists" % group)
        db.session.add(ZoneGroup(name=group, comment=comment))

    @updating
    def zone_group_delete(self, group):
        self.user.can_dns_admin()
        group = get_zone_group(group)
        if len(group.outputs) > 0:
            raise InvalidParameterError('Zone-group %s is connected to at least one output.' % group.name)
        self._delete_rights_for('ZoneGroup', group.id)
        db.session.delete(group)

    @updating
    def zone_group_rename(self, group, new_name):
        self.user.can_dns_admin()
        group = get_zone_group(group)
        group.name = new_name
        group.update_modified()

    @updating
    def zone_group_add_zone(self, group, zone, view=None):
        zone = get_zone(zone, False)
        self.user.can_manage_zone(zone)
        group = get_zone_group(group)
        view_already = ZoneView.query.join(ZoneGroup.views).filter(ZoneGroup.id == group.id).filter(ZoneView.zone_id == zone.id).first()
        if view_already is not None:
            raise InvalidParameterError('%s view %s already in zone-group %s' % (zone.display_name, view_already.name, group.name))
        view = get_view(zone, view)
        zone_group_add_zone(group, zone, view)

    @updating
    def zone_group_remove_zone(self, group, zone):
        zone = get_zone(zone, False)
        self.user.can_manage_zone(zone)
        group = get_zone_group(group)
        _check_no_ongoing_actions(zone)
        view = ZoneView.query.join(ZoneGroup.views).filter(ZoneGroup.id == group.id).filter(ZoneView.zone_id == zone.id).first()
        if view is not None:
            group.views.remove(view)
            group.update_modified()
            for output in group.outputs:
                dim.dns.check_view_removal_from_output(view, output)

    @updating
    def zone_group_set_comment(self, group, comment):
        self.user.can_dns_admin()
        group = get_zone_group(group)
        group.comment = comment
        group.update_modified()

    @readonly
    def zone_group_get_attrs(self, group):
        group = get_zone_group(group)
        result = dict(created=group.created,
                      created_by=group.created_by,
                      modified=group.modified,
                      modified_by=group.modified_by)
        if group.comment is not None:
            result['comment'] = group.comment
        return result

    @readonly
    def zone_group_get_views(self, group):
        group = get_zone_group(group)
        return [dict(zone=view.zone.display_name, view=view.name) for view in group.views]

    @readonly
    def zone_group_list(self):
        return [dict(name=name, comment=comment) for name, comment in db.session.query(ZoneGroup.name, ZoneGroup.comment).all()]

    @readonly
    def zone_group_list_outputs(self, group):
        group = get_zone_group(group)
        return [dict(name=output.name, plugin=output.plugin, comment=output.comment) for output in group.outputs]

    @readonly
    def output_list(self, include_status=False):
        result = []
        if include_status:
            outputs = db.session.query(Output, func.count(OutputUpdate.id).label('pending_records'))\
                .outerjoin(Output.updates).group_by(*Output.__table__.columns)
        else:
            outputs = db.session.query(Output, literal('0'))
        outputs = outputs.order_by(Output.name)
        for output, pending_records in outputs:
            attrs = {'name': output.name,
                     'plugin': output.plugin}
            if include_status:
                attrs['last_run'] = output.last_run if output.last_run.year > 1970 else None
                attrs['status'] = output.status
                attrs['pending_records'] = pending_records
            result.append(attrs)
        return result

    @updating
    def output_create(self, name, plugin, **options):
        self.user.can_dns_admin()
        if Output.query.filter_by(name=name).count():
            raise AlreadyExistsError("An output named '%s' already exists" % name)
        if plugin not in Output.PLUGINS:
            raise InvalidParameterError('Plugin must be one of: %s' % ' '.join(list(Output.PLUGINS.keys())))
        for attr_name in Output.PLUGINS[plugin]:
            if options.get(attr_name) is None:
                raise InvalidParameterError('Plugin %s requires a %s' % (plugin, attr_name))
        output = Output(name=name, plugin=plugin, **options)
        db.session.add(output)

    @updating
    def output_set_comment(self, output, comment):
        self.user.can_dns_admin()
        output = get_output(output)
        output.comment = comment
        output.update_modified()

    @updating
    def output_add_group(self, output, group):
        self.user.can_dns_admin()
        output = get_output(output)
        group = get_zone_group(group)

        for view in group.views:
            view_conflicting = dim.dns.check_view_addition_to_output(view, output)
            if view_conflicting:
                raise InvalidParameterError('zone %s view %s already defined for output %s.'
                                            % (view_conflicting.zone.display_name, view_conflicting.name, output.name))
        output.groups.append(group)

    @updating
    def output_remove_group(self, output, group):
        self.user.can_dns_admin()
        output = get_output(output)
        group = get_zone_group(group)
        if group in output.groups:
            for zone in [view.zone for view in group.views]:
                _check_no_ongoing_actions(zone)
            output.groups.remove(group)
            for view in group.views:
                dim.dns.check_view_removal_from_output(view, output)

    @readonly
    def output_get_attrs(self, output):
        self.user.can_dns_admin()
        output = get_output(output)
        attributes = dict(name=output.name,
                          type=output.plugin,
                          created=output.created,
                          created_by=output.created_by,
                          modified=output.modified,
                          modified_by=output.modified_by,
                          comment=output.comment)
        for attr_name in Output.PLUGINS[output.plugin]:
                attributes[attr_name] = getattr(output, attr_name)
        if output.comment is not None:
            attributes['comment'] = output.comment
        if output.last_run.year > 1970:
            attributes['last_run'] = output.last_run
            attributes['status'] = output.status
            attributes['pending_records'] = db.session.query(func.count(OutputUpdate.id)).filter(OutputUpdate.output == output).scalar()
        return attributes

    @readonly
    def output_get_groups(self, output):
        output = get_output(output)
        return [{'zone_group': group.name, 'comment': group.comment} for group in output.groups]

    @updating
    def output_delete(self, output):
        self.user.can_dns_admin()
        output = get_output(output)
        db.session.delete(output)

    @updating
    def output_rename(self, output, new_name):
        self.user.can_dns_admin()
        output = get_output(output)
        output.name = new_name
        output.update_modified()

    @readonly
    def output_update_list(self, output=None):
        query = db.session.query(
            OutputUpdate.id,
            Output.name.label('output'),
            OutputUpdate.action,
            OutputUpdate.zone_name,
            OutputUpdate.serial,
            OutputUpdate.name,
            OutputUpdate.ttl,
            OutputUpdate.type,
            OutputUpdate.content).join(Output).order_by(OutputUpdate.id)
        if output:
            query = query.filter(Output.name == output)
        return [dict(list(zip(list(row.keys()), row))) for row in query]

    @updating
    def output_update_delete(self, update_ids):
        self.user.can_dns_update_agent()
        if update_ids:
            OutputUpdate.query.filter(OutputUpdate.id.in_(update_ids)).delete(synchronize_session=False)

    @readonly
    def history_zones(self, profile, limit=None, begin=None, end=None):
        hs = HistorySelect()
        hs.add_select(Zone, 'zone-profile' if profile else None) \
            .where(hs.c.profile == profile).where(or_(hs.c.action == 'created',
                                                      hs.c.action == 'deleted',
                                                      hs.c.action == 'renamed'))
        return hs.execute(limit, begin, end)

    @readonly
    def history_zone(self, zone, profile, limit=None, begin=None, end=None):
        hs = HistorySelect()
        hs.add_select(Zone, 'zone-profile' if profile else None).where(hs.c.name == zone).where(hs.c.profile == profile)
        hs.add_select(RR).where(hs.c.zone == zone)
        hs.add_select(ZoneView).where(hs.c.zone == zone)
        hs.add_select(RegistrarAccount).where(hs.c.zone == zone)
        hs.add_select(ZoneKey).where(hs.c.zone == zone)
        if not profile:
            hs.add_select(ZoneGroup).where(hs.c.zone == zone)
        hs.add_select(GroupRight).where(or_(hs.c.object == zone,
                                            hs.c.object.like('zone ' + zone + ' %')))
        return hs.execute(limit, begin, end, incl=([] if profile else ['view', 'layer3domain']))

    @readonly
    def history_zone_views(self, zone, limit=None, begin=None, end=None):
        hs = HistorySelect()
        hs.add_select(ZoneView).where(hs.c.zone == zone)
        return hs.execute(limit, begin, end)

    @readonly
    def history_zone_view(self, zone, view, limit=None, begin=None, end=None):
        hs = HistorySelect()
        hs.add_select(ZoneView).where(hs.c.zone == zone).where(hs.c.name == view)
        hs.add_select(RR).where(hs.c.zone == zone).where(hs.c.view == view)
        return hs.execute(limit, begin, end, incl=['layer3domain'])

    @readonly
    def history_rr(self, name, limit=None, begin=None, end=None):
        hs = HistorySelect()
        query = hs.add_select(RR)
        if name is not None:
            query.where(hs.c.name == name)
        return hs.execute(limit, begin, end, incl=['zone', 'view', 'layer3domain'])

    @readonly
    def history_zone_group(self, zone_group, limit=None, begin=None, end=None):
        hs = HistorySelect()
        query = hs.add_select(ZoneGroup)
        if zone_group is not None:
            query.where(hs.c.name == zone_group)
        return hs.execute(limit, begin, end)

    @readonly
    def history_output(self, output, limit=None, begin=None, end=None):
        hs = HistorySelect()
        query = hs.add_select(Output)
        if output is not None:
            query.where(hs.c.name == output)
        return hs.execute(limit, begin, end)

    @readonly
    def history_group(self, group, limit=None, begin=None, end=None):
        hs = HistorySelect()
        query = hs.add_select(Group)
        if group is not None:
            query.where(hs.c.name == group)
        query = hs.add_select(GroupRight)
        if group is not None:
            query.where(hs.c.group == group)
        return hs.execute(limit=limit, begin=begin, end=end)

    @readonly
    def history_ippool(self, name, limit=None, begin=None, end=None):
        hs = HistorySelect()
        hs.add_select(Pool).where(hs.c.name == name)
        return hs.execute(limit, begin, end, incl=['layer3domain'])

    @readonly
    def history_ipblock(self, ipblock, layer3domain=None, limit=None, begin=None, end=None):
        ip = parse_ip(ipblock)
        hs = HistorySelect()
        query = hs.add_select(Ipblock)
        query = query.where(hs.c.address == ip.address) \
            .where(hs.c.prefix == ip.prefix) \
            .where(hs.c.version == ip.version)
        if layer3domain is not None and layer3domain != 'all':
            layer3domain = _get_layer3domain_arg(layer3domain)
            query.where(hs.c.layer3domain == layer3domain.name)
        return hs.execute(limit, begin, end, incl=['layer3domain'])

    @readonly
    def history_registrar_account(self, name, limit=None, begin=None, end=None):
        hs = HistorySelect()
        hs.add_select(RegistrarAccount).where(hs.c.name == name)
        return hs.execute(limit, begin, end)

    @readonly
    def history_layer3domain(self, name, limit=None, begin=None, end=None):
        hs = HistorySelect()
        query = hs.add_select(Layer3Domain)
        if name is not None and name != 'all':
            query.where(hs.c.name == name)
        return hs.execute(limit, begin, end)

    @readonly
    def history(self, user, limit=None, begin=None, end=None):
        def filter_by_user(query):
            if user is not None:
                query.where(hs.c.user == user)

        hs = HistorySelect()
        filter_by_user(hs.add_select(Zone).where(hs.c.profile == False))  # noqa
        filter_by_user(hs.add_select(Zone, 'zone-profile').where(hs.c.profile == True))  # noqa
        for klass in [Pool, Ipblock, ZoneView, RR, ZoneGroup, Output, Group, GroupRight, RegistrarAccount, ZoneKey]:
            filter_by_user(hs.add_select(klass))
        return hs.execute(limit, begin, end, incl=['zone', 'view', 'layer3domain'])

    def _check_allocate(alternative):
        '''
        Returns a function that:
        - checks if user can allocate from pool, else checks alternative
        - returns the pool

        '''
        def _real_check(self, ip_or_block, pool=None, layer3domain=None):
            allocate_sufficient = True
            if pool is None:
                if layer3domain is None:
                    if hasattr(ip_or_block, 'layer3domain'):
                        layer3domain = ip_or_block.layer3domain
                    else:
                        layer3domain = _get_layer3domain_arg(None)
                allocate_sufficient = False
                subnet = get_subnet(ip_or_block, layer3domain)
                ip = getattr(ip_or_block, 'ip', ip_or_block)
                if subnet:
                    pool = subnet.pool
                if pool and subnet.ip != ip:
                    allocate_sufficient = True
            if pool and allocate_sufficient:
                self.user.can_allocate(pool)
            elif alternative:
                getattr(self.user, alternative)()
            return pool
        return _real_check
    _can_change_ip = _check_allocate('can_network_admin')
    _can_change_ip_attributes = _check_allocate('can_modify_container_attributes')
    _can_change_ip_rr = _check_allocate('')

    def _set_gateway(self, subnet, gateway_str):
        if gateway_str is None:
            if subnet.gateway is not None:
                subnet.gateway = None
                subnet.update_modified()
        else:
            gateway = parse_ip(gateway_str)
            if not gateway.is_host:
                raise InvalidIPError("Gateway must be a host address")
            if subnet.version != gateway.version:
                raise InvalidIPError("Gateway IP version must match subnet's IP version")
            subnet.gateway = gateway.address
            subnet.update_modified()

    def _add_block(self, pool, block):
        if pool.version:
            if pool.version != block.version:
                raise InvalidIPError("An IPv%d block cannot be added to an IPv%d pool" % (block.version, pool.version))
        else:
            pool.version = block.version
        max_priority = pool.subnets[-1].priority if pool.subnets else 0
        block.pool = pool
        block.priority = max_priority + 1
        record_history(block, action='set_attr', attrname='pool', newvalue=pool.name)

    def _ip_mark(self, ip, attributes, pool=None, layer3domain=None, parse_rr=False, allow_overlap=False):
        if ip is None:
            return None
        # Bizarre hack to let anyone create A rrs for ips not managed by pools
        if parse_rr:
            pool = self._can_change_ip_rr(ip, pool, layer3domain=layer3domain)
        else:
            pool = self._can_change_ip(ip, pool, layer3domain=layer3domain)
        ipblock = Ipblock.query_ip(ip, layer3domain).first()
        if ipblock:
            raise AlreadyExistsError("%s from layer3domain %s is already allocated with status %s" % (
            ipblock, ipblock.layer3domain.name, ipblock.status.name))
        else:
            args = dict(address=ip.address,
                        prefix=ip.prefix,
                        version=ip.version,
                        layer3domain=pool.layer3domain if pool else layer3domain,
                        status=get_status('Static'))
            if pool:
                ipblock = Ipblock.create(**args)
            else:
                ipblock = _ipblock_create(allow_overlap=allow_overlap, **args)
            Messages.info('Marked IP %s from layer3domain %s as static' % (ip, ipblock.layer3domain.name))
            if pool is not None:
                record_history(pool, action='create static', **_cidr(ipblock))
        ipblock.set_attrs(attributes)
        return ipblock

    def _delegation_mark(self, blocks, layer3domain, attributes, full):
        result = []
        for addr in blocks:
            block = Ipblock.create(address=addr.address,
                                   prefix=addr.prefix,
                                   version=addr.version,
                                   layer3domain=layer3domain,
                                   status=get_status('Delegation'))
            block.set_attrs(attributes)
            result.append(self.ipblock_get_attrs(block, full=full))
        return result

    def _create_reverse_zones(self, block):
        profile = dim.dns.reverse_zone_profile(block.ip, block.layer3domain)
        for name in subnet_reverse_zones(block):
            zone = Zone.query.filter_by(name=name).first()
            if zone:
                if ZoneView.query.filter_by(zone=zone, name=block.layer3domain.name).count() == 0:
                    self.zone_create_view(zone.name, view=block.layer3domain.name, from_profile=profile)
                    Messages.info('Creating view %s in zone %s %s' % (
                        block.layer3domain.name, zone.name,
                        'with profile ' + profile if profile else 'without profile'))
            else:
                self.zone_create(name, from_profile=profile, view_name=block.layer3domain.name)

    def _delete_reverse_zones(self, block, force):
        if block.version == 4 and block.prefix > 24:
            # Check that the subnet is the last for the reverse zone
            rev_ip = IP(int(block.address) & ~255, prefix=24, version=4)
            q = (db.session.query(Ipblock.id)
                 .join(IpblockStatus).filter(IpblockStatus.name == 'Subnet')
                 .filter(Ipblock.version == 4)
                 .filter(Ipblock.layer3domain_id == block.layer3domain_id)
                 .filter(Ipblock.prefix < 32)
                 .filter(inside(Ipblock.address, rev_ip)))
            if q.count() > 0:
                return
        for name in subnet_reverse_zones(block):
            if Zone.query.filter_by(name=name).count():
                zone = get_zone(name, profile=False)
                view = ZoneView.query.filter_by(zone=zone, name=block.layer3domain.name).first()
                if view is None:
                    continue
                # Delete all @ NS
                ns_rrs = RR.query.filter_by(type='NS', view=view, name=name + '.')
                delete_with_references(ns_rrs, free_ips=False, references='ignore', user=self.user)
                # If force, ignore failure to delete the zone
                deleted = True
                delete_zone = len(zone.views) == 1

                def delete_zone_or_view():
                    if delete_zone:
                        self.zone_delete(name)
                        Messages.info('Deleting zone %s' % name)
                    else:
                        self.zone_delete_view(name, view.name)
                        Messages.info('Deleting view %s from zone %s' % (view.name, name))
                if force:
                    try:
                        delete_zone_or_view()
                    except DimError as e:
                        deleted = False
                        if delete_zone:
                            Messages.warn('Zone %s was not deleted: %s' % (name, str(e)))
                        else:
                            Messages.warn('Zone %s view %s was not deleted: %s' % (name, view.name, str(e)))
                else:
                    delete_zone_or_view()

    def _check_zone_delete(self, zone, profile=False):
        if profile:
            self.user.can_dns_admin()
        zone = get_zone(zone, profile)
        if is_reverse_zone(zone.display_name):
            self.user.can_delete_reverse_zones()
        elif not profile:
            self.user.can_manage_zone(zone)
        if len(zone.views) > 1:
            raise DimError("The zone %s has more than one view and cannot be deleted." % zone.display_name)
        _check_no_ongoing_actions(zone)
        return zone

    def _check_zone_delete_view(self, zone, view):
        zone = get_zone(zone, profile=False)
        self.user.can_manage_zone(zone)
        view = get_view(zone, view)
        if ZoneView.query.filter_by(zone=zone).count() == 1:
            raise DimError('The zone %s has only one view.' % zone.name)
        return view

    def _zone_delete_view(self, view, cleanup, also_deleting_zone):
        view_query = RR.query.filter_by(view=view).order_by(RR.name)
        view_rrs = view_query.all()
        if cleanup:
            delete_with_references(view_query, free_ips=True, references='warn', user=self.user)
        else:
            if view_rrs:
                if also_deleting_zone:
                    raise DimError('Zone not empty.')
                else:
                    raise DimError('The view %s of the zone %s is not empty.' % (view.name, view.zone.display_name))
        # Delete NS rrs from the view with the same name in the parent zone
        parent_view = get_parent_zone_view(view.zone.name, view.name)
        if parent_view:
            dim.dns.delete_ns_rrs_from_parent(view, parent_view, self.user)
        else:
            parent_zone = get_parent_zone(view.zone.name)
            if parent_zone:
                Messages.warn('Parent zone %s has no view named %s, cannot clean up NS Records' % (
                    parent_zone.name, view.name))
        outputs = view.outputs.all()
        self._delete_rights_for('ZoneView', view.id)
        db.session.delete(view)
        if not also_deleting_zone:
            view.zone.update_modified()
        for output in outputs:
            dim.dns.check_view_removal_from_output(view, output)

    def _delete_rights_for(self, object_class, object_id):
        '''
        Delete group rights on the object defined by (object_class, object_id).

        This should be called at each object deletion if there could be access rights on it
        (object_class can currently be only 'ZoneView' and 'Ippool'). This is needed because of the
        denormalized way the rights are stored (inherited from netdot).

        Failure to call this at deleting such objects will result in zombie rights which will deny
        legal operations in some cases.
        '''
        ars = AccessRight.query.filter_by(object_class=object_class, object_id=object_id).all()
        if ars:
            ids = [ar.id for ar in ars]
            for gr in GroupRight.query.filter(GroupRight.accessright_id.in_(ids)):
                db.session.delete(gr)
            for ar in ars:
                db.session.delete(ar)

    def _rr_create_single_pair(self, type, profile=False, zone=None, create_linked=True, create_revzone=False, **kwargs):
        view = kwargs.get('view', None)

        def create_forward():
            forward_zone = get_rr_zone(forward_name, zone if type != 'PTR' else None, profile)
            if forward_zone is not None and forward_zone.name == '' and type == 'PTR':
                raise InvalidParameterError('Cannot create PTR records in root zone.')
            if forward_zone:
                rr_type = 'A' if kwargs['ip'].version == 4 else 'AAAA'
                self.user.can_create_rr(get_view(forward_zone, view), rr_type)
                dim.dns.create_single_rr(name=forward_name,
                                         zone=forward_zone,
                                         view=view,
                                         rr_type=rr_type,
                                         overwrite=overwrite_a,
                                         user=self.user,
                                         **kwargs)
            return forward_zone

        def create_reverse():
            created_ptr = False
            if type == 'PTR':
                reverse_name = kwargs.pop('name')
            else:
                reverse_name = dim.dns.get_ptr_name(kwargs['ip'])
            reverse_zone = get_rr_zone(reverse_name, zone if type == 'PTR' else None, profile, notfound_message=not create_revzone)
            if reverse_zone is not None and reverse_zone.name == '':
                return None, False
            if create_revzone and reverse_zone is None:
                ip = IP(dim.dns.get_ip_from_ptr_name(reverse_name))
                self.zone_create(dim.dns.guess_revzone(ip),
                                 view_name=kwargs['ip'].layer3domain.name,
                                 from_profile=dim.dns.reverse_zone_profile(ip, kwargs['ip'].layer3domain))
                reverse_zone = get_rr_zone(reverse_name, None, profile)
            if reverse_zone:
                view_name = kwargs['ip'].layer3domain.name
                view_exists = ZoneView.query.filter_by(zone=reverse_zone, name=view_name).count()
                forward = make_fqdn(forward_name, forward_zone)
                print((view_name, view_exists, zone))
                if view_exists:
                    dim.dns.create_single_rr(name=reverse_name,
                                             zone=reverse_zone,
                                             view=view_name,
                                             ptrdname=forward,
                                             rr_type='PTR',
                                             overwrite=overwrite_ptr,
                                             user=self.user,
                                             **kwargs)
                    created_ptr = True
                else:
                    Messages.info('Zone %s has no view named %s.' % (reverse_zone.name, view_name))
            return reverse_zone, created_ptr

        if type in ('A', 'AAAA', 'PTR'):
            if profile:
                raise InvalidParameterError('Cannot add PTR records to zone profiles.')
            view = kwargs.pop('view', None)
            overwrite_a = kwargs.pop('overwrite_a', False)
            overwrite_ptr = kwargs.pop('overwrite_ptr', False)
            forward_name = kwargs.pop('ptrdname' if type == 'PTR' else 'name')
            forward_zone = reverse_zone = None
            created_ptr = False
            if type == 'PTR':
                reverse_zone, created_ptr = create_reverse()
                if create_linked:
                    forward_zone = create_forward()
            else:
                forward_zone = create_forward()
                # don't create reverse records for forward records in profiles
                if create_linked and not profile:
                    reverse_zone, created_ptr = create_reverse()
            if create_linked and not profile:
                if reverse_zone and not forward_zone:
                    Messages.warn('No forward zone found. Only creating reverse entry.')
                elif forward_zone and not reverse_zone:
                    Messages.warn('No reverse zone found. Only creating forward entry.')
                elif forward_zone and reverse_zone and not created_ptr:
                    Messages.warn('No reverse zone view found. Only creating forward entry.')
                elif not reverse_zone and not forward_zone:
                    Messages.warn('No record was created because no forward or reverse zone found.')
        else:
            zone_obj = get_rr_zone(kwargs['name'], zone, profile)
            if zone_obj:
                self.user.can_create_rr(get_view(zone_obj, view), type)
                dim.dns.create_single_rr(rr_type=type, zone=zone_obj, user=self.user, **kwargs)

    def _check_rr_parameters(self, kwargs, creating):
        if 'type' not in kwargs:
            raise InvalidParameterError('Missing required parameter: type')
        rr_type = kwargs['type'] = kwargs['type'].upper()
        rr_parameters = set(RR.get_class(rr_type).fields)
        if rr_type == 'PTR':
            required_parameters = set(['type'])
            if 'ip' not in kwargs and 'name' not in kwargs:
                raise InvalidParameterError('Missing required parameter: name or ip')
        else:
            required_parameters = set(['type', 'name'])
        optional_parameters = set(['create_linked'])
        if creating:
            if rr_type in ('A', 'AAAA', 'PTR'):
                optional_parameters |= set(['overwrite_a', 'overwrite_ptr', 'layer3domain'])
            else:
                optional_parameters |= set(['overwrite'])
            if rr_type == 'PTR':
                optional_parameters |= set(['create_revzone'])
            required_parameters |= rr_parameters
        else:
            # When deleting, either all rr_parameters must be specified or none
            if set(kwargs.keys()) & rr_parameters:
                required_parameters |= rr_parameters
            if rr_type in ('A', 'AAAA', 'PTR'):
                optional_parameters |= set(['layer3domain'])
        missing_parameters = required_parameters - set(kwargs.keys())
        extra_parameters = set(kwargs.keys()) - required_parameters - optional_parameters
        if rr_type == 'PTR':
            extra_parameters -= set(['name', 'ip'])
        if missing_parameters:
            raise InvalidParameterError('Missing required parameters: %s' % ' '.join(missing_parameters))
        if extra_parameters:
            raise InvalidParameterError('Unknown parameters: %s' % ' '.join(extra_parameters))

    def _parse_ip_from_rr_parameters(self, kwargs, zone, creating, allow_overlap=False):
        rr_type = kwargs['type']
        if rr_type == 'PTR' and ('ip' not in kwargs) and ('name' in kwargs):
            name = kwargs['name']
            if not name.endswith('.'):
                if not zone:
                    raise InvalidParameterError('Name must be a FQDN')
                name += '.' + zone + '.'
            kwargs['ip'] = dim.dns.get_ip_from_ptr_name(name)
        if rr_type in ('A', 'AAAA', 'PTR') and 'ip' in kwargs:
            ip = parse_ip(kwargs['ip'])
            for type, version in list(dict(A=4, AAAA=6).items()):
                if rr_type == type and ip.version != version:
                    raise InvalidParameterError('Invalid IPv%d: %s' % (version, kwargs['ip']))

            def find_parent():
                parents = Ipblock._ancestors_noparent_query(ip, None).filter(
                    or_(Ipblock.status == get_status('Subnet'), Ipblock.status == get_status('Container'))).all()
                subnets = [p for p in parents if p.status.name == 'Subnet']
                if len(subnets) == 1:
                    return subnets[0].layer3domain
                elif len(subnets) == 0:
                    containers = [p for p in parents if p.status.name == 'Container']
                    if containers and all([c.layer3domain == containers[0].layer3domain for c in containers]):
                        return containers[0].layer3domain
            layer3domain = _get_layer3domain_arg(kwargs.get('layer3domain'),
                                                 guess_function=find_parent if creating else lambda: _find_ip(ip))
            try:
                kwargs['ip'] = check_block(get_block(kwargs['ip'], layer3domain), layer3domain,
                                           dict(host=True, status='Static'))
            except Exception as e:
                if creating:
                    kwargs['ip'] = self._ip_mark(check_ip(ip, layer3domain, {'host': True}), layer3domain=layer3domain,
                                                 attributes=None, parse_rr=True, allow_overlap=allow_overlap)
                else:
                    raise InvalidParameterError('Invalid IP %s: %s' % (kwargs['ip'], str(e)))
        if rr_type == 'PTR' and ('ip' in kwargs) and ('name' not in kwargs):
            kwargs['name'] = dim.dns.get_ptr_name(kwargs['ip'])

    def _get_rr_and_zone(self, view=None, **kwargs):
        def _get_rr_helper(name, view=None, type=None, **kwargs):
            zone = get_rr_zone(name, None, None)
            if zone is None:
                raise DimError('Zone for %s not found' % name)
            rrs = RR.query.filter_by(name=name)
            if view is not None:
                rrs = rrs.filter_by(view=get_view(zone, view))
            else:
                if len(zone.views) > 1:
                    raise MultipleViewsError('A view must be selected from: ' + ' '.join(sorted([v.name for v in zone.views])))
            if type is not None:
                rrs = rrs.filter_by(type=type)
                if kwargs:
                    kwargs = RR.validate_args(type, **kwargs)
                    value = RR.get_class(type).value_from_fields(**kwargs)
                    rrs = rrs.filter_by(value=value)
            if rrs.count() > 1:
                raise DimError('%s is ambiguous' % name)
            elif rrs.count() == 1:
                return (rrs.one(), zone)
            raise DimError('No records found.')

        if 'type' in kwargs:
            self._check_rr_parameters(kwargs, creating=False)
            self._parse_ip_from_rr_parameters(kwargs, zone=None, creating=False)
            if kwargs['type'] == 'PTR' and 'ip' in kwargs:
                kwargs['name'] = dim.dns.get_ptr_name(kwargs['ip'])
                del kwargs['ip']
        else:
            if 'name' not in kwargs:
                raise InvalidParameterError('Missing required parameter: name')
            unknown = set(kwargs.keys()) - set(['name'])
            if unknown:
                raise InvalidParameterError('Unknown parameters: %s' % ' '.join(unknown))
        return _get_rr_helper(view=view, **kwargs)

    def _rr_create(self, zone=None, views=None, ttl=None, comment=None, profile=False, allow_overlap=False, **kwargs):
        self._check_rr_parameters(kwargs, creating=True)
        self._parse_ip_from_rr_parameters(kwargs, zone=zone, creating=True, allow_overlap=allow_overlap)
        for view in views or [None]:
            self._rr_create_single_pair(zone=zone, view=view, profile=profile, ttl=ttl, comment=comment, **kwargs)

    def _rr_edit(self, rr, views, references, user, **kwargs):
        a_name_changes = []
        name_changes = []
        other_changes = []

        def add_rr_change(rr, changes):
            l = other_changes
            if rr.type in ['A', 'AAAA'] and 'name' in changes:
                l = a_name_changes
            elif 'name' in changes:
                l = name_changes
            l.append((rr, changes))
        if kwargs:
            add_rr_change(rr, kwargs)
        if references and kwargs:
            # Validate references parameter
            references = [int(i) for i in references]
            rrs = RR.query.filter(RR.id.in_(references)).all()
            if len(rrs) != len(references):
                missing_refs = set(references) - set([r.id for r in rrs])
                raise InvalidParameterError('Invalid references ids: ' + ', '.join(missing_refs))
            references = rrs
            (target, ip) = _rr_edit_compute_target_and_ip(rr, kwargs)
            # Compute changes for references
            for rr in references:
                changes = {}
                if ip is not None and rr.type in ['A', 'AAAA', 'PTR'] and rr.ipblock.ip != ip:
                    changes['ip'] = str(ip)
                if target:
                    field = None
                    if rr.type in ['A', 'AAAA']:
                        field = 'name'
                    elif rr.type == 'PTR':
                        field = 'ptrdname'
                    else:
                        target_fields = set(RRType.target_fields) & set(RR.get_class(rr.type).fields)
                        if len(target_fields) == 1:
                            field = target_fields.pop()
                    if field is not None:
                        changes[field] = target
                if changes:
                    add_rr_change(rr, changes)
        ipblocks = set()

        def delete_rr(rr):
            if rr.ipblock_id:
                ipblocks.add(rr.ipblock_id)
            delete_single_rr(rr, self.user)

        def create_rr(rr, changes):
            if rr.ipblock_id:
                ipblocks.discard(rr.ipblock_id)
            data = _rr_create_info(rr, changes, views)
            if rr.type in ['PTR']:
                data['create_linked'] = False
            self._rr_create(**data)
        # Delete rrs with no name modifications
        for (rr, changes) in other_changes:
            logging.debug('Deleting rr with no name change %s %s' % (rr, changes))
            delete_rr(rr)
        # Recreate rrs with name modifications (except A/AAAA)
        for (rr, changes) in name_changes:
            logging.debug('Deleting rr with name change %s %s' % (rr, changes))
            delete_rr(rr)
        # Delete all A/AAAA records with name changed
        for (rr, _) in a_name_changes:
            logging.debug('Deleting A/AAAA rr with changed name %s' % rr)
            delete_with_references(RR.query.filter(RR.id == rr.id), free_ips=False, references='warn', user=user)
        # Create A/AAAA record with name changed
        # Only creating once, since we pass all the views
        for (rr, changes) in a_name_changes:
            logging.debug('Creating A/AAAA rr with changed name %s %s' % (rr, changes))
            self._rr_create(**_rr_create_info(rr, changes, views))
            break
        # Recreate rrs with name modifications (except A/AAAA)
        for (rr, changes) in name_changes:
            logging.debug('Deleting rr with name change %s %s' % (rr, changes))
            create_rr(rr, changes)
        # Recreate rrs with no name modifications
        for (rr, changes) in other_changes:
            logging.debug('Recreating rr with no name change %s %s' % (rr, changes))
            create_rr(rr, changes)
        if ipblocks:
            free_ipblocks(ipblocks)
        return {'messages': Messages.get()}

    def _ippool_query(self, pool, vlan, cidr, can_allocate, owner, layer3domain):
        q = db.session.query(Pool.id, Pool.name)
        if layer3domain is not None:
            q = q.filter(Pool.layer3domain == get_layer3domain(layer3domain))
        if cidr is not None:
            q = q.outerjoin(Pool.subnets)
        if can_allocate:
            if not self.user.is_super_admin:
                q = q.join(
                    AccessRight,
                    or_(
                        AccessRight.access == 'network_admin',
                        and_(AccessRight.access == 'allocate',
                             AccessRight.object_class == 'Ippool',
                             AccessRight.object_id == Pool.id)))\
                    .outerjoin(GroupRight).outerjoin(Group).outerjoin(*Group.users.attr)\
                    .filter(User.username == self.username).distinct()
        if pool is not None:
            q = q.filter(Pool.name.like(make_wildcard(pool)))
        if vlan is not None:
            q = q.join(Vlan).filter(Vlan.vid == validate_vlan(vlan))
        if cidr is not None:
            cidr = parse_ip(cidr)
            q = q.filter(Ipblock.version == cidr.version)\
                .filter(inside(Ipblock.address, cidr))\
                .filter(Ipblock.prefix >= cidr.prefix).distinct()
        if owner is not None:
            q = q.join(Group).filter(Pool.owner == get_group(owner))
        return q

    def _changeable_views(self, can_create_rr, can_delete_rr):
        ''' Return the number of views that user has access to. '''
        q = db.session.query(func.count(ZoneView.id)).correlate(Zone).filter(ZoneView.zone_id == Zone.id)
        return self._filter_views(q, can_create_rr, can_delete_rr).scalar_subquery()

    def _filter_views(self, query, can_create_rr, can_delete_rr):
        access = self._view_access_expr(can_create_rr=can_create_rr, can_delete_rr=can_delete_rr)
        return query.join(AccessRight, or_(*access))\
                    .outerjoin(GroupRight).outerjoin(Group).outerjoin(*Group.users.attr)\
                    .filter(User.username == self.username)

    def _view_access_expr(self, can_create_rr, can_delete_rr):
        access = [AccessRight.access == 'dns_admin']

        def filter_right(right):
            return and_(AccessRight.access == right,
                        AccessRight.object_class == 'ZoneView',
                        AccessRight.object_id == ZoneView.id)
        if can_create_rr:
            access.append(filter_right('create_rr'))
        if can_delete_rr:
            access.append(filter_right('delete_rr'))
        return access

    def _zone_view_rights(self):
        if self.user.is_super_admin:
            return [literal(True).label('can_create_rr'), literal(True).label('can_delete_rr')]
        else:
            def has_access(can_create_rr, can_delete_rr):
                access = self._view_access_expr(can_create_rr=can_create_rr, can_delete_rr=can_delete_rr)
                return db.session.query(func.count(AccessRight.id)).correlate(ZoneView).filter(or_(*access))\
                    .outerjoin(GroupRight).outerjoin(Group).outerjoin(*Group.users.attr)\
                    .filter(User.username == self.username).scalar_subquery()
            return [and_(0 < has_access(True, False)).label('can_create_rr'),
                    and_(0 < has_access(False, True)).label('can_delete_rr')]


    @readonly
    def ipblock_get_attrs_multi(self, ipblock, layer3domain=None, full=False, filters = {}, **options):
        ip = parse_ip(ipblock)
        ipblocks = Ipblock.query_ip(ip, layer3domain, **filters)
        result = []
        for ipblock in ipblocks.all():
            result.append(self.ipblock_get_attrs(ipblock, layer3domain=ipblock.layer3domain.name, full=full, **options))
        return result


def TRPC(*args, **kwargs):
    '''
    Returns an instance to a TransactionProxy class wrapping the public methods
    of RPC in transactions.
    '''
    return TransactionProxy(RPC(*args, **kwargs))


def parse_ip(ip_str):
    try:
        return IP(ip_str)
    except:
        logging.debug('parse_ip', exc_info=True)
        raise InvalidIPError("Invalid IP: '%s'" % (ip_str))


def check_ip(ip, layer3domain, options, ensure_empty_options=True):
    if 'host' in options:
        if options.pop('host') and not ip.is_host:
            raise InvalidParameterError('Host address expected')
    if 'pool' in options:
        pool = get_pool(options.pop('pool'))
        if layer3domain != pool.layer3domain or ip not in pool:
            raise NotInPoolError("'%(block)s' from layer3domain '%(layer3domain)s' is not part of the pool '%(pool)s'"
                                 % dict(block=str(ip), layer3domain=layer3domain.name, pool=pool.name))
    if 'delegation' in options:
        delegation = get_block(options.pop('delegation'), layer3domain=layer3domain)
        if delegation.status.name != 'Delegation':
            raise InvalidStatusError("%s from layer3domain %s is not a Delegation" % (delegation, layer3domain.name))
        if ip not in delegation:
            raise NotInDelegationError(
                "%s from layer3domain %s is not part of the delegation %s" % (ip, layer3domain.name, delegation))
    if ensure_empty_options:
        check_options(options)
    return ip


def check_block(block, layer3domain, options, ip=None):
    if ip is None:
        ip = block.ip
    check_ip(ip, layer3domain, options, ensure_empty_options=False)
    if 'status' in options:
        expected = options.pop('status')
        if block is not None and block.status.name != expected:
            raise InvalidStatusError("%(block)s from layer3domain %(layer3domain)s is a %(status)s block (expected '%(expected)s')"
                                     % dict(block=ip, status=block.status.name, expected=expected,
                                            layer3domain=layer3domain.name))
    check_options(options)
    return block


def check_options(options):
    if options:
        raise InvalidParameterError("Unknown options: " + ' '.join(list(options.keys())))


def get_pool(pool_name):
    pool = Pool.query.filter_by(name=pool_name).first()
    if pool:
        return pool
    else:
        raise InvalidPoolError("Pool '%s' does not exist" % pool_name)


def get_status(status_name):
    status = IpblockStatus.query.filter_by(name=status_name).first()
    if status:
        return status
    else:
        raise InvalidStatusError("Invalid status '%s'" % status_name)


def get_block(block_str, layer3domain):
    ip = parse_ip(block_str)
    block = Ipblock.query_ip(ip, layer3domain).first()
    if block is None:
        raise InvalidIPError("IP block '%s' does not exist in layer3domain %s" % (block_str, layer3domain.name))
    return block


def get_object_id_class(access, object_name):
    if access == 'allocate':
        pool = get_pool(object_name)
        return (pool.id, 'Ippool')
    elif access in ('network_admin',
                    'dns_admin',
                    'dns_update_agent',
                    'zone_create'):
        return (0, 'all')
    elif access == 'zone_admin':
        zone = get_zone(object_name, profile=False)
        return (zone.id, 'Zone')
    elif access in ['create_rr', 'delete_rr']:
        zone, view_name = object_name
        view_id = db.session.query(ZoneView.id).filter_by(zone=zone, name=view_name).scalar()
        if view_id is None:
            raise InvalidParameterError("View '%s' does not exist in zone %s" % (view_name, zone.display_name))
        else:
            return (view_id, 'ZoneView')
    elif access == 'attr':
        pool = get_pool(object_name[1])
        return (pool.id, 'Ippool')
    else:
        raise InvalidAccessRightError("Invalid access right: %r" % access)


def get_group(group_str):
    group = Group.query.filter_by(name=group_str).first()
    if group is None:
        raise InvalidGroupError("Group '%s' does not exist" % group_str)
    return group


def get_user(user_str: str) -> User:
    user = User.query.filter_by(username=user_str).first()
    if user is None:
        raise InvalidUserError("User '%s' does not exist" % user_str)
    return user


def get_layer3domain(name):
    layer3domain = Layer3Domain.query.filter_by(name=name).first()
    if layer3domain is None:
        raise InvalidUserError("Layer3domain '%s' does not exist" % name)
    return layer3domain


def get_subnet(ip_or_block, layer3domain):
    if hasattr(ip_or_block, 'subnet'):
        if ip_or_block.status == 'Subnet':
            return ip_or_block
        ip = ip_or_block.ip
    else:
        ip = ip_or_block
    ancestors = Ipblock._ancestors_noparent(ip, layer3domain, include_self=True)
    if ancestors:
        return ancestors[0].subnet
    else:
        return None


def validate_vlan(vid):
    try:
        if not isinstance(vid, int):
            vid = int(vid)
    except:
        raise InvalidVLANError("VLAN id %r is invalid, can't be converted to integer" % vid)
    if vid < 1 or vid > 4094:
        raise InvalidVLANError("VLAN id %r is invalid, must be between 1 and 4094" % vid)
    return vid


def make_vlan(vid):
    vid = validate_vlan(vid)
    logging.debug('make_vlan(%r)' % vid)
    vlan = Vlan.query.filter_by(vid=vid).first()
    if not vlan:
        vlan = Vlan(vid=vid)
        db.session.add(vlan)
    return vlan


def subnet_reserved_ips(subnet, dont_reserve_network_broadcast, no_reserve_class_c_boundaries):
    reserved = []
    if subnet.version == 4:
        if not dont_reserve_network_broadcast:
            reserved.append(subnet.network)
            reserved.append(subnet.broadcast)
            start = subnet.network.address
            end = subnet.broadcast.address

            # Reserve .0 and .255 addresses because some buggy routers have problems
            # with them
            zero = start - (start % 256)
        
            if no_reserve_class_c_boundaries:
                reserved.append(IP(zero, prefix=32, version=4))
                reserved.append(IP(end, prefix=32, version=4))
            else:
                while zero < end:
                    if start <= zero:
                        reserved.append(IP(zero, prefix=32, version=4))
                    if zero + 255 <= end:
                        reserved.append(IP(zero + 255, prefix=32, version=4))
                    zero += 256
        # Remove duplicates
            for address in [subnet.network, subnet.broadcast]:
                if reserved.count(address) == 2:
                    reserved.remove(address)
    elif subnet.version == 6 and not dont_reserve_network_broadcast:
        reserved = [IP(subnet.address, prefix=128, version=6)]
    return reserved


def _ipblock_create(address, prefix, version, layer3domain, allow_overlap=False, **kwargs):
    '''Check IP space overlapping before calling Ipblock.create'''
    ip = IP(int(address), prefix, version)
    if Layer3Domain.query.count() != 1:
        # Check overlap with other layer3domains (no containers)
        q = Ipblock.query.filter(Ipblock.version == version, Ipblock.layer3domain != layer3domain).join(
            IpblockStatus).filter(IpblockStatus.name != 'Container')
        cond = Ipblock._ancestors_noparent_condition(ip, True)
        if not ip.is_host:
            cond = or_(cond,
                       and_(Ipblock.address >= address,
                            Ipblock.address < address + 2 ** (ip.bits - prefix)))
        other = q.filter(cond).order_by(Ipblock.prefix.asc()).first()
        if other:
            overlap = ip if ip in other.ip else other.ip
            whitelisted = False
            # Check if overlap is inside whitelist
            for wl_ip in app.config['LAYER3DOMAIN_WHITELIST']:
                if overlap in wl_ip:
                    whitelisted = True
                    break
            if not whitelisted:
                raise InvalidParameterError('IP Space %s not whitelisted for duplicate use' % overlap)
            else:
                message = '%s in layer3domain %s %s with %s in layer3domain %s' % (
                    ip, layer3domain.name, 'overlaps' if allow_overlap else 'would overlap', other.ip,
                    other.layer3domain.name)
                if allow_overlap:
                    Messages.warn(message)
                else:
                    raise InvalidParameterError(message)
    return Ipblock.create(address=address, prefix=prefix, version=version, layer3domain=layer3domain, **kwargs)


def reserve(ip, layer3domain):
    block = Ipblock.query_ip(ip, layer3domain).first()
    if block:
        if block.status.name == 'Reserved':
            pass
        elif block.status.name == 'Available':
            block.status = get_status('Reserved')
        else:
            raise AlreadyExistsError("%s from layer3domain %s is already allocated with status %s" % (
                ip, layer3domain.name, block.status.name))
    else:
        Ipblock.create(address=ip.address,
                       prefix=ip.prefix,
                       version=ip.version,
                       layer3domain=layer3domain,
                       status=get_status('Reserved'))


def validate_soa_attrs(attrs):
    if attrs is None:
        return None
    if 'mail' in attrs:
        attrs['mail'] = validate_mail(None, 'mail', attrs['mail'])
    invalid_attrs = set(attrs.keys()) - set(ZoneView.soa_attrs)
    if invalid_attrs:
        raise InvalidParameterError("Unknown options: " + ' '.join(invalid_attrs))
    return attrs


def free_if_reserved(ip, layer3domain):
    block = Ipblock.query_ip(ip, layer3domain).first()
    if block and block.status.name == 'Reserved':
        db.session.delete(block)


def _filter_addresses(query, version, layer3domain, start, end):
    '''
    Returns a new query filtering for host addresses between `start` and `end`.
    '''
    return query\
        .filter(Ipblock.prefix == (32 if version == 4 else 128))\
        .filter(Ipblock.address.between(start, end))\
        .filter(Ipblock.layer3domain_id == layer3domain.id)\
        .order_by(Ipblock.address)


def _get_ip_list(version, start, end, ip_type, limit, full, extra_columns, id_map_needed, layer3domain):
    '''
    Returns a list of dicts with the Ipblock attributes and optionally a mapping
    from Ipblock id to dict.
    '''
    def process_range(range_start, range_end):
        range_end_max = min(range_end, range_start + limit - len(ips) - 1)
        if range_start <= range_end_max:
            logging.debug("process_range(%s, %s)", IP(range_start, version=version), IP(range_end_max, version=version))
        for address in range(range_start, range_end_max + 1):
            ips.append({'ip': IP(address, version=version).label(expanded=full),
                        'status': 'Available'})

    def process_block(block, row):
        ip_dict = dict([('ip', block.label(expanded=full)), ('status', row.status)] +
                       [(c, getattr(row, c)) for c in extra_columns])
        if id_map_needed:
            id2ip_dict[row.id] = ip_dict
        ips.append(ip_dict)
    ip_query = _filter_addresses(
        db.session.query(Ipblock.id,
                         Ipblock.address,
                         Ipblock.prefix,
                         IpblockStatus.name.label('status'),
                         *[getattr(Ipblock, c) for c in extra_columns]).join(IpblockStatus),
        version,
        layer3domain,
        start,
        end)
    range_start = start
    id2ip_dict = {}
    ips = []
    for row in ip_query:
        ip = IP(int(row.address), row.prefix, version=version)
        if ip_type in ('all', 'free'):
            # Process the gap before the current ip
            if len(ips) >= limit:
                break
            process_range(range_start, ip.address - 1)
            range_start = ip.address + 1
        # Process the current ip
        if len(ips) >= limit:
            break
        if ip_type == 'free':
            if row.status == 'Available':
                process_block(ip, row)
        elif ip_type == 'used':
            if row.status == 'Static':
                process_block(ip, row)
        else:
            process_block(ip, row)
    if ip_type in ('all', 'free'):
        # Process the gap after the last row
        process_range(range_start, end)
    return ips, id2ip_dict


def get_subnet_ips(version, start, end, ip_type, limit, full, attributes, layer3domain):
    '''
    If attributes is None, all attributes are returned. Otherwise it's a set of
    attribute names to be retrieved.
    '''
    extra_columns = set(['created', 'modified', 'modified_by'])
    if attributes is not None:
        extra_columns &= set(attributes)
    ptr_needed = attributes is None or 'ptr_target' in attributes

    # Determine if a separate query for custom attributes is needed
    if attributes is None:
        custom_attrs_needed = True
        custom_attrs = None
    else:
        custom_attrs = set(attributes) - set(IpblockAttrName.reserved)
        if custom_attrs:
            custom_attrs_needed = True
        else:
            custom_attrs_needed = False

    # The custom attributes will be populated later with the help of id2ip_dict
    # if needed
    ips, id2ip_dict = _get_ip_list(version=version,
                                   layer3domain=layer3domain,
                                   start=start,
                                   end=end,
                                   ip_type=ip_type,
                                   limit=limit,
                                   full=full,
                                   extra_columns=extra_columns,
                                   id_map_needed=custom_attrs_needed or ptr_needed)
    if custom_attrs_needed:
        attr_query = _filter_addresses(
            db.session.query(Ipblock.id,
                             IpblockAttrName.name.label('name'),
                             IpblockAttr.value.label('value')),
            version=version,
            layer3domain=layer3domain,
            start=start,
            end=end) \
            .outerjoin(IpblockAttr, IpblockAttr.ipblock_id == Ipblock.id) \
            .join(IpblockAttrName, IpblockAttr.name_id == IpblockAttrName.id)
        if custom_attrs:
            attr_query = attr_query.filter(IpblockAttrName.name.in_(custom_attrs))

        for id, name, value in attr_query:
            if name is not None and id in id2ip_dict:
                id2ip_dict[id][name] = value

    if ptr_needed:
        ptrs = _filter_addresses(db.session.query(Ipblock.id, RR.target).filter(RR.type == 'PTR'),
                                 version=version,
                                 layer3domain=layer3domain,
                                 start=start,
                                 end=end).join(RR)
        for id, target in ptrs:
            if id in id2ip_dict and target:
                id2ip_dict[id]['ptr_target'] = target
    return ips


def make_wildcard(string):
    return string.replace('_', '\_').replace('*', '%').replace('?', '_')


def inside(address, cidr):
    return between(address, cidr.network.address, cidr.broadcast.address)


def get_rights(access, object):
    '''
    Returns a list of tuples (access, object).
    For create_rr and delete_rr object is a tuple (zone, view_name).
    '''
    rights = []
    if access in ['create_rr', 'delete_rr']:
        try:
            zone_name, views = object
            zone_name = Zone.from_display_name(zone_name)
        except:
            raise InvalidParameterError('Invalid object: %r' % object)
        try:
            zone = db.session.query(Zone).filter_by(name=zone_name).one()
        except:
            raise InvalidParameterError("Zone '%s' does not exist" % (object[0], ))
        if len(views) == 0:
            if len(zone.views) > 1:
                raise MultipleViewsError('A view must be selected from: ' + ' '.join(sorted([view.name for view in zone.views])))
            else:
                rights = [(access, (zone, zone.views[0].name))]
        else:
            for view in views:
                rights += [(access, (zone, view))]
    else:
        rights = [(access, object)]
    return rights


def get_zone_group(group_str):
    group = ZoneGroup.query.filter_by(name=group_str).first()
    if group is None:
        raise InvalidParameterError("Zone-group '%s' does not exist" % group_str)
    return group


def get_output(output_str):
    output = Output.query.filter_by(name=output_str).first()
    if output is None:
        raise InvalidParameterError("Output '%s' does not exist" % output_str)
    return output


def get_registrar_account(name):
    ra = RegistrarAccount.query.filter_by(name=name).first()
    if ra is None:
        raise InvalidParameterError("Registrar-account '%s' does not exist" % name)
    return ra


def _cidr(obj):
    return dict(address=obj.address, prefix=obj.prefix)


def _rr_create_info(rr, changes, views=None):
    '''
    Return a dict containing info for creating a rr (starting from *rr* and applying *changes* to it).
    '''
    info = RR.get_class(rr.type).fields_from_value(rr.value)
    info.update(dict(type=rr.type,
                     views=[rr.view.name],
                     profile=False,
                     ttl=rr.ttl,
                     comment=rr.comment))
    if rr.type != 'PTR':
        info['name'] = rr.name
    else:
        info['ip'] = dim.dns.get_ip_from_ptr_name(rr.name)
    info.update(changes)
    if 'name' in changes and views is not None:
        info['views'] = views
    return info


def _rr_edit_compute_target_and_ip(rr, kwargs):
    target = None
    if 'name' in kwargs and rr.name != kwargs['name'] and rr.type != 'PTR':
        target = kwargs['name']
    if rr.type == 'PTR' and 'ptrdname' in kwargs and rr.target != kwargs['ptrdname']:
        target = kwargs['ptrdname']
    ip = None
    if rr.type in ['A', 'AAAA', 'PTR'] and 'ip' in kwargs and rr.ipblock.ip != IP(kwargs['ip']):
        ip = IP(kwargs['ip'])
    return (target, ip)


def _zone_view_display_string(zone, view_name):
    return '%s%s' % (zone.display_name, ' view %s' % view_name if len(zone.views) > 1 else '')


def _check_department_number(department_number):
    if Group.query.filter_by(department_number=department_number).count():
        raise AlreadyExistsError("A group with department_number %s already exists"
                                 % department_number)
    try:
        lg = dim.ldap_sync.LDAP().departments('(ou=%s)' % department_number)
        if not lg:
            raise Exception("Department number %s not found" % department_number)
        return lg[0].name
    except Exception as e:
        raise DimError('LDAP error: %s' % e)


def _check_parent_view(view):
    parent_zone = get_parent_zone(view.zone.name)
    if parent_zone is None:
        return
    for parent_view in parent_zone.views:
        if parent_view.name == view.name:
            return
    Messages.warn('Parent zone %s has no view named %s, automatic NS record management impossible' % (
        parent_zone.name, view.name))


def _group_grant_access(group, access, object):
    rights = get_rights(access, object)
    if access == 'zone_create':
        # Check that no user has zone_create from a different user-group
        user_ids = [user.id for user in group.users]
        if Group.query.filter(Group.id != group.id) \
                .filter(Group.users.any(User.id.in_(user_ids))) \
                .join(GroupRight).join(AccessRight).filter(AccessRight.access == 'zone_create').count() != 0:
            raise InvalidParameterError('An user can be granted the zone_create right from a single user-group')
    for (access, object) in rights:
        object_id, object_class = get_object_id_class(access, object)
        if access == 'attr':
            access = 'attr.' + object[0]
        group.rights.add(AccessRight.find_or_create(access=access,
                                                    object_id=object_id,
                                                    object_class=object_class))


def _get_ksk_keys(zone):
    return ZoneKey.query.filter(ZoneKey.type == 'ksk').filter(ZoneKey.zone == zone).all()


def _ongoing_actions_query(zone):
    return RegistrarAction.query.filter_by(zone=zone).join(Zone).filter(
        RegistrarAction.status.in_(['pending', 'running'])).count()


def _check_no_ongoing_actions(zone):
    if _ongoing_actions_query(zone):
        raise InvalidParameterError(
            'An action that updates the zone %s at the registrar is in progress' % zone.display_name)


def _update_registrar_keys(zone):
    keys = _get_ksk_keys(zone)
    action = RegistrarAction(zone=zone, status='pending')
    for key in keys:
        key.registrar_action = action
    db.session.add(action)


def _get_layer3domain_arg(layer3domain, options=None, guess_function=None):
    if layer3domain is None:
        try:
            return Layer3Domain.query.one()
        except:
            if options and options.get('pool'):
                pool = get_pool(options['pool'])
                return pool.layer3domain
            else:
                if guess_function:
                    l = guess_function()
                    if l:
                        return l
                raise InvalidParameterError('A layer3domain is needed')
    else:
        return get_layer3domain(layer3domain)


def _find_ip(ip):
    if type(ip) != IP:
        ip = parse_ip(ip)
    blocks = Ipblock.query_ip(ip, None).all()
    if len(blocks) == 1:
        return blocks[0].layer3domain


def set_rd(layer3domain, rd):
    layer3domain.set_rd(rd)
    record_history(layer3domain, action='set_attr', attrname='rd', newvalue=rd,
                   oldvalue=layer3domain.display_rd if layer3domain.type == Layer3Domain.VRF else None)


def _find_ipblock(ipblock, layer3domain, status=None):
    # if address doesn't match the prefix, truncate it
    ip = IP(ipblock, auto_correct=True)
    if not valid_block(ipblock):
        Messages.warn('%s rounded to %s because it is not a valid CIDR block'
                      % (ipblock, ip))
    block = Ipblock.query_ip(ip, layer3domain).first()
    if block:
        return block
    status_str = ' or '.join(status)
    # Try ancestors
    if ip.prefix != 0:
        parents = Ipblock._ancestors_noparent_query(ip, layer3domain)
        if status:
            parents = parents.join(IpblockStatus).filter(IpblockStatus.name.in_(status))
        parents = parents.all()
        if parents:
            Messages.warn('%s rounded to %s because no ipblock exists at %s with status %s'
                        % (ip, parents[0].ip, ip, status_str))
            return parents[0]
    # Try descendants
    descendants = Ipblock.query.filter(inside(Ipblock.address, ip),
                                       Ipblock.version == ip.version,
                                       Ipblock.layer3domain_id == layer3domain.id)
    if status:
        descendants = descendants.join(IpblockStatus).filter(IpblockStatus.name.in_(status))
    descendants = descendants.order_by(Ipblock.prefix).all()
    if descendants:
        Messages.warn('%s rounded to %s because no ipblock exists at %s with status %s'
                      % (ip, descendants[0].ip, ip, status_str))
        return descendants[0]


def fast_count(query):
    '''Run a SELECT COUNT(*) with the same filters as query and return the resulting number'''
    # query.count() would use a subselect which is very slow in MySQL
    # return db.session.execute(query.statement.with_only_columns([func.count()]).order_by(None)).scalar()
    # FIXME: implement fas again
    return query.count()
