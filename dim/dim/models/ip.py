import datetime
import logging
import re

from sqlalchemy import Column, BigInteger, Integer, String, Numeric, TIMESTAMP, ForeignKey, UniqueConstraint
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import relationship, backref, validates, synonym
from sqlalchemy.sql import bindparam, or_, between, func, expression, text
from sqlalchemy.types import DateTime

from dim import db
from dim.errors import InvalidParameterError
from dim.ipaddr import IP
from dim.iptrie import IPTrie
from dim.models import WithAttr, TrackChanges, get_session_username
from dim.rrtype import validate_uint32


class minus_days(expression.FunctionElement):
    type = DateTime()
    name = 'minus_days'


@compiles(minus_days, 'mysql')
def mysql_minus_days(element, compiler, **kw):
    date, days = list(element.clauses)
    return "DATE_SUB(%s, INTERVAL %s DAY)" % (compiler.process(date),
                                              compiler.process(days))


class Vlan(db.Model):
    id = Column(BigInteger, primary_key=True, nullable=False)
    vid = Column(Integer, unique=True, nullable=False)

    @property
    def display_name(self):
        return self.vid


class IpblockStatus(db.Model):
    id = Column(BigInteger, primary_key=True, nullable=False)
    name = Column(String(64), nullable=False, unique=True)

    @property
    def display_name(self):
        return self.name


class Layer3Domain(db.Model, TrackChanges):
    VRF = 'vrf'
    TYPES = {VRF: ('rd', )}

    id = Column(BigInteger, primary_key=True, nullable=False)
    name = Column(String(128), nullable=False, unique=True)
    type = Column(String(20), nullable=False)
    comment = Column(String(255), nullable=True, index=False)
    # Vrf
    rd = Column(BigInteger, nullable=True, unique=True)

    validate_uint32 = validates('rd')(validate_uint32)

    @property
    def display_name(self):
        return self.name

    @property
    def display_rd(self):
        return '%d:%d' % (self.rd >> 16, self.rd & 0xFFFF)

    def set_comment(self, comment):
        self.comment = comment
        self.update_modified()

    def set_rd(self, rd_str):
        if self.type != Layer3Domain.VRF:
            raise InvalidParameterError('Layer3domain %s is not of type vrf' % self.name)
        rd = Layer3Domain.parse_rd(rd_str)
        if Layer3Domain.query.filter_by(rd=rd).count() > 0:
            raise InvalidParameterError('Layer3domain with type vrf rd %s already exists' % rd_str)
        self.rd = rd

    @staticmethod
    def parse_rd(rd):
        m = re.match(r'(\d{1,5}):(\d{1,5})', rd)
        if m is None:
            raise InvalidParameterError("Invalid rd '%s'" % rd)

        def validate_uint16(s):
            value = int(s)
            if value < 0 or value > 2 ** 16 - 1:
                raise InvalidParameterError("Invalid rd '%s'" % rd)
            return value

        a = validate_uint16(m.group(1))
        b = validate_uint16(m.group(2))
        return (a << 16) + b


class Pool(db.Model, WithAttr):
    __tablename__ = 'ippool'

    id = Column(BigInteger, primary_key=True, nullable=False)
    name = Column(String(128), unique=True, nullable=False)
    version = Column(Integer, index=True, default=0, nullable=False)  # TODO allow null
    description = Column(String(128))
    vlan_id = Column(BigInteger, ForeignKey('vlan.id'))
    created = Column(TIMESTAMP, nullable=False,
                     default=datetime.datetime.utcnow,
                     server_default='1970-01-02 00:00:01')
    modified = Column(TIMESTAMP, nullable=False,
                      default=datetime.datetime.utcnow,
                      onupdate=datetime.datetime.utcnow,
                      server_default='1970-01-02 00:00:01')
    modified_by = Column(String(128),
                         default=get_session_username,
                         onupdate=get_session_username)
    owner_id = Column(BigInteger, ForeignKey('usergroup.id', ondelete='SET NULL'), nullable=True)
    layer3domain_id = Column(BigInteger, ForeignKey('layer3domain.id'), nullable=False)

    vlan = relationship(Vlan)
    owner = relationship('Group')
    layer3domain = relationship(Layer3Domain)
    ipblocks = relationship("Ipblock", backref="pool", order_by='Ipblock.priority')
    subnets = synonym('ipblocks')

    @property
    def display_name(self):
        return self.name

    def __str__(self):
        return self.name

    @validates('name')
    def validate_name(self, key, name):
        if not re.match(r'^[A-Za-z0-9][-_A-Za-z0-9]*$', name):
            raise ValueError("Invalid pool name: '%s'" % name)
        return name

    def __contains__(self, ip):
        if isinstance(ip, Ipblock):
            if ip.pool == self:
                return True
            ip = ip.ip
        for subnet in self.subnets:
            if ip in subnet.ip:
                return True
        return False

    def update_modified(self):
        '''
        Update the modified and modified_by fields.

        This only needs to be called when a change to the pool doesn't modify
        any columns in the ippool table.
        '''
        self.modified = datetime.datetime.utcnow()
        self.modified_by = get_session_username()

    @property
    def total_ips(self) -> int:
        return sum(s.total for s in self.subnets)

    @property
    def used_ips(self) -> int:
        return sum(s.used for s in self.subnets)

    def allocation_history(self, days_ago):
        return AllocationHistory.query\
            .filter_by(pool=self)\
            .filter(func.date(AllocationHistory.date) == func.date(minus_days(func.now(), days_ago)))\
            .order_by(AllocationHistory.date)\
            .first()


class PoolAttrName(db.Model):
    __tablename__ = 'ippoolattrname'

    id = Column(BigInteger, primary_key=True, nullable=False)
    name = Column(String(128), nullable=False, unique=True)

    reserved = ['name', 'vlan', 'description', 'version', 'created', 'modified', 'modified_by', 'layer3domain']


class PoolAttr(db.Model):
    __tablename__ = 'ippoolattr'
    __table_constraints__ = (UniqueConstraint('name', 'ippool_id'), )

    id = Column(BigInteger, primary_key=True, nullable=False)
    ippool_id = Column(BigInteger, ForeignKey('ippool.id'), nullable=False)
    name_id = Column('name', BigInteger, ForeignKey('ippoolattrname.id'), nullable=False)
    value = Column(String(255), nullable=False)

    pool = relationship(Pool, backref=backref('attributes', cascade='all, delete-orphan'))
    name = relationship(PoolAttrName)


Pool.AttrNameClass = PoolAttrName
Pool.AttrClass = PoolAttr
Pool.attr_backref = 'pool'


class FavoritePool(db.Model):
    __tablename__ = 'favoriteippool'

    ippool_id = Column(BigInteger, ForeignKey('ippool.id'), primary_key=True)
    user_id = Column(BigInteger, ForeignKey('user.id'), primary_key=True)

    user = relationship('User', uselist=False, backref=backref('favorite_pools',
                                                               cascade='all, delete-orphan'))
    pool = relationship('Pool', uselist=False, backref=backref('favorited_by',
                                                               cascade='all, delete-orphan',
                                                               collection_class=set))


class AllocationHistory(db.Model):
    id = Column(BigInteger, primary_key=True, nullable=False)
    date = Column(TIMESTAMP, nullable=False)
    ippool_id = Column(BigInteger, ForeignKey('ippool.id'), nullable=False)
    total_ips = Column(Numeric(precision=40, scale=0), nullable=False)
    used_ips = Column(Numeric(precision=40, scale=0), nullable=False)

    pool = relationship(Pool, backref=backref('allocationhistory', cascade='all, delete-orphan'))

    @staticmethod
    def collect_data():
        for pool in Pool.query.all():
            db.session.add(
                AllocationHistory(
                    pool=pool,
                    total_ips=pool.total_ips,
                    used_ips=pool.used_ips))


class Ipblock(db.Model, WithAttr, TrackChanges):
    # 'version' is part of a block's identity: 0.0.0.0/0 and ::/0 both have
    # address 0 and prefix 0, so without it they collide inside one layer3domain.
    # It is appended so that the (address, prefix, layer3domain_id) index prefix
    # stays intact -- ip.py hints this index by name in _set_parent().
    __table_constraints__ = (UniqueConstraint('address', 'prefix', 'layer3domain_id', 'version'), )

    id = Column(BigInteger, primary_key=True, nullable=False)
    version = Column(Integer, index=True, nullable=False)
    address = Column(Numeric(precision=40, scale=0), nullable=False)
    prefix = Column(Integer, nullable=False)
    priority = Column(Integer)
    gateway = Column(Numeric(precision=40, scale=0))
    parent_id = Column(BigInteger, ForeignKey('ipblock.id'))
    status_id = Column(BigInteger, ForeignKey('ipblockstatus.id'))
    ippool_id = Column(BigInteger, ForeignKey('ippool.id'))
    vlan_id = Column(BigInteger, ForeignKey('vlan.id'))
    layer3domain_id = Column(BigInteger, ForeignKey('layer3domain.id'), nullable=False)

    # relationships
    parent = relationship('Ipblock', remote_side=[id],
                          backref=backref('children', lazy='dynamic', order_by='Ipblock.address'))
    status = relationship(IpblockStatus)
    vlan = relationship(Vlan)
    layer3domain = relationship(Layer3Domain)

    def __str__(self):
        return str(self.ip)

    def label(self, expanded=False):
        return self.ip.label(expanded)

    @property
    def ip(self):
        return IP(int(self.address), self.prefix, self.version)

    @property
    def gateway_ip(self):
        return IP(int(self.gateway), version=self.version)

    @property
    def is_host(self):
        return self.ip.is_host

    @property
    def free(self):
        return self.total - self.used

    @property
    def total(self):
        return self.ip.numhosts

    def _used(self, only_static) -> int:
        ret: int = 0
        q = db.session.query(Ipblock.prefix, func.count()).filter(Ipblock.parent == self)
        if only_static:
            q = q.join(IpblockStatus).filter(IpblockStatus.name == 'Static')
        for prefix, count in q.group_by(Ipblock.prefix).all():
            ret += count * 2 ** (self.ip.bits - prefix)
        return ret

    @property
    def used(self):
        return self._used(only_static=False)

    @property
    def static(self):
        return self._used(only_static=True)

    @property
    def subnet(self):
        return self._ancestor('Subnet')

    @property
    def delegation(self):
        return self._ancestor('Delegation')

    @staticmethod
    def free_ranges(block, children):
        '''Return ranges of free addresses inside block as tuples (start_address, stop_address)'''
        start = block.ip.address
        for child in children:
            if start <= child.ip.address - 1:
                yield start, child.ip.address - 1
            start = child.ip.broadcast.address + 1
        if start <= block.ip.broadcast.address:
            yield start, block.ip.broadcast.address

    @property
    def free_space(self):
        '''
        Returns the shortest list of IP objects that cover all the free space.
        '''
        def max_hostbits(address, max_):
            '''Return the maximum number of host bits, but no more than max_'''
            hb = 0
            mask = 1
            while address & mask == 0 and hb < max_:
                hb += 1
                mask *= 2
            return hb

        def fill(range_start, range_end):
            '''
            Fill the free space between range_start and range_end with the least
            amount of blocks.
            '''
            if range_start > range_end:
                return []
            hb = max_hostbits(range_start, bits - self.prefix)
            while range_start + 2 ** hb - 1 > range_end:
                hb -= 1
            return [IP(range_start, bits - hb, self.version)] + fill(range_start + 2 ** hb, range_end)

        bits = self.ip.bits
        result = []
        for (start, stop) in Ipblock.free_ranges(self, self.children.all()):
            result.extend(fill(start, stop))
        return result

    def __contains__(self, item):
        if isinstance(item, Ipblock):
            return item.ip in self.ip
        else:
            return item in self.ip

    @staticmethod
    def create(*args, **kwargs):
        ipblock = Ipblock(*args, **kwargs)
        db.session.add(ipblock)
        ipblock._tree_update()
        ipblock._validate()
        return ipblock

    def delete(self):
        logging.debug("Deleting %s" % self)
        if not self.is_host:
            logging.debug('Updating parents for children of %s' % self)
            Ipblock.query.filter_by(parent=self).update({'parent_id': self.parent_id})
        db.session.delete(self)

    def delete_subtree(self):
        logging.debug("Deleting subtree %s" % self)
        # Because the children relationship is dynamic, we have to manually sort
        # the deletes to satisfy fk constraints
        for block in Ipblock.query\
                .filter(Ipblock.version == self.version)\
                .filter(Ipblock.layer3domain_id == self.layer3domain_id)\
                .filter(Ipblock.prefix >= self.prefix)\
                .filter(between(Ipblock.address, self.ip.network.address, self.ip.broadcast.address))\
                .order_by(Ipblock.prefix.desc()).all():
            db.session.delete(block)

    @staticmethod
    def query_ip(ip, layer3domain, status=None):
        filters = {}
        if layer3domain is not None:
            filters['layer3domain'] = layer3domain
        if status is not None:
            from dim.rpc import get_status
            filters['status'] = get_status(status)
        return Ipblock.query.filter_by(address=ip.address,
                                       prefix=ip.prefix,
                                       version=ip.version,
                                       **filters)

    @staticmethod
    def build_tree_mem(layer3domain, version):
        logging.debug('build_tree_mem(%s, %d)' % (layer3domain.name, version))
        new_parents = []
        blocks = db.session.query(Ipblock.id,
                                  Ipblock.address,
                                  Ipblock.prefix,
                                  Ipblock.parent_id)\
            .filter(Ipblock.version == version, Ipblock.layer3domain_id == layer3domain.id)\
            .order_by(Ipblock.prefix).all()
        tree = IPTrie(version)
        for id, address, prefix, parent in blocks:
            ip = IP(int(address), prefix, version)
            if prefix == tree._bits:
                new_parent = tree.parent(ip)
            else:
                new_parent = tree.insert(ip, id)
            new_parent_id = new_parent.data if new_parent else None
            if new_parent_id != parent:
                new_parents.append((id, new_parent_id))
        return tree, new_parents

    @staticmethod
    def build_tree_parents(layer3domain, version):
        tree, new_parents = Ipblock.build_tree_mem(layer3domain, version)
        if new_parents:
            logging.warning('%d wrong parents found during tree rebuild in layer3domain %s version %d' % (
                len(new_parents), layer3domain.name, version))
            update = Ipblock.__table__.update() \
                .where(Ipblock.id == bindparam('_id')) \
                .values(parent_id=bindparam('_parent'))
            db.session.execute(update, params=[dict(_id=id, _parent=parent)
                                               for id, parent in new_parents])
            db.session.expire_all()
        return tree

    def _tree_update(self):
        db.session.flush()      # we need self.id
        logging.debug('Updating tree for %s', self)
        new_parent_id = None
        if self.ip.prefix !=0:
            ancestors = Ipblock._ancestors_noparent(self.ip, self.layer3domain)
            new_parent_id = ancestors[0].id if ancestors else None
        if self.parent_id != new_parent_id:
            self.parent_id = new_parent_id
        if not self.is_host:
            # TODO patch sqlalchemy to support with_hint for update statements
            Ipblock.query\
                .with_hint(Ipblock, 'USE INDEX (address)', 'mysql')\
                .filter(Ipblock.parent_id == self.parent_id)\
                .filter(Ipblock.prefix > self.prefix)\
                .filter(Ipblock.address >= self.ip.network.address)\
                .filter(Ipblock.address <= self.ip.broadcast.address)\
                .update({'parent_id': self.id})

    def _validate(self):
        status = self.status.name
        if self.is_host:
            if status in ('Container', 'Subnet', 'Delegation'):
                raise Exception("Creating %s blocks with only one IP addresss is not allowed" % status)
        else:
            if status in ('Available', 'Static'):
                raise Exception("%s blocks must be host addresses" % status)

        if self.parent and self.parent.status:
            pstatus = self.parent.status.name
            if self.is_host:
                if pstatus == 'Reserved':
                    raise Exception(str(self) + ": Address allocations not allowed under Reserved blocks")
            else:
                if status == 'Delegation':
                    if pstatus != 'Subnet':
                        raise Exception("Delegation blocks only allowed under Subnet blocks: %s %s under %s %s"
                                        % (self.status.name, self, pstatus, self.parent))
                elif pstatus != 'Container':
                    raise Exception("Block allocations only allowed under Container blocks: %s under %s" % (self, self.parent))

        if status == 'Subnet':
            if not self.parent:
                raise Exception('Subnet %s has no parent container' % self)
            # We only want addresses or Delegation inside a subnet.
            if self.children.join(IpblockStatus).filter(~or_(Ipblock.prefix == self.ip.bits, IpblockStatus.name == 'Delegation')).count():
                raise Exception("Subnet %s cannot be created because it would have non-Delegation blocks as children" % self)
        elif status == 'Reserved':
            if self.children.count():
                raise Exception("%s: Reserved blocks can't contain other blocks" % self)
        elif status == 'Delegation':
            if self.parent is None:
                raise Exception("%s: Delegation blocks only allowed under Subnet blocks" % self)

    def _ancestor(self, status_name):
        last = node = self
        while True:
            if node.status.name == status_name:
                return node
            node = node.parent
            if node is None:
                return None
            if last not in node:
                # TODO cached_tree rebuild?
                # TODO do this elsewhere too?
                logging.warn('Invalid parent for %s: %s' % (last, node))
                ancestors = Ipblock._ancestors_noparent(self.ip, self.layer3domain)
                self.parent = ancestors[0] if ancestors else None

    @staticmethod
    def _ancestors_noparent(ip, layer3domain, include_self=False):
        return Ipblock._ancestors_noparent_query(ip, layer3domain, include_self).all()

    @staticmethod
    def _ancestors_noparent_condition(ip, include_self=False):
        # use raw sql because sqlalchemy is slow at building large queries
        f = " OR ".join(('address=%d AND prefix=%d' %
                         (ip.address & ((2 ** ip.bits - 1) ^ (2 ** (ip.bits - prefix) - 1)), prefix)
                         for prefix in range(ip.prefix + (1 if include_self else 0))))
        return text('(' + f + ')')

    @staticmethod
    def _ancestors_noparent_query(ip, layer3domain, include_self=False):
        q = Ipblock.query.filter_by(version=ip.version)
        if layer3domain is not None:
            q = q.filter_by(layer3domain=layer3domain)
        return q.filter(Ipblock._ancestors_noparent_condition(ip, include_self=include_self)) \
            .order_by(Ipblock.prefix.desc())


class IpblockAttrName(db.Model):
    id = Column(BigInteger, primary_key=True, nullable=False)
    name = Column(String(128), nullable=False, unique=True)

    reserved = ['ip', 'vlan', 'description', 'created', 'modified', 'modified_by', 'status',
                'subnet', 'delegation', 'mask', 'prefixlen', 'pool', 'ptr_target']


class IpblockAttr(db.Model):
    __table_constraints__ = (UniqueConstraint('name', 'ipblock'), )

    id = Column(BigInteger, primary_key=True, nullable=False)
    ipblock_id = Column('ipblock', BigInteger, ForeignKey('ipblock.id'), nullable=False)
    name_id = Column('name', BigInteger, ForeignKey('ipblockattrname.id'), nullable=False)
    value = Column(String(255), nullable=False)

    ipblock = relationship(Ipblock, backref=backref('attributes', cascade='all, delete-orphan'))
    name = relationship(IpblockAttrName)


Ipblock.AttrNameClass = IpblockAttrName
Ipblock.AttrClass = IpblockAttr
Ipblock.attr_backref = 'ipblock'
