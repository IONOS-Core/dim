import logging
import re
import dns.zone
import dns.name
import dns.rdatatype
import dns.tokenizer
import dimcli

RECORD = 25

class ParsedRR(object):
    __slots__ = ('src', 'args')
    def __init__(self, src, args):
        self.src = src
        self.args = args


rdata_params = {
    'A': lambda rdata: dict(ip=rdata.address),
    'AAAA': lambda rdata: dict(ip=rdata.address),
    'PTR': lambda rdata: dict(ptrdname=str(rdata.target)),
    'CNAME': lambda rdata: dict(cname=str(rdata.target)),
    'DNAME': lambda rdata: dict(target=str(rdata.target)),
    'MX': lambda rdata: dict(preference=int(rdata.preference), exchange=str(rdata.exchange)),
    'NS': lambda rdata: dict(nsdname=str(rdata.target)),
    'TXT': lambda rdata: dict(strings=rdata.to_text()),
    'SPF': lambda rdata: dict(strings=rdata.to_text()),
    'RP': lambda rdata: dict(mbox=str(rdata.mbox), txtdname=str(rdata.txt)),
    'HINFO': lambda rdata: dict(cpu=rdata.cpu, os=rdata.os),
    'SRV': lambda rdata:
        dict(priority=int(rdata.priority),
             weight=int(rdata.weight),
             port=int(rdata.port),
             target=str(rdata.target)),
    'CERT': lambda rdata:
        dict(certificate_type=int(rdata.certificate_type),
             key_tag=int(rdata.key_tag),
             algorithm=int(rdata.algorithm),
             certificate=rdata.certificate),
    'NAPTR': lambda rdata:
        dict(order=int(rdata.order),
             preference=int(rdata.preference),
             flags=rdata.flags,
             service=rdata.service,
             regexp=rdata.regexp,
             replacement=rdata.replacement),
    }


def msg(result):
    dimcli._print_messages(result)


def rdata_to_text(name, ttl, rdata):
    rr_type = dns.rdatatype.to_text(rdata.rdtype)
    fields = [name,
              dns.rdataclass.to_text(rdata.rdclass),
              rr_type,
              rdata.to_text()]
    if ttl:
        fields[1:1] = [ttl]
    return ' '.join(str(x) for x in fields)


def create_imported_view(server, zone_name, default_ttl, soa_rdata, view):
    soa_attributes=dict(
        primary=str(soa_rdata.mname),
        ttl=default_ttl,
        mail=str(soa_rdata.rname),
        serial=soa_rdata.serial,
        expire=soa_rdata.expire,
        minimum=soa_rdata.minimum,
        refresh=soa_rdata.refresh,
        retry=soa_rdata.retry)
    logging.info('Creating zone %s%s' % (zone_name, " view " + view if view else ""))
    view_name = view if view else 'default'
    if server.zone_list(pattern=zone_name) and view is not None:
        msg(server.zone_create_view(zone_name, view, soa_attributes=soa_attributes))
        single_view = False
    else:
        msg(server.zone_create(zone_name, soa_attributes=soa_attributes, empty_profile_warning=False, view_name=view_name))
        single_view = True
    logging.log(RECORD, rdata_to_text(zone_name + '.', default_ttl, soa_rdata))
    logging.info("Creating RR @ SOA %(rr)s in zone %(zone)s%(view_msg)s" % dict(
            rr=soa_rdata.to_text(),
            zone=zone_name,
            view_msg=" view " + view_name if not single_view else ""))


def get_first_soa(parsed_zone):
    default_ttl = None
    for name, ttl, rdata in parsed_zone.iterate_rdatas():
        if rdata.rdtype == dns.rdatatype.SOA:
            return name, ttl, rdata
    return None, None, None


def get_parsed_records(parsed_zone, default_ttl=None):
    records = []
    for name, rdlist in list(parsed_zone.items()):
        for rdataset in rdlist.rdatasets:
            for rdata in rdataset.items:
                rr_type = dns.rdatatype.to_text(rdata.rdtype)
                rr_src = rdata_to_text(name, rdataset.ttl, rdata)
                if rr_type == 'SOA':
                    continue
                if rr_type not in rdata_params:
                    logging.warn('Import not implemented for RR type %s' % rr_type)
                    continue
                rr_args = dict(name=str(name),
                               ttl=None if rdataset.ttl == default_ttl else rdataset.ttl,
                               type=rr_type,
                               **rdata_params[rr_type](rdata))
                records.append(ParsedRR(src=rr_src, args=rr_args))
    return records


def target_from_fields(kwargs):
    target_fields = ('ptrdname', 'cname', 'exchange', 'nsdname', 'target', 'txtdname')
    tf = list(set(target_fields) & set(kwargs.keys()))
    if tf:
        return kwargs[tf[0]]


def toposort(nodes, neighbours):
    '''
    Performs topological sort on a graph defined by:
    - *nodes* number of nodes
    - *neighbours* function that takes a node number and returns the node's neighbours as list
    '''
    sorted_nodes = []
    seen = set()
    def dfs(node):
        if node in seen:
            return
        seen.add(node)
        for neighbour in neighbours(node):
            dfs(neighbour)
        sorted_nodes.append(node)
    for i in range(nodes):
        dfs(i)
    return sorted_nodes


def toposort_records(records):
    '''
    :type records: list of ParsedRR objects
    '''
    # Build the graph
    targets = []
    names = {}
    for i, record in enumerate(records):
        targets.append(target_from_fields(record.args))
        names.setdefault(record.args['name'], set()).add(i)
    # Sort the graph
    sorted_nodes = toposort(len(records), lambda node: names.get(targets[node], []))
    return [records[node] for node in sorted_nodes]


def import_zone(server, content, zone_name=None, view=None, revzone=False):
    parsed_zone = dns.zone.from_text(str(content), origin=zone_name, relativize=False, check_origin=False)
    soa_name, default_ttl, soa_rdata = get_first_soa(parsed_zone)
    if soa_rdata is None:
        raise Exception('Missing SOA')
    if revzone:
        extra_args = dict(profile=False, create_linked=False)
        logging.log(RECORD, rdata_to_text(soa_name, default_ttl, soa_rdata))
        logging.info('Nothing to do')
    else:
        extra_args = dict(profile=False, views=[view], zone=zone_name)
        if str(soa_name) != zone_name + '.':
            raise Exception('SOA does not match the zone name')
        create_imported_view(server, zone_name, default_ttl, soa_rdata, view)
    parsed_records = get_parsed_records(parsed_zone, default_ttl)
    if not revzone:
        parsed_records = toposort_records(parsed_records)
    for parsed_record in parsed_records:
        logging.log(RECORD, parsed_record.src)
        try:
            args = dict(extra_args)
            args.update(parsed_record.args)
            if args['type'] == 'PTR':
                args['create_revzone'] = True
            msg(server.rr_create(**args))
        except Exception as e:
            logging.debug('Error importing %s' % parsed_record.src, exc_info=True)
            logging.error(str(e))
