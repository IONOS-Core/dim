from contextlib import wraps

from sqlalchemy import Column, Integer, BigInteger, Boolean, String, Text, ForeignKey, UniqueConstraint, or_, literal
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.orm import relationship, backref

from dim import db
from dim.errors import PermissionDeniedError, InvalidAccessRightError
from dim.models import TrackChanges, get_session_tool
from dim.util import is_reverse_zone


def _find_or_create(klass):
    def find_or_create(**kwargs):
        o = klass.query.filter_by(**kwargs).first()
        if not o:
            o = klass(**kwargs)
            db.session.add(o)
        return o
    return find_or_create


class GroupMembership(db.Model):
    __tablename__ = 'usergroupuser'

    usergroup_id = Column(BigInteger, ForeignKey('usergroup.id'), primary_key=True)
    user_id = Column(BigInteger, ForeignKey('user.id'), primary_key=True)
    from_ldap = Column(Boolean, nullable=False)

    user = relationship('User', uselist=False, backref=backref('group_membership', cascade='all, delete-orphan'))
    group = relationship('Group', uselist=False, backref=backref('membership', cascade='all, delete-orphan', collection_class=set))

    def __init__(self, user=None, group=None, from_ldap=False):
        self.user = user
        self.group = group
        self.from_ldap = from_ldap


class UserType(db.Model):
    id = Column(BigInteger, primary_key=True, nullable=False)
    name = Column(String(128), nullable=False, unique=True)


class Group(db.Model, TrackChanges):
    __tablename__ = 'usergroup'

    id = Column(BigInteger, primary_key=True, nullable=False)
    # when department_number is set, ldap_sync will update the group name to match the linked
    # department name
    name = Column(String(128), nullable=False, unique=True)
    # when not null, this group is linked to an LDAP department
    department_number = Column(Integer, nullable=True, unique=True)

    users = association_proxy('membership', 'user')
    rights = association_proxy('group_rights', 'accessright')

    def __str__(self):
        return self.name

    @property
    def is_network_admin(self):
        return GroupRight.query.filter_by(group=self)\
            .join(AccessRight).filter_by(access='network_admin').count() != 0

    @property
    def is_dns_admin(self):
        return GroupRight.query.filter_by(group=self)\
            .join(AccessRight).filter_by(access='dns_admin').count() != 0

    @property
    def network_rights(self):
        return GroupRight.query.filter_by(group=self).join(AccessRight)\
            .filter(AccessRight.access.in_(AccessRight.grantable_by_network_admin)).count()

    @property
    def dns_rights(self):
        return GroupRight.query.filter_by(group=self).join(AccessRight)\
            .filter(AccessRight.access.in_(AccessRight.grantable_by_dns_admin)).count()

    def remove_user(self, user):
        self.users.remove(user)


class Department(db.Model):
    '''This table only serves as a cache of the departments available in LDAP'''
    department_number = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)


class GroupRight(db.Model):
    __table_constraints__ = (UniqueConstraint('usergroup_id', 'accessright_id'), )

    id = Column(BigInteger, primary_key=True, nullable=False)
    accessright_id = Column(BigInteger, ForeignKey('accessright.id'))
    usergroup_id = Column(BigInteger, ForeignKey('usergroup.id'))

    group = relationship(Group, backref=backref('group_rights', collection_class=set, cascade='all, delete-orphan'))
    accessright = relationship('AccessRight', uselist=False, backref=backref('grouprights', cascade='all, delete-orphan'))

    def __init__(self, accessright):
        self.accessright = accessright


class AccessRight(db.Model):
    __table_constraints__ = (UniqueConstraint('access', 'object_class', 'object_id'), )

    id = Column(BigInteger, primary_key=True, nullable=False)
    access = Column(String(128), nullable=False)
    object_class = Column(String(128), nullable=False)
    object_id = Column(BigInteger, nullable=False)

    groups = association_proxy('grouprights', 'group')

    grantable_by_network_admin = ['allocate', 'attr']
    grantable_by_dns_admin = [
        'dns_update_agent',
        'zone_create',
        'zone_admin',
        'create_rr',
        'delete_rr']


AccessRight.find_or_create = staticmethod(_find_or_create(AccessRight))


def permission(func):
    @wraps(func)
    def wrapper(self, *args):
        if self.is_super_admin:
            return
        if not func(self, *args):
            import inspect
            argspec = inspect.getfullargspec(func).args
            if 'type' in argspec:
                args = args[0:argspec.index('type') - 1] + args[argspec.index('type'):]
            reason = ' '.join(str(a) for a in (func.__name__, ) + args)
            raise PermissionDeniedError('Permission denied (%s)' % reason)
        else:
            return True
    return wrapper


UserRights = dict(
    can_dns_admin={'tool_access': False, 'access': [('dns_admin', None)]},
    can_create_forward_zones={'tool_access': False, 'access': [('dns_admin', None), ('zone_create', None)]},
    can_create_reverse_zones={'tool_access': True, 'access': [('dns_admin', None), ('network_admin', None),
                                                               ('zone_create', None)]},
    can_delete_reverse_zones={'tool_access': True, 'access': [('dns_admin', None), ('network_admin', None)]},
    can_dns_update_agent={'tool_access': False, 'access': [('dns_update_agent', None)]},

    can_network_admin={'tool_access': True, 'access': [('network_admin', None)]},
    can_modify_pool_attributes={'tool_access': False, 'access': [('dns_admin', None), ('network_admin', None)]},
    can_modify_container_attributes={'tool_access': False, 'access': [('dns_admin', None), ('network_admin', None)]},

    can_create_groups={'tool_access': False, 'access': [('dns_admin', None), ('network_admin', None)]})


class User(db.Model):
    __tablename__ = 'user'

    id = Column(BigInteger, primary_key=True, nullable=False)
    user_type_id = Column(BigInteger, ForeignKey('usertype.id'))
    username = Column(String(128), unique=True)  # same as the LDAP o attribute
    preferences = Column(Text)

    # LDAP fields
    ldap_uid = Column(Integer, unique=True, nullable=True)
    ldap_cn = Column(String(128), nullable=True)
    department_number = Column(Integer, nullable=True)

    groups = association_proxy('group_membership', 'group')

    user_type = relationship(UserType)

    def __init__(self, username, user_type='User', ldap_uid=None, ldap_cn=None, department_number=None,
                 register=True):
        self.username = username
        self.user_type = UserType.query.filter_by(name=user_type).one()  # TODO enum
        self.ldap_uid = ldap_uid
        self.ldap_cn = ldap_cn
        self.department_number = department_number
        if register:
            self.register()

    def register(self):
        all_users = Group.query.filter_by(name='all_users').first()
        if all_users is None:
            all_users = Group(name='all_users')
            db.session.add(all_users)
        all_users.users.add(self)

    def __hash__(self):
        return hash(self.username)

    def __eq__(self, o):
        return self.username == o.username

    def has_any_access(self, access_list):
        anylist = []
        for access, obj in access_list:
            if obj is None:
                anylist.append(Group.rights.any(access=access))
            else:
                class_name = dict(Pool='Ippool').get(obj.__class__.__name__, obj.__class__.__name__)
                anylist.append(Group.rights.any(access=access, object_class=class_name, object_id=obj.id))
        return Group.query.filter(Group.users.any(id=self.id)).filter(or_(*anylist)).count() != 0

    @permission
    def can_set_attribute(self, pool, attr):
        is_network_admin = self.has_any_access([('network_admin', None)])
        is_dns_admin = self.has_any_access([('dns_admin', None)])
        if is_network_admin or is_dns_admin:
            return True
        return Group.query.filter(Group.users.any(id=self.id)). \
            join(GroupRight). \
            join(AccessRight).filter(
                AccessRight.object_id == pool.id,
                AccessRight.object_class == 'Ippool',
                literal('attr.' + attr).like(AccessRight.access + '%')).count() != 0


    @property
    def is_super_admin(self):
        return self.user_type.name == 'Admin'

    @permission
    def can_grant_access(self, group, access):
        if get_session_tool():
            return False
        if access in ('network_admin', 'dns_admin'):
            return self.user_type.name == 'Admin'
        else:
            if access in AccessRight.grantable_by_network_admin:
                self.can_network_admin()
            elif access in AccessRight.grantable_by_dns_admin:
                self.can_dns_admin()
            else:
                raise InvalidAccessRightError('Invalid access right: %r' % access)
            return self.can_edit_group(group)

    @permission
    def can_edit_group(self, group):
        if get_session_tool():
            return False
        if group.is_network_admin or group.is_dns_admin:
            return False
        is_network_admin = self.has_any_access([('network_admin', None)])
        is_dns_admin = self.has_any_access([('dns_admin', None)])
        return is_network_admin or is_dns_admin

    @permission
    def can_allocate(self, pool):
        return self.has_any_access([('network_admin', None),
                                    ('dns_admin', None),
                                    ('allocate', pool)])

    @permission
    def can_manage_zone(self, zone):
        access_list = [('dns_admin', None), ('zone_admin', zone)]
        # NetworkAdmin can manage reverse zones
        if is_reverse_zone(zone.name):
            access_list.append(('network_admin', None))
        return self.has_any_access(access_list)

    @permission
    def can_create_rr(self, view, type):
        if is_reverse_zone(view.zone.name) and type == 'PTR':
            return True
        else:
            return self.has_any_access([('create_rr', view),
                                        ('zone_admin', view.zone),
                                        ('dns_admin', None)])

    @permission
    def can_delete_rr(self, view, type):
        if is_reverse_zone(view.zone.name) and type == 'PTR':
            return True
        else:
            return self.has_any_access([('delete_rr', view),
                                        ('zone_admin', view.zone),
                                        ('dns_admin', None),
                                        ('network_admin', None)])

    def get_rights(self):
        available = list(UserRights.keys())
        if self.is_super_admin:
            return available
        perms = []
        for perm in available:
            if getattr(self, perm, None):
                try:
                    getattr(self, perm)()
                    perms.append(perm)
                except PermissionDeniedError:
                    pass
        return perms


for can, conf in list(UserRights.items()):
    def can_func(self, tool_access=conf['tool_access'], access=conf['access']):
        if not tool_access and get_session_tool():
            return False
        return self.has_any_access(access)
    can_func.__name__ = can
    setattr(User, can, permission(can_func))
