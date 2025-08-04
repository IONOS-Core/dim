

import logging
import re

from sqlalchemy import or_, and_, func
from sqlalchemy.orm import aliased

from dim import db
from dim.errors import InvalidParameterError, InvalidZoneError, InvalidViewError, DimError, MultipleViewsError, AlreadyExistsError
from dim.ipaddr import IP
from dim.messages import Messages
from dim.models import Ipblock, RR, Zone, ZoneView, ZoneGroup, Output, OutputUpdate, AccessRight, Layer3Domain
from dim.util import make_fqdn


ZONE_OBJ_NAME = {
    0: 'Zone',
    1: 'Zone profile'}


def subnet_reverse_zones(subnet):
    '''
    :type subnet: Ipblock
    '''
    rzones = []
    subnet_str = IP(int(subnet.address), version=subnet.version).label(expanded=True)
    if subnet.version == 4:
        if subnet.prefix < 16:
            raise ValueError("The prefix %d is too small (the minimum is 16)" % subnet.prefix)
        raddr = [int(n) for n in list(reversed(subnet_str.split('.')))[1:]]
        bits = max(0, 24 - subnet.prefix)
        for i in range(2 ** bits):
            rzones.append('.'.join([str(n) for n in raddr] + ['in-addr.arpa']))
            raddr[0] += 1
    elif subnet.version == 6:
        # Don't allow prefixes larger than /64
        prefix = min(subnet.prefix, 64)
        # fhn = first nibble which has host bits
        fhn_bits = prefix % 4
        fhn_number = int(prefix / 4)
        if fhn_bits == 0:
            fhn_bits = 4
            fhn_number -= 1
        addr = list(reversed(subnet_str.replace(':', '')[:fhn_number + 1]))
        for i in range(2 ** (4 - fhn_bits)):
            rzones.append('.'.join(addr + ['ip6.arpa']))
            addr[0] = '%x' % (int(addr[0], 16) + 1)
    else:
        raise ValueError("Unknown IP version")
    return rzones


def guess_revzone(ip):
    '''
    :type ip: IP
    '''
    ip_str = ip.label(expanded=True)
    if ip.version == 4:
        return '.'.join(list(reversed(ip_str.split('.')[:3])) + ['in-addr.arpa'])
    elif ip.version == 6:
        digits = ''.join(ip_str.split(':')[:4])
        return '.'.join(list(reversed(digits)) + ['ip6.arpa'])
    else:
        raise ValueError("Unknown IP version")


def reverse_zone_profile(ip, layer3domain):
    for ancestor in Ipblock._ancestors_noparent(ip, layer3domain):
        profile = ancestor.get_attrs().get('reverse_dns_profile', None)
        if profile is not None:
            return profile


def get_ptr_name(block):
    address = block.label(expanded=True)
    if block.version == 4:
        return '.'.join(list(reversed(address.split('.'))) + ['in-addr.arpa']) + '.'
    elif block.version == 6:
        return '.'.join(list(reversed(address.replace(':', ''))) + ['ip6.arpa']) + '.'
    else:
        raise ValueError("Unknown IP version")


def get_ip_from_ptr_name(ptr_name, strict=True):
    if ptr_name.endswith('.in-addr.arpa.'):
        labels = list(reversed(ptr_name[:-len('.in-addr.arpa.')].split('.')))
        # remove rfc 2317 subnet markers
        labels = [p for p in labels if re.match(r'^\d+$', p)]
        if not strict and len(labels) < 4:
            labels += ['0'] * (4 - len(labels))
        if len(labels) == 4:
            return '.'.join(labels)
    elif ptr_name.endswith('.ip6.arpa.'):
        labels = list(reversed(ptr_name[:-len('.ip6.arpa.')].split('.')))
        labels = [p for p in labels if re.match(r'^[0-9a-fA-F]+$', p)]
        if not strict and len(labels) < 32:
            labels += ['0'] * (32 - len(labels))
        if len(labels) == 32:
            return ':'.join(''.join(labels[group * 4 + i] for i in range(4))
                            for group in range(8))
    raise ValueError('Invalid PTR name')


def get_view(zone, view_name):
    if view_name is None:
        if len(zone.views) == 1:
            return zone.views[0]
        else:
            raise MultipleViewsError('A view must be selected from: %s' % ' '.join(sorted(v.name for v in zone.views)))
    else:
        view = ZoneView.query.filter_by(zone=zone, name=view_name).first()
        if view is None:
            raise InvalidViewError('Zone %s has no view named %s.' % (zone.name, view_name))
        return view


def get_zone(name, profile):
    zone_name = Zone.to_display_name(name)
    name = Zone.from_display_name(name).lower()
    q = Zone.query.filter_by(name=name, profile=profile)
    zone = q.first()
    if zone is None:
        raise InvalidZoneError("%s %s does not exist" % (ZONE_OBJ_NAME[int(profile)], zone_name))
    return zone


def get_rr_zone(name, zone, profile, notfound_message=True):
    if zone is None:
        if not name.endswith('.'):
            raise InvalidParameterError('Name must be a FQDN')
        zone = Zone.find(name)
        if zone is None:
            if notfound_message:
                Messages.info('No zone found for %s' % name)
            return None
    else:
        zone = get_zone(zone, profile)
        if name.endswith('.') and (not name.endswith('.' + zone.name + '.') and name != zone.name + '.'):
            raise InvalidParameterError('RR name %s not in zone %s' % (name, zone.name))
    return zone


def zone_available_check(zone, profile):
    zone_name = Zone.from_display_name(zone)
    parent_zone = Zone.find(zone_name + '.')
    if parent_zone is not None and zone_name == parent_zone.name:
        raise AlreadyExistsError("%s %s already exists" % (ZONE_OBJ_NAME[int(parent_zone.profile)], Zone.to_display_name(zone_name)))
    return parent_zone


def rr_delete(name, zone, view, profile, free_ips, references, user, type=None, **kwargs):
    fqdn = make_fqdn(name, view.zone.name)
    rrs = RR.query.filter_by(name=make_fqdn(name, view.zone.name), view=view)
    display_query = [fqdn]
    if type:
        rrs = rrs.filter_by(type=type)
        display_query.append(type)
        if kwargs:
            kwargs = RR.validate_args(type, **kwargs)
            value = RR.get_class(type).value_from_fields(**kwargs)
            rrs = rrs.filter_by(value=value)
            if 'ip' in kwargs:
                rrs = rrs.filter_by(ipblock=kwargs['ip'])
            display_query.append(value)  # this should never be needed
    if rrs.count() > 1:
        raise DimError('%s is ambiguous' % ' '.join(display_query))
    elif rrs.count() == 0:
        raise DimError('%s does not exist' % ' '.join(display_query))
    delete_with_references(rrs, free_ips=free_ips, references=references, user=user)


def check_new_rr(new_rr):
    '''Check if rr can be created'''
    if new_rr.type in ['MX', 'NS', 'SRV']:
        point_to = _same_view_or_different_zone(new_rr).filter(RR.name == new_rr.target)
        if point_to.count() > 0:
            if new_rr.type in ['MX', 'NS'] and point_to.filter(or_(RR.type == 'A', RR.type == 'AAAA')).count() == 0:
                raise InvalidParameterError('The target of %s records must have A or AAAA resource records' % new_rr.type)
            if new_rr.type == 'SRV' and point_to.filter(or_(RR.type == 'A', RR.type == 'AAAA', RR.type == 'NS')).count() == 0:
                raise InvalidParameterError('The target of %s records must have A, AAAA or NS resource records' % new_rr.type)
    if new_rr.type == 'CNAME':
        if new_rr.name == new_rr.view.zone.name + '.':
            raise InvalidParameterError('It is not allowed to create a CNAME for a zone')
        if _same_view_or_different_zone(new_rr)\
                .filter(or_(RR.name == new_rr.name,
                            and_(~RR.type.in_(('CNAME', 'PTR')), RR.target == new_rr.name))).count():
            raise InvalidParameterError('%s cannot be created because other RRs with the same name or target exist' % new_rr)
    elif new_rr.type == 'DNAME':
        if new_rr.name == new_rr.view.zone.name + '.':
            raise InvalidParameterError('It is not allowed to create a DNAME for a zone')
        # DNAME cannot coexist with other records at the same name (except NS and DS at zone cuts)
        if _same_view_or_different_zone(new_rr)\
                .filter(RR.name == new_rr.name)\
                .filter(~RR.type.in_(('NS', 'DS'))).count():
            raise InvalidParameterError('%s cannot be created because other RRs with the same name exist' % new_rr)
        # Check for conflicting records under the DNAME subtree
        # Records under the DNAME subtree are those that are subdomains of the DNAME
        dname_name = new_rr.name if new_rr.name.endswith('.') else new_rr.name + '.'
        
        # Find all records that could be under this DNAME subtree
        all_records = _same_view_or_different_zone(new_rr).filter(RR.name != dname_name).all()
        
        for record in all_records:
            record_name = record.name if record.name.endswith('.') else record.name + '.'
            # Check if this record is a subdomain of the DNAME
            if record_name != dname_name and record_name.endswith('.' + dname_name):
                raise InvalidParameterError('%s cannot be created because RRs exist under the DNAME subtree' % new_rr)
    elif new_rr.type == 'PTR':
        if _same_view_or_different_zone(new_rr)\
                .filter(RR.type == 'CNAME').filter(RR.name == new_rr.name).count():
            raise InvalidParameterError('%s cannot be created because other RRs with the same name exist' % new_rr)
    else:
        if _same_view_or_different_zone(new_rr)\
                .filter(RR.type == 'CNAME').filter(or_(RR.name == new_rr.name, RR.name == new_rr.target)).count():
            raise InvalidParameterError('%s cannot be created because a CNAME with the same name exists' % new_rr)
        # Check if new record conflicts with existing DNAME records
        if _same_view_or_different_zone(new_rr)\
                .filter(RR.type == 'DNAME').filter(RR.name == new_rr.name).count():
            raise InvalidParameterError('%s cannot be created because a DNAME with the same name exists' % new_rr)
        # Check if new record is under a DNAME subtree
        dname_records = _same_view_or_different_zone(new_rr).filter(RR.type == 'DNAME').all()
        for dname in dname_records:
            # Normalize names by ensuring they end with a dot
            dname_name = dname.name if dname.name.endswith('.') else dname.name + '.'
            new_rr_name = new_rr.name if new_rr.name.endswith('.') else new_rr.name + '.'
            
            # Check if the new record is a subdomain under the DNAME
            if new_rr_name != dname_name and new_rr_name.endswith('.' + dname_name):
                raise InvalidParameterError('%s cannot be created under DNAME subtree %s' % (new_rr, dname.name))


def create_single_rr(name, rr_type, zone, view, user, overwrite=False, **kwargs):
    '''
    :type zone: Zone
    :type view: string
    :param view: name of the view
    :param rr_type: RR type (string)
    :return: True if the record was created or already existed
    '''
    view = get_view(zone, view)
    existed = False
    created = True
    name = make_fqdn(name, view.zone.name)
    rr_query = RR.query.filter(RR.name == name).filter(RR.type == rr_type)\
        .join(ZoneView).filter(RR.view == view)
    new_rr = RR.create(name=name, type=rr_type, view=view, **kwargs)
    rrs = rr_query.all()
    if rrs:
        if overwrite:
            for rr in rrs:
                Messages.warn("Deleting RR %s from %s" % (rr.bind_str(relative=True), rr.view))
                delete_single_rr(rr, user)
        else:
            samerr = None
            for rr in rrs:
                if rr.value == new_rr.value and (rr.type not in ('A', 'AAAA', 'PTR') or rr.ipblock == new_rr.ipblock):
                    samerr = rr
                    break
            if samerr:
                created = False
                existed = True
                Messages.info("%s already exists" % samerr)
            else:
                if rr_type == 'PTR':  # Don't allow PTR round robin records
                    created = False
                    Messages.warn("Not overwriting: %s" % rrs[0])
                elif rr_type == 'MX' and (new_rr.target == '.' and len(rrs) > 0):
                    raise DimError('Can not create NULL MX record - other MX record already exists')
                elif rr_type == 'MX' and RR.query.filter(RR.name == new_rr.name, RR.type == 'MX', RR.view == view, RR.target == '.').count() > 0:
                    raise DimError('Can not create MX record - NULL MX record already exists')
                else:
                    Messages.warn("The name %s already existed, creating round robin record" % name)
    if created:
        if rr_type == 'RP':
            point_to = _same_view_or_different_zone(new_rr).filter(RR.name == new_rr.target)
            if point_to.filter(RR.type == 'TXT').count() == 0:
                Messages.warn('TXT Record %s not found' % (new_rr.target,))
        elif rr_type == 'SSHFP':
            same_name = _same_view_or_different_zone(new_rr).filter(RR.name == new_rr.name)
            if same_name.filter(or_(RR.type == 'A', RR.type == 'AAAA')).count() == 0:
                Messages.warn('No A or AAAA found for %s' % (new_rr.name,))
        check_new_rr(new_rr)
        Messages.info("Creating RR {rr}{comment_msg} in {view_msg}".format(
            rr=new_rr.bind_str(relative=True),
            comment_msg=' comment {0}'.format(kwargs['comment']) if kwargs.get('comment', None) else '',
            view_msg=new_rr.view))
        if new_rr.target and rr_type not in ['PTR', 'RP']:
            # Creating a PTR record also creates the A/AAAA record
            if _same_view_or_different_zone(new_rr).filter(RR.name == new_rr.target).count() == 0:
                Messages.warn('%s does not exist.' % new_rr.target)
        new_rr.insert()
    return created or existed


def _same_view_or_different_zone(rr):
    '''Return other rrs in the same view or different zones'''
    return RR.query.join(RR.view).filter(RR.id != rr.id).filter(or_(RR.view == rr.view, ZoneView.zone != rr.view.zone))


def orphaned_references(rr, to_delete=None):
    '''Returns the RRs that would have invalid targets if *rr* is deleted
       (along with the rest of rrs in the to_delete set which contains rr)'''
    if to_delete is None:
        to_delete = [rr]
    same_name_count = len([r for r in to_delete if r.name == rr.name and r.zoneview_id == rr.zoneview_id])
    same_name = aliased(RR)
    refs = _same_view_or_different_zone(rr)\
        .filter(RR.target == rr.name).filter(RR.type != 'PTR')\
        .join(same_name, and_(same_name.zoneview_id == rr.zoneview_id,
                              same_name.name == rr.name))\
        .group_by(*RR.__table__.columns).having(func.count(same_name.id) == same_name_count)
    if rr.type in ('A', 'AAAA'):
        other_a_records = aliased(RR)
        # reverse records are only deleted when the last forward record is deleted
        # (multiple forward records for the same ip can be created in different views)
        refs = refs.union(
            RR.query.filter_by(type='PTR', ipblock_id=rr.ipblock_id, target=rr.name)
            .join(other_a_records, and_(other_a_records.name == rr.name,
                                        other_a_records.ipblock_id == rr.ipblock_id,
                                        other_a_records.type.in_(('A', 'AAAA'))))
            .group_by(*RR.__table__.columns).having(func.count(other_a_records.id) == 1))
        # NS and MX targets must have A/AAAA records
        other_a_count = len([r for r in to_delete if r.name == rr.name and
                             r.zoneview_id == rr.zoneview_id and
                             r.type in ('A', 'AAAA')])
        refs = refs.union(
            _same_view_or_different_zone(rr)
            .filter(RR.target == rr.name)
            .filter(RR.type.in_(('NS', 'MX')))
            .join(other_a_records, and_(other_a_records.zoneview_id == rr.zoneview_id,
                                        other_a_records.name == rr.name,
                                        other_a_records.type.in_(('A', 'AAAA'))))
            .group_by(*RR.__table__.columns).having(func.count(other_a_records.id) == other_a_count))
    elif rr.type == 'PTR':
        # forward records
        refs = refs.union(RR.query
                          .filter(RR.type.in_(('A', 'AAAA')))
                          .filter_by(ipblock_id=rr.ipblock_id, name=rr.target))
    # SRV targets must have A/AAAA/NS records
    if rr.type in ('A', 'AAAA', 'NS'):
        other_records = aliased(RR)
        other_a_and_ns_count = len([r for r in to_delete if r.name == rr.name and
                                    r.zoneview_id == rr.zoneview_id and
                                    r.type in ('A', 'AAAA', 'NS')])
        refs = refs.union(
            _same_view_or_different_zone(rr)
                .filter(RR.target == rr.name)
                .filter(RR.type == 'SRV')
                .join(other_records, and_(other_records.zoneview_id == rr.zoneview_id,
                                          other_records.name == rr.name,
                                          other_records.type.in_(('A', 'AAAA', 'NS'))))
                .group_by(*RR.__table__.columns).having(func.count(other_records.id) == other_a_and_ns_count))
    return refs


def delete_single_rr(rr, user):
    user.can_delete_rr(rr.view, rr.type)
    # If there are multiple identical A/AAAA records (name, type and content) in a view
    # they must have ipblocks in different layer3domains.
    # We need to make sure that the delete record event is sent to pdns only when the last record is deleted.
    # However, the SOA serial needs to be updated every time one of these records are deleted
    # in order to keep the SOA serial in sync between dim and pdns.
    send_delete_event = True
    if rr.type in ('A', 'AAAA'):
        if Layer3Domain.query.count() != 1:
            other_layer3domain_rrs = RR.query \
                .filter_by(view=rr.view, name=rr.name, type=rr.type, value=rr.value) \
                .filter(RR.ipblock_id != rr.ipblock_id) \
                .count() > 0
            if other_layer3domain_rrs:
                send_delete_event = False
    rr.delete(send_delete_rr_event=send_delete_event)


def delete_ipblock_rrs(ipblock_ids, user):
    rrs = RR.query.filter(RR.ipblock_id.in_(ipblock_ids))
    delete_with_references(rrs, free_ips=True, references='delete', user=user)


def delete_with_references(query, free_ips, references, user):
    if references not in ('warn', 'error', 'delete', 'ignore'):
        raise InvalidParameterError('Invalid value for references (must be warn, error, delete or ignore)')
    rrs = query.all()
    if free_ips:
        ipblocks = set()
    to_delete = set()
    done = False
    while rrs and not done:
        to_delete |= set(rrs)
        orphaned = set()         # RRs orphaned by deleting rrs
        forward_reverse = set()  # forward or reverse RRs corresponding to rrs
        for rr in rrs:
            for orr in orphaned_references(rr, to_delete).all():
                if (rr.type == 'PTR' and orr.type in ('A', 'AAAA')) or \
                   (rr.type in ('A', 'AAAA') and orr.type == 'PTR'):
                    if references != 'ignore':
                        forward_reverse.add(orr)
                elif orr not in rrs:
                    orphaned.add(orr)
        if references in ('warn', 'error', 'ignore'):
            for rr in orphaned:
                logging.debug('%s referenced by:\n%s' % (rr.target, rr))
            for rr_target in set(rr.target for rr in orphaned):
                if references == 'warn':
                    Messages.warn('%s is referenced by other records' % rr_target)
                elif references == 'error':
                    raise DimError('%s is referenced by other records' % rr_target)
            done = True
        rrs = (orphaned | forward_reverse) - to_delete
        to_delete |= forward_reverse
    for rr in sorted(to_delete, key=lambda rr: (rr.type, rr.name, rr.target, rr.value, rr.id)):
        Messages.info("Deleting RR %s from %s" % (rr.bind_str(relative=True), rr.view))
        if free_ips and rr.ipblock_id:
            subnet = Ipblock.query.get(rr.ipblock_id).subnet
            if subnet and subnet.pool:
                user.can_allocate(subnet.pool)
            ipblocks.add(rr.ipblock_id)
        delete_single_rr(rr, user)
    if free_ips and ipblocks:
        free_ipblocks(ipblocks)


def free_ipblocks(ipblocks):
    freed_ipblocks = Ipblock.query.outerjoin(RR, Ipblock.id == RR.ipblock_id)\
        .filter(Ipblock.id.in_(ipblocks)).group_by(*Ipblock.__table__.columns).having(func.count(RR.id) == 0)
    for ipblock in freed_ipblocks:
        Messages.info('Freeing IP %s from layer3domain %s' % (ipblock, ipblock.layer3domain.name))
        db.session.delete(ipblock)


def apply_profile(view, zone, from_profile):
    for rr in RR.query.filter_by(view=from_profile.views[0]):
        new_rr = RR(name=make_fqdn(RR.record_name(rr.name, rr.view.zone.name), zone.name),
                    view=view,
                    type=rr.type,
                    ttl=rr.ttl,
                    ipblock=rr.ipblock,
                    target=rr.target,
                    value=rr.value)
        try:
            check_new_rr(new_rr)
            db.session.add(new_rr)
        except InvalidParameterError:
            pass


def delete_ns_rrs_from_parent(view, parent_view, user):
    '''Delete subzone NS rrs from parent_view'''
    for rr in RR.query.filter(RR.zoneview_id == parent_view.id).filter(RR.name == view.zone.name + '.')\
            .filter(RR.type == 'NS'):
        Messages.info("Deleting RR %s from %s" % (rr.bind_str(relative=True), rr.view))
        delete_single_rr(rr, user)


def create_subzone(new_zone, parent_zone, from_profile, soa_attributes, user,
                   inherit_rights=True, inherit_zone_groups=True):
    '''
    Create views and move rrs from parent zone.
    Records from the parent zones have priority over the ones from the profile.
    Copy @ NS records from subzone to parent zone.
    If inherit_rights is True, inherit user rights from parent zone.
    If inherit_zone_groups is True, inherit zone-group membership from parent zone. No zone-group rights are checked.
    This is necessary so net_admins can create subnets without access to the zone-group
    where the parent reverse zone is a member.
    If inherit_owner is True, inherit the owner from parent zone.
    '''
    # map parent view id to child view
    view_id_map = {}
    parent_views_ids = [view.id for view in parent_zone.views]
    for view in parent_zone.views:
        view_id_map[view.id] = ZoneView.create(new_zone, view.name,
                                               from_profile=from_profile, soa_attributes=soa_attributes, copy_rrs=False)
    Messages.info('Creating views %s for zone %s' % (', '.join([view.name for view in new_zone.views]), new_zone.name))
    for rr in RR.query.filter(RR.zoneview_id.in_(parent_views_ids))\
            .filter(or_(RR.name.endswith('.' + new_zone.name + '.'),
                        RR.name == new_zone.name + '.')):
        view_msg = (' view ' + rr.view.name) if len(parent_zone.views) > 1 else ''
        msg_info = (rr.bind_str(relative=True),
                    new_zone.name + view_msg,
                    parent_zone.name + view_msg)
        rr.notify_delete()
        rr.view = view_id_map[rr.zoneview_id]
        rr.value = RR.get_class(rr.type).fqdn_target(rr.value, parent_zone.name)
        try:
            check_new_rr(rr)
            Messages.info('Moving RR %s in zone %s from zone %s' % msg_info)
            rr.notify_insert()
        except InvalidParameterError:
            db.session.delete(rr)
            Messages.warn('Rejected to move RR %s in zone %s, deleted RR from zone %s' % msg_info)
    if from_profile is not None:
        for view in new_zone.views:
            apply_profile(view, new_zone, from_profile)
    for parent_view in parent_zone.views:
        if inherit_zone_groups:
            for group in parent_view.groups:
                zone_group_add_zone(group, new_zone, view_id_map[parent_view.id])
        if inherit_rights:
            rights = AccessRight.query.filter_by(object_class='ZoneView', object_id=parent_view.id).all()
            for right in rights:
                for group in right.groups:
                    group.rights.add(AccessRight.find_or_create(access=right.access,
                                                                object_id=view_id_map[parent_view.id].id,
                                                                object_class=right.object_class))


def zone_group_add_zone(group, zone, view):
    # The rights check is performed only in RPC.zone_group_add_zone;
    # when creating subzones, no check is needed.
    for output in group.outputs:
        if check_view_addition_to_output(view, output):
            raise InvalidParameterError('You can not add %s view %s to the zone-group %s because it breaks the output %s.'
                                        % (zone.display_name, view.name, group.name, output.name))
    group.views.append(view)
    group.update_modified()


def check_view_addition_to_output(view, output):
    '''Must be called before the addition'''
    # views from the same zone and output
    zone_views = ZoneView.query.join(ZoneView.zone).filter(ZoneView.zone == view.zone)\
        .join(ZoneView.groups).join(ZoneGroup.outputs).filter(Output.id == output.id)
    present = False     # view is already present in output
    for other_view in zone_views:
        if other_view == view:
            present = True
        else:
            return other_view
    if not present:
        OutputUpdate.send_create_view(view, output, OutputUpdate.CREATE_ZONE)


def check_view_removal_from_output(view, output):
    '''Must be called after the removal'''
    if ZoneView.query.join(ZoneView.groups).join(ZoneGroup.outputs)\
            .filter(Output.id == output.id)\
            .filter(ZoneView.id == view.id).count() == 0:
        OutputUpdate.send_delete_view(view, output)


def get_subzones(zone):
    descendants = Zone.query.filter(Zone.name.like('%.' + zone.name)).all()
    return [s for s in descendants if re.match(r'^[^\.]+\.%s$' % zone.name, s.name)]


def get_parent_zone(zone_name):
    index = zone_name.find('.')
    if index == -1:
        return None
    return Zone.query.filter_by(name=zone_name[index + 1:], profile=False).first()


def get_parent_zone_view(zone_name, view_name):
    parent_zone = get_parent_zone(zone_name)
    if parent_zone is None:
        return None
    for parent_view in parent_zone.views:
        if parent_view.name == view_name:
            return parent_view
    return None


def create_ds_rr(key, user, parent_zone=None):
    '''Create DS records for key into views of the parent_zone that match subzone view names if parent_zone is signed'''
    if key.type != 'ksk':
        return
    if parent_zone is None:
        parent_zone = get_parent_zone(key.zone.name)
    if parent_zone and parent_zone.is_signed():
        digest_type = 2
        for view in parent_zone.views:
            create_single_rr(user=user, zone=parent_zone, view=view.name, ttl=None, **key.ds_rr_params(digest_type))


def delete_ds_rr(key, user, parent_zone=None, view=None):
    '''Delete DS records for key from view (or all views that match zone view names if view is None)
    of parent_zone if parent_zone is signed'''
    if key.type != 'ksk':
        return
    if parent_zone is None:
        parent_zone = get_parent_zone(key.zone.name)
    if parent_zone and parent_zone.is_signed():
        digest_type = 2
        for view in [view] if view else parent_zone.views:
            rr_delete(user=user, zone=parent_zone, view=view, type='DS', profile=False, free_ips=False,
                      references='ignore', **key.ds_rr_params(digest_type))


def _matching_views(parent_zone, subzone):
    view_names = set(v.name for v in subzone.views)
    return [v for v in parent_zone.views if v.name in view_names]


def copy_ns_rrs(zone, view, user):
    def get_child_view(subzone, view_name):
        for view in subzone.views:
            if view.name == view_name:
                return view
        return None

    def copy_rrs(parent_view, child_view):
        if child_view is None or parent_view is None:
            return
        for rr in RR.query.filter(RR.zoneview_id == view.id).filter(RR.name == view.zone.name + '.')\
                .filter(RR.type == 'NS'):
            create_single_rr(name=rr.name, rr_type=rr.type, zone=parent_view.zone, view=view.name, user=user,
                             ttl=rr.ttl, **RR.get_class(rr.type).fields_from_value(rr.value))

    # Copy NS in parent zone if view names match
    copy_rrs(get_parent_zone_view(zone.name, view.name), view)
    # Copy NS from subzones if view names match and add DS rrs
    for subzone in get_subzones(zone):
        copy_rrs(view, get_child_view(subzone, view.name))
