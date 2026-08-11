

import base64
import datetime
import hashlib
import logging
import re
import struct

import flask.json as json
from flask import current_app as app, g
from sqlalchemy import Column, ForeignKey, BigInteger, Integer, String, Boolean, TIMESTAMP, UniqueConstraint, func, desc, Text, Enum
from sqlalchemy.orm import relationship, validates, backref

from dim import db
from dim.errors import InvalidParameterError
from dim.models import get_session_username, Ipblock, WithAttr, TrackChanges
from dim.rrtype import RRType, RRMeta, validate_fqdn, validate_uint32
from dim.util import make_fqdn, strip_dot


class RegistrarAccount(db.Model):
    __tablename__ = 'registraraccount'

    AUTODNS3 = 'autodns3'

    id = Column(BigInteger, primary_key=True, nullable=False)
    name = Column(String(128), nullable=False, unique=True)
    plugin = Column(String(20), nullable=False)
    url = Column(String(255), nullable=False)
    username = Column(String(128), nullable=False)
    password = Column(String(128), nullable=False)
    subaccount = Column(String(128), nullable=False)


class Zone(db.Model, WithAttr, TrackChanges):
    id = Column(BigInteger, primary_key=True, nullable=False)
    name = Column(String(255), nullable=False, unique=True)
    profile = Column(Boolean, nullable=False)
    owner_id = Column(BigInteger, ForeignKey('usergroup.id', ondelete='SET NULL'), nullable=True)
    nsec3_algorithm = Column(Integer)
    nsec3_iterations = Column(Integer)
    nsec3_salt = Column(String(510))
    valid_begin = Column(TIMESTAMP, nullable=True)
    valid_end = Column(TIMESTAMP, nullable=True)
    registraraccount_id = Column(BigInteger, ForeignKey('registraraccount.id'), nullable=True)

    owner = relationship('Group')
    registrar_account = relationship(RegistrarAccount, backref=backref('zones'))

    @staticmethod
    def _candidates(name):
        sections = name.split('.')[:-1]
        candidates = ['.'.join(sections[i:]) for i in range(len(sections))]
        if name != '.':
            candidates += ['']
        return candidates

    @staticmethod
    def find(name):
        '''
        Returns the most specific zone matching `name` or None.
        '''
        return Zone.query.filter(Zone.name.in_(Zone._candidates(name))).order_by(desc(func.length(Zone.name))).first()

    @staticmethod
    def create(name, profile=False, attributes=None, owner=None):
        name = name.lower()
        try:
            validate_fqdn(None, 'zone', name + '.')
        except:
            raise InvalidParameterError('Invalid zone name: %s' % name)
        zone = Zone(name=name, profile=profile)
        db.session.add(zone)
        zone.set_attrs(attributes)
        zone.owner = owner
        return zone

    @property
    def display_name(self):
        return Zone.to_display_name(self.name)

    def __str__(self):
        return self.display_name

    @staticmethod
    def to_display_name(name):
        return '.' if name == '' else name

    @staticmethod
    def from_display_name(name):
        return '' if name == '.' else name

    @staticmethod
    def check_nsec3params(nsec3_algorithm, nsec3_iterations, nsec3_salt):
        if nsec3_algorithm not in (0, 1):
            raise InvalidParameterError('Invalid NSEC3 algorithm (must be 0 for disabled or 1 for sha1)')
        if not (0 <= nsec3_iterations <= 65535):
            raise InvalidParameterError('Invalid NSEC3 iteration count (must be between 0 and 65535)')
        if not (nsec3_salt == '-' or (re.match(r'^[0-9a-fA-F]+$', nsec3_salt) and len(nsec3_salt) <= 510)):
            raise InvalidParameterError('Invalid NSEC3 salt (must be a hexadecimal string or "-")')

    def set_nsec3params(self, nsec3_algorithm, nsec3_iterations, nsec3_salt):
        Zone.check_nsec3params(nsec3_algorithm, nsec3_iterations, nsec3_salt)

        self.nsec3_algorithm = nsec3_algorithm
        self.nsec3_iterations = nsec3_iterations
        self.nsec3_salt = nsec3_salt
        OutputUpdate.send_dnssec_update(self, self.views[0].nsec3param)

    @property
    def nsec3param(self):
        if self.nsec3_algorithm == 0:
            return ''
        else:
            return ' '.join(str(x) for x in
                            [self.nsec3_algorithm,
                             0,  # NSEC3 opt-out
                             self.nsec3_iterations,
                             self.nsec3_salt])

    def set_validity(self):
        self.valid_begin = datetime.datetime.utcnow()
        self.valid_end = self.valid_begin + datetime.timedelta(seconds=self.get_validity_window())

    def get_validity_window(self):
        attributes = self.get_attrs()
        return int(attributes.get('dnssec_validity_window', 3600 * 24 * 14))

    def is_signed(self):
        return len(self.keys) > 0

    def set_attrs(self, attrs):
        if not attrs:
            return
        self._check_dnssec_attrs(attrs)
        WithAttr.set_attrs(self, attrs)

    def _check_dnssec_attrs(self, attrs):
        def validate_int(value, min, message):
            try:
                value = int(value)
            except:
                raise InvalidParameterError('%s: %s' % (message, json.dumps(value)))
            if value < min:
                raise InvalidParameterError('%s: %s' % (message, json.dumps(value)))

        if 'default_algorithm' in attrs and str(attrs['default_algorithm']) != '8':
            raise InvalidParameterError('Algorithm can only be 8 (rsasha256)')
        if 'default_ksk_bits' in attrs:
            validate_int(attrs['default_ksk_bits'], 1, 'Invalid key length')
        if 'default_zsk_bits' in attrs:
            validate_int(attrs['default_zsk_bits'], 1, 'Invalid key length')
        if 'dnssec_validity_window' in attrs:
            validate_int(attrs['dnssec_validity_window'], 3600 * 24 * 4, 'Invalid dnssec validity window')

    def update_registrar_keys(self, keys):
        RegistrarAccountZoneKey.query.filter_by(zone_id=self.id).delete(synchronize_session=False)
        for key in keys:
            db.session.add(RegistrarAccountZoneKey(algorithm=key.algorithm,
                                                   pubkey=key.pubkey,
                                                   zone=key.zone))

    def has_registrar_keys(self):
        return RegistrarAccountZoneKey.query.filter_by(zone_id=self.id).count()


class ZoneAttrName(db.Model):
    id = Column(BigInteger, primary_key=True, nullable=False)
    name = Column(String(128), nullable=False, unique=True)

    # TODO remove aliases?
    reserved = ['name', 'created', 'created_by', 'modified', 'modified_by', 'aliases', 'alias_for',
                'nsec3_algorithm', 'nsec3_iterations', 'nsec3_salt', 'owner', 'department_number']


class ZoneAttr(db.Model):
    __table_constraints__ = (UniqueConstraint('name', 'zone'), )

    id = Column(BigInteger, primary_key=True, nullable=False)
    zone_id = Column('zone', BigInteger, ForeignKey('zone.id'), nullable=False)
    name_id = Column('name', BigInteger, ForeignKey('zoneattrname.id'), nullable=False)
    value = Column(String(255), nullable=False)

    zone = relationship(Zone, backref=backref('attributes', cascade='all, delete-orphan'))
    name = relationship(ZoneAttrName)


Zone.AttrNameClass = ZoneAttrName
Zone.AttrClass = ZoneAttr
Zone.attr_backref = 'zone'


class ZoneView(db.Model, TrackChanges):
    __table_constraints__ = (UniqueConstraint('name', 'zone_id'), )
    soa_attrs = ['ttl', 'primary', 'mail', 'serial', 'refresh', 'retry', 'expire', 'minimum']

    id = Column(BigInteger, primary_key=True, nullable=False)
    name = Column(String(255), nullable=False)
    zone_id = Column(BigInteger, ForeignKey('zone.id'), nullable=False)
    # SOA fields
    ttl = Column(Integer, nullable=False)
    primary = Column(String(255), nullable=False)
    mail = Column(String(255), nullable=False)
    serial = Column(BigInteger, nullable=False)
    refresh = Column(Integer, nullable=False)
    retry = Column(Integer, nullable=False)
    expire = Column(Integer, nullable=False)
    minimum = Column(BigInteger, nullable=False)

    zone = relationship(Zone, backref='views')

    @staticmethod
    def create(zone, name, from_profile=None, soa_attributes=None, copy_rrs=True):
        if from_profile:
            assert len(from_profile.views) == 1
            from_view = from_profile.views[0]
            fields = dict(
                ttl=from_view.ttl,
                primary=from_view.primary,
                mail=from_view.mail,
                refresh=from_view.refresh,
                retry=from_view.retry,
                expire=from_view.expire,
                minimum=from_view.minimum)
        else:
            fields = dict(
                primary='localhost.',
                mail='hostmaster.' + (zone.name or 'root') + '.',
                refresh=app.config['DNS_DEFAULT_REFRESH'],
                retry=app.config['DNS_DEFAULT_RETRY'],
                expire=app.config['DNS_DEFAULT_EXPIRE'],
                minimum=app.config['DNS_DEFAULT_MINIMUM'],
                ttl=app.config['DNS_DEFAULT_ZONE_TTL'])
        if soa_attributes:
            fields.update(soa_attributes)
        if not fields.get('serial', None):
            fields['serial'] = int(datetime.date.today().strftime("%Y%m%d01"))
        zoneview = ZoneView(name=name, zone=zone, **fields)
        if from_profile and copy_rrs:
            for rr in RR.query.filter_by(view=from_view):
                db.session.add(RR(name=make_fqdn(RR.record_name(rr.name, rr.view.zone.name), zone.name),
                                  view=zoneview,
                                  type=rr.type,
                                  ttl=rr.ttl,
                                  ipblock=rr.ipblock,
                                  target=rr.target,
                                  value=rr.value))
        db.session.add(zoneview)
        zone.update_modified()
        return zoneview

    validate_fqdn = validates('primary')(validate_fqdn)

    @validates('expire', 'ttl', 'minimum', 'refresh', 'retry')
    def validate_int32(self, key, value):
        value = int(value)
        if value < 0 or value > 2 ** 31 - 1:
            raise InvalidParameterError("Invalid %s: %d" % (key, value))
        return value

    validate_uint32 = validates('serial')(validate_uint32)

    def set_soa_attrs(self, attributes):
        # TODO what happens to outputs if we set the serial here?
        ttl_change = 'ttl' in attributes and int(attributes['ttl']) != self.ttl
        if ttl_change:
            rrs = RR.query.filter_by(view=self, ttl=None).all()
            for rr in rrs:
                OutputUpdate.send_rr_action(rr, OutputUpdate.DELETE_RR)
        for name, value in list(attributes.items()):
            setattr(self, name, value)
        if 'serial' not in attributes:
            self.update_serial()
        OutputUpdate.send_update_soa(self)
        if ttl_change:
            for rr in rrs:
                OutputUpdate.send_rr_action(rr, OutputUpdate.CREATE_RR)

    def update_serial(self):
        '''
        Increment the serial and call update_modified().

        Only update the serial by calling this function, otherwise outputs will
        not be correctly updated.
        '''
        self.serial += 1
        self.update_modified()

    @property
    def nsec3param(self):
        return self.zone.nsec3param

    def soa_value(self):
        return '{primary} {email} {serial} {refresh} {retry} {expire} {minimum}'.format(
            primary=self.primary,
            email=self.mail,
            serial=self.serial,
            refresh=self.refresh,
            retry=self.retry,
            expire=self.expire,
            minimum=self.minimum)

    @staticmethod
    def pdns_soa_value(value):
        primary, email, serial, refresh, retry, expire, minimum = value.split()
        return '{primary} {email} {serial} {refresh} {retry} {expire} {minimum}'.format(
            primary=strip_dot(primary),
            email=strip_dot(email),
            serial=serial,
            refresh=refresh,
            retry=retry,
            expire=expire,
            minimum=minimum)

    @property
    def outputs(self):
        return Output.query.join(Output.groups).join(ZoneGroup.views).filter(ZoneView.id == self.id)

    def __str__(self):
        zone_object = 'zone profile' if self.zone.profile else 'zone'
        if len(self.zone.views) == 1:
            return '{0} {1}'.format(zone_object, self.zone.display_name)
        else:
            return '{0} {1} view {2}'.format(zone_object, self.zone.display_name, self.name)

    @property
    def display_name(self):
        return self.name


class FavoriteZoneView(db.Model):
    zoneview_id = Column(BigInteger, ForeignKey('zoneview.id'), primary_key=True)
    user_id = Column(BigInteger, ForeignKey('user.id'), primary_key=True)

    user = relationship('User', uselist=False, backref=backref('favorite_zoneviews',
                                                               cascade='all, delete-orphan'))
    zoneview = relationship('ZoneView', uselist=False, backref=backref('favorited_by',
                                                                       cascade='all, delete-orphan',
                                                                       collection_class=set))


class RR(db.Model, TrackChanges):
    id = Column(BigInteger, primary_key=True, nullable=False)
    type = Column(String(10), nullable=False, index=True)
    ttl = Column(Integer)
    zoneview_id = Column(BigInteger, ForeignKey('zoneview.id'), nullable=False)
    name = Column(String(254), nullable=False, index=True)
    comment = Column(String(255), nullable=True, index=False)
    value = Column(Text(65535), nullable=False)
    target = Column(String(255), nullable=True, index=True)
    ipblock_id = Column(BigInteger, ForeignKey('ipblock.id'), nullable=True)

    ipblock = relationship(Ipblock)
    view = relationship(ZoneView)

    @staticmethod
    def get_class(rr_type):
        for c in RRType.__subclasses__():
            if c.__name__ == rr_type:
                return c
        raise InvalidParameterError('Invalid rr type: %s' % rr_type)

    @staticmethod
    def validate_args(type, **kwargs):
        rr_class = RR.get_class(type)
        try:
            for field, validate in list(rr_class.validate.items()):
                kwargs[field] = validate(None, field, kwargs[field], **({i:kwargs[i] for i in kwargs if i != field}))
        except ValueError as e:
            raise InvalidParameterError(str(e))
        return kwargs

    @staticmethod
    def create(name, type, view, ttl, comment=None, **kwargs):
        if type == 'SRV':
            labels = name.split('.')
            if len(labels) < 2 or not labels[0].startswith('_') or not labels[1].startswith('_'):
                raise InvalidParameterError('SRV records must start with two _ labels service and proto')
        kwargs = RR.validate_args(type, **kwargs)
        rr = RR(name=name, type=type, view=view, ttl=ttl, comment=comment)
        for field in list(kwargs.keys()):
            if field == 'ip':
                rr.ipblock = kwargs['ip']
            elif field in RRType.target_fields:
                rr.target = make_fqdn(kwargs[field], view.zone.name)
        rr.value = RR.get_class(type).value_from_fields(**kwargs)
        return rr

    def insert(self):
        db.session.add(self)
        self.notify_insert()

    def delete(self, send_delete_rr_event=True):
        db.session.delete(self)
        self.notify_delete(send_delete_rr_event)

    def set_ttl(self, ttl):
        OutputUpdate.send_rr_action(self, OutputUpdate.DELETE_RR)
        self.ttl = ttl
        self.view.update_serial()
        OutputUpdate.send_rr_action(self, OutputUpdate.CREATE_RR)

    def set_comment(self, comment):
        self.comment = comment
        self.update_modified()

    def notify_insert(self):
        self.view.update_serial()
        OutputUpdate.send_rr_action(self, OutputUpdate.CREATE_RR)

    def notify_delete(self, send_delete_rr_event=True):
        self.view.update_serial()
        if send_delete_rr_event:
            OutputUpdate.send_rr_action(self, OutputUpdate.DELETE_RR)
        else:
            OutputUpdate.send_update_soa(self.view)

    @staticmethod
    def record_name(rr_name, zone_name):
        assert rr_name.endswith(zone_name + '.')
        if rr_name == zone_name + '.':
            return '@'
        else:
            if zone_name != '':
                return rr_name[:-len(zone_name) - 2]
            return rr_name[:-1]

    def bind_str(self, relative=False):
        if relative:
            name = RR.record_name(self.name, self.view.zone.name)
        else:
            name = self.name
        if self.ttl:
            return ' '.join([name, str(self.ttl), self.type, self.value])
        else:
            return ' '.join([name, self.type, self.value])

    def __str__(self):
        return self.bind_str()

    @validates('name')
    def validate_name(self, key, value):
        if value.startswith('*.'):
            validate_fqdn(self, key, value[2:])
        else:
            validate_fqdn(self, key, value)
        return value.lower()

    @validates('ttl')
    def validate_ttl(self, key, value):
        if value is None:
            return None
        else:
            try:
                return int(value)
            except:
                raise InvalidParameterError('Invalid %s: %s' % (key, value))


outputzonegroup_table = db.Table(
    'outputzonegroup',
    Column('output_id', BigInteger, ForeignKey('output.id')),
    Column('zonegroup_id', BigInteger, ForeignKey('zonegroup.id')),
    **db.Model.default_table_kwargs)


zonegroupzoneview_table = db.Table(
    'zonegroupzoneview',
    Column('zonegroup_id', BigInteger, ForeignKey('zonegroup.id')),
    Column('zoneview_id', BigInteger, ForeignKey('zoneview.id')),
    **db.Model.default_table_kwargs)


class RegistrarAccountZoneKey(db.Model):
    id = Column(BigInteger, primary_key=True, nullable=False)
    zone_id = Column(BigInteger, ForeignKey('zone.id'), nullable=False)
    algorithm = Column(Integer, nullable=False)
    pubkey = Column(Text(65535), nullable=False)  # base64-encoded public key

    zone = relationship(Zone, backref=backref('registrar_keys', cascade='all, delete-orphan'))


class RegistrarAction(db.Model):
    id = Column(BigInteger, primary_key=True, nullable=False)
    zone_id = Column(BigInteger, ForeignKey('zone.id'), nullable=False)
    status = Column(Enum('pending', 'running', 'done', 'failed', 'unknown'), nullable=False)
    error = Column(Text(65535), nullable=True)
    started = Column(TIMESTAMP, nullable=True)
    completed = Column(TIMESTAMP, nullable=True)

    zone = relationship(Zone, backref=backref('registrar_actions', cascade='all, delete-orphan'))


class ZoneKey(db.Model):
    __table_constraints__ = (UniqueConstraint('zone_id', 'label'), )

    id = Column(BigInteger, primary_key=True, nullable=False)
    bits = Column(Integer, nullable=False)
    type = Column(Enum('ksk', 'zsk'), nullable=False)
    label = Column(String(255), nullable=False)
    algorithm = Column(Integer, nullable=False)
    pubkey = Column(Text(65535), nullable=False)  # base64-encoded public key
    privkey = Column(Text(65535), nullable=False)  # private key
    zone_id = Column(BigInteger, ForeignKey('zone.id'), nullable=False)
    created = Column(TIMESTAMP, nullable=False, default=datetime.datetime.utcnow, server_default='1970-01-02 00:00:01')
    registraraction_id = Column(BigInteger, ForeignKey('registraraction.id'), nullable=True)

    zone = relationship(Zone, backref=backref('keys', cascade='all, delete-orphan'))
    registrar_action = relationship(RegistrarAction, backref=backref('keys'))

    @property
    def flags(self):
        return 257 if self.type == 'ksk' else 256

    def rdata(self):
        protocol = 3
        return dnskey_rdata(self.flags, protocol, self.algorithm, self.pubkey)

    def tag(self):
        if self.pubkey:
            return dnskey_tag(self.rdata())

    def ds(self, digest_type):
        hfunc = {1: hashlib.sha1,
                 2: hashlib.sha256,
                 4: hashlib.sha384}
        if digest_type not in hfunc:
            raise InvalidParameterError('Invalid DS digest type: %s' % digest_type)
        return ds_hash(self.zone.name.encode('utf-8'), self.rdata(), hfunc[digest_type])

    def ds_rr_params(self, digest_type):
        return dict(name=self.zone.name + '.', rr_type='DS', key_tag=self.tag(), algorithm=self.algorithm,
                    digest_type=digest_type, digest=self.ds(digest_type))


def dnskey_rdata(flags, protocol, algorithm, pubkey):
    return struct.pack('!HBB', flags, protocol, algorithm) + base64.b64decode(pubkey)


def dnskey_tag(rdata):
    ac = 0
    for i, c in enumerate(rdata):
        ac += c if i & 1 else c << 8
    ac += (ac >> 16) & 0xFFFF
    return ac & 0xFFFF


def ds_hash(owner: bytes, rdata: bytes, digest_function):
    if type(owner) != bytes:
        raise TypeError("owner must be of type bytes, got {}".format(type(owner)))
    if type(rdata) != bytes:
        raise TypeError("rdata must be of type bytes, got {}".format(type(rdata)))

    parts = owner.lower().split(b'.')
    canon = []
    for p in parts:
        canon.append(bytes([len(p)]))
        canon.append(p)
    canon.append(bytes([0]))
    raw = digest_function(b''.join(canon) + rdata)
    digest = raw.hexdigest()
    result = digest.upper()
    return result


class Output(db.Model, TrackChanges):
    __tablename__ = 'output'

    PDNS_DB = 'pdns-db'
    BIND = 'bind'
    PLUGINS = {BIND: (),
               PDNS_DB: ('db_uri', )}

    id = Column(BigInteger, primary_key=True, nullable=False)
    name = Column(String(255), nullable=False, unique=True)
    plugin = Column(String(20), nullable=False)
    # plugin pdns & bind
    comment = Column(String(255), nullable=True, index=False)
    db_uri = Column(String(255), nullable=True, index=False)
    last_run = Column(TIMESTAMP, nullable=False, server_default='1970-01-02 00:00:01')
    status = Column(String(255), nullable=True)

    groups = relationship("ZoneGroup",
                          secondary=outputzonegroup_table,
                          backref="outputs")

    def update_status(self, status):
        self.status = status[:255]
        self.last_run = datetime.datetime.utcnow()


class ZoneGroup(db.Model, TrackChanges):
    __tablename__ = 'zonegroup'

    id = Column(BigInteger, primary_key=True, nullable=False)
    name = Column(String(255), nullable=False, unique=True)
    comment = Column(String(255), nullable=True, index=False)
    views = relationship("ZoneView",
                         secondary=zonegroupzoneview_table,
                         backref="groups")

    def __str__(self):
        return self.name


class OutputUpdate(db.Model):
    __tablename__ = 'outputupdate'

    CREATE_RR = 'create_rr'
    DELETE_RR = 'delete_rr'
    UPDATE_SOA = 'update_soa'
    CREATE_ZONE = 'create_zone'
    REFRESH_ZONE = 'refresh_zone'
    DELETE_ZONE = 'delete_zone'
    DNSSEC_UPDATE = 'dnssec_update'
    valid_actions = (CREATE_RR, DELETE_RR, UPDATE_SOA, CREATE_ZONE, REFRESH_ZONE, DELETE_ZONE,
                     DNSSEC_UPDATE)

    # Rows per INSERT when fanning a whole zone out to an output; see
    # _bulk_send_create_rrs().
    BULK_ROWS_PER_INSERT = 5000

    # update metadata
    id = Column(BigInteger, primary_key=True, nullable=False)
    transaction = Column(String(16))
    action = Column(String(15), nullable=False)
    output_id = Column(BigInteger, ForeignKey('output.id'), nullable=False)

    # update data
    zone_name = Column(String(255), nullable=False)
    serial = Column(BigInteger)
    name = Column(String(255))
    ttl = Column(Integer)
    type = Column(String(10))
    content = Column(Text(65535))

    output = relationship(Output, backref=backref('updates', cascade='all, delete-orphan'))

    def __str__(self):
        return ('output={0.output.name}, tid={0.transaction} zone={0.zone_name}, serial={0.serial}: ' +
                '{0.action} {0.name} {0.ttl} {0.type} {0.content}').format(self)

    def __init__(self, **kwargs):
        super(OutputUpdate, self).__init__(**kwargs)
        self.transaction = g.tid
        logging.info('Sending update %s' % self)
        assert self.action in self.valid_actions

    @staticmethod
    def _send_create_zone(view, zone_name, output, action):
        db.session.add(OutputUpdate(
            action=action,
            output=output,
            zone_name=zone_name,
            serial=view.serial,
            name=zone_name,
            ttl=view.ttl,
            type='SOA',
            content=view.soa_value()))
        # Flush so the row above keeps the lower id: updates are consumed
        # ordered by id, and the zone has to appear before its records. This
        # also assigns output.id, which the bulk insert below needs.
        db.session.flush()
        OutputUpdate._bulk_send_create_rrs(view, zone_name, output)
        if view.zone.keys:
            OutputUpdate._send_dnssec_update_to_output("add keys " + ' '.join([key.label for key in view.zone.keys]),
                                                       zone_name, output)

    @staticmethod
    def _bulk_send_create_rrs(view, zone_name, output):
        '''Insert a CREATE_RR update for every RR in *view*, in batches.

        Deliberately bypasses the ORM. One OutputUpdate instance per RR means
        one INSERT round trip per RR, because SQLAlchemy needs the generated id
        of every instance and cannot batch those. Locally that is invisible,
        but at a few ms of network latency a zone with a few hundred thousand
        records takes tens of minutes to land in outputupdate.

        Selecting plain columns instead of RR instances also keeps the identity
        map from growing to one object per record.
        '''
        rr_zone_name = view.zone.name
        total = 0
        last_id = 0
        while True:
            # Paginate by id rather than streaming the whole select: MySQLdb
            # refuses a new statement while an unread result set is open on the
            # same connection, and the insert below runs on that connection.
            batch = db.session.query(RR.id, RR.name, RR.ttl, RR.type, RR.value)\
                .filter(RR.zoneview_id == view.id)\
                .filter(RR.id > last_id)\
                .order_by(RR.id)\
                .limit(OutputUpdate.BULK_ROWS_PER_INSERT).all()
            if not batch:
                break
            last_id = batch[-1][0]
            db.session.execute(OutputUpdate.__table__.insert(), [
                dict(transaction=g.tid,
                     action=OutputUpdate.CREATE_RR,
                     output_id=output.id,
                     zone_name=zone_name,
                     serial=view.serial,
                     name=make_fqdn(RR.record_name(name, rr_zone_name), zone_name),
                     # `or` rather than a None check to keep the pre-batching
                     # behaviour: a ttl of 0 falls back to the zone ttl
                     ttl=ttl or view.ttl,
                     type=rr_type,
                     content=value)
                for _id, name, ttl, rr_type, value in batch])
            total += len(batch)
        logging.info('Sent %d %s updates for zone %s to output %s',
                     total, OutputUpdate.CREATE_RR, zone_name, output.name)

    @staticmethod
    def _send_delete_zone(zone_name, output):
        db.session.add(OutputUpdate(action=OutputUpdate.DELETE_ZONE, zone_name=zone_name, output=output))

    @staticmethod
    def _send_rr_action_to_zone(rr, action, zone_name, output):
        db.session.add(OutputUpdate(
            action=action,
            output=output,
            zone_name=zone_name,
            serial=rr.view.serial,
            name=make_fqdn(RR.record_name(rr.name, rr.view.zone.name), zone_name),
            ttl=rr.ttl or rr.view.ttl,
            type=rr.type,
            content=rr.value))

    @staticmethod
    def _send_update_soa(view, zone_name, output):
        db.session.add(OutputUpdate(
            action=OutputUpdate.UPDATE_SOA,
            output=output,
            zone_name=zone_name,
            serial=view.serial,
            name=zone_name,
            ttl=view.ttl,
            type='SOA',
            content=view.soa_value()))

    @staticmethod
    def send_rr_action(rr, action):
        for output in rr.view.outputs:
            OutputUpdate._send_rr_action_to_zone(rr, action, rr.view.zone.name, output)

    @staticmethod
    def send_create_view(view, output, action):
        OutputUpdate._send_create_zone(view, view.zone.name, output, action)

    @staticmethod
    def send_delete_view(view, output):
        OutputUpdate._send_delete_zone(view.zone.name, output)

    @staticmethod
    def send_update_soa(view):
        for output in view.outputs:
            OutputUpdate._send_update_soa(view, view.zone.name, output)

    @staticmethod
    def _send_dnssec_update_to_output(content, zone_name, output):
        db.session.add(OutputUpdate(
            action=OutputUpdate.DNSSEC_UPDATE,
            output=output,
            zone_name=zone_name,
            content=content))

    @staticmethod
    def send_dnssec_update(zone, content):
        for view in zone.views:
            for output in view.outputs:
                OutputUpdate._send_dnssec_update_to_output(content, zone.name, output)
