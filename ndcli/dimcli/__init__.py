import binascii
import getpass
import logging
import os.path
import re
import shlex
import sys
import textwrap
from datetime import datetime
from functools import wraps
from typing import List
from collections import OrderedDict

from operator import itemgetter

from . import zoneimport
from .cliparse import Command, Option, Group, Argument, Token
from . import version

from dimclient import DimClient, DimError

__version__ = version.VERSION

logger = logging.getLogger('ndcli')

def _readconfig(config_file):
    config = {}
    config['server'] = "https://localhost/dim"
    config['username'] = getpass.getuser()
    try:
        cfg = open(config_file)
        for line in cfg:
            if '=' in line:
                key, value = line.split('=', 1)
                config[key.strip()] = value.strip()
    except Exception as e:
        logger.debug('Configuration file cannot be read: %s' % e)
    return config

config_path = os.getenv('NDCLI_CONFIG', '~/.ndclirc')
config = _readconfig(os.path.expanduser(config_path))

# get_layer3domain returns from_args, from the environment variable or the
# layer3domain from ndclirc.
def get_layer3domain(from_args):
    if from_args is not None:
        return from_args
    env_l3 = os.getenv('NDCLI_LAYER3DOMAIN', None)
    if env_l3 is not None:
        return env_l3
    if 'layer3domain' in config:
        return config['layer3domain']
    return None

class Client(object):
    def __init__(self, server_url, username, password, cookie_file=None, cookie_umask=None, dry_run=False):
        self.dry_run = dry_run
        self.client = DimClient(server_url, cookie_file=cookie_file, cookie_umask=cookie_umask)
        if not self.client.logged_in:
            if not self.login_prompt(username=username, password=password, ignore_cookie=True):
                raise Exception('could not log in')

    def login_prompt(self, username=None, password=None, permanent_session=False, ignore_cookie=False):
        if not ignore_cookie and self.logged_in:
            return True
        else:
            if username is None:
                username = input('Username: ')
            if password is None:
                password = getpass.getpass()
            return self.client.login(username, password, permanent_session)

    # inject the dry-run argument into kwargs, so that all calls can deliver dry-run.
    def _add_dryrun(self, func, *args, **kwargs):
        if self.dry_run:
            kwargs['dryrun'] = self.dry_run

        fun = getattr(self.client, func)
        return fun(*args, **kwargs)

    def __getattr__(self, name):
        return lambda *args, **kwargs: self._add_dryrun(name, *args, **kwargs)

def dim_client(args):
    server_url = args.server or os.getenv('NDCLI_SERVER', config['server'])
    username = args.username or os.getenv('NDCLI_USERNAME', config['username'])
    cookie_path = os.path.expanduser(os.getenv('NDCLI_COOKIEPATH', f'~/.ndcli.cookie.{username}'))
    logger.debug("Dim server URL: %s" % server_url)
    logger.debug("Username: %s" % username)
    client = Client(
            server_url,
            username,
            args.password,
            cookie_file=cookie_path,
            cookie_umask=0o077,
            dry_run=args.get('dryrun', False))
    return client


def email2fqdn(string):
    try:
        name, host = string.split('@')
        return '%s.%s.' % (name.replace('.', '\\.'), host)
    except:
        raise Exception('Invalid email address: %s' % string)


def is_reverse_zone(zone):
    return zone.endswith('in-addr.arpa') or zone.endswith('ip6.arpa')


def generate_salt():
    return binascii.hexlify(os.urandom(8))


def completion_callback(func):
    @wraps(func)
    def wrapper(token, parser):
        try:
            return func(token, parser)
        except:
            # logger.warning('error completing "%s"' % token, exc_info=True)
            return []
    return wrapper


@completion_callback
def complete_poolname(token, parser):
    pools = dim_client(parser.values).ippool_list(pool=token + '*', include_subnets=False)
    return [p['name'] for p in pools]


@completion_callback
def complete_allocate_poolname(token, parser):
    pools = dim_client(parser.values).ippool_list(pool=token + '*', include_subnets=False, can_allocate=True)
    return [p['name'] for p in pools]


@completion_callback
def complete_subnet(token, parser):
    subnets = dim_client(parser.values).ippool_get_subnets(parser.values['poolname'], include_usage=False)
    return [p['subnet'] for p in subnets if p['subnet'].startswith(token)]


@completion_callback
def complete_delegation(token, parser):
    delegations = dim_client(parser.values).ippool_get_delegations(parser.values['poolname'], include_usage=False)
    return [p['delegation'] for p in delegations if p['delegation'].startswith(token)]


@completion_callback
def complete_zone(token, parser):
    zones = dim_client(parser.values).zone_list(pattern=token + '*', profile=False)
    return [p['name'] for p in zones]


@completion_callback
def complete_zone_keys(token, parser):
    return [k['label'] for k in dim_client(parser.values).zone_list_keys(parser.values['zonename'])]


@completion_callback
def complete_zoneprofile(token, parser):
    zones = dim_client(parser.values).zone_list(pattern=token + '*', profile=True)
    return [p['name'] for p in zones]


def complete_view(zone_arg):
    @completion_callback
    def _complete_view(token, parser):
        zone_name = parser.values[zone_arg]
        if zone_name is None:
            return []
        if not zone_name.endswith('.'):
            zone_name += '.'
        views = dim_client(parser.values).name_list_views(zone_name)
        return [p['name'] for p in views]
    return _complete_view


@completion_callback
def complete_output(token, parser):
    outputs = dim_client(parser.values).output_list()
    return [p['name'] for p in outputs]


@completion_callback
def complete_group(token, parser):
    groups = dim_client(parser.values).group_list()
    return [p for p in groups]


@completion_callback
def complete_zone_group(token, parser):
    zone_groups = dim_client(parser.values).zone_group_list()
    return [p['name'] for p in zone_groups]


@completion_callback
def complete_department(token, parser):
    return [d['name']
            for d in dim_client(parser.values).department_list()]


@completion_callback
def complete_username(token, parser):
    return [u['name'] for u in dim_client(parser.values).user_list()]


@completion_callback
def complete_username_from_group(token, parser):
    return dim_client(parser.values).group_get_users(parser.values['group'])


@completion_callback
def complete_registrar_account(token, parser):
    return [r['name'] for r in dim_client(parser.values).registrar_account_list()]


@completion_callback
def complete_layer3domain(token, parser):
    return [r['name'] for r in dim_client(parser.values).layer3domain_list()]


@completion_callback
def complete_rr_type(token, parser):
    rrs = dim_client(parser.values).rr_list(pattern=parser.values['name'], zone=parser.values.get('zonename'))
    return [t.lower() for t in set([r['type'] for r in rrs]) - set(['SOA'])]


@completion_callback
def complete_rr_value(token, parser):
    # Hack for delete rr
    if parser.values['type'].lower() == 'view':
        return []
    rrs = dim_client(parser.values).rr_list(pattern=parser.values['name'], type=parser.values['type'],
                                            zone=parser.values.get('zonename'))
    return [r['value'] for r in rrs]


query_description = '''If no query parameter is given, all the pools are selected. Otherwise, the
parameter is tested to determine its type in the following order:

* VLAN_ID: positive integer >=2 and <=4094
* CIDR: address/prefix
* POOL: any string. You may use '*' for matching multiple characters and '?' for
  matching single characters.'''
full_option = Option('F', 'full', help='print IPv6 addresses in full')
script_option = Option('H', 'script', help='scripting mode output (no headers, tab between fields)')
dryrun_option = Option('n', 'dryrun', help="don't commit changes to the database")
attributes_arg = Argument('attributes', metavar='NAME:VALUE', nargs='*')
attr_names_arg = Argument('attr_names', metavar='ATTR_NAME', nargs='*')
dns_field_value_group = Group(Argument('fields',
                                       choices='primary|mail|serial|refresh|retry|expire|minimum|ttl'.split('|'),
                                       action='append_unique'),
                              Argument('values', metavar='VALUE', action='append'),
                              nargs='*')
overlap_option = Option(None, 'allow-overlap',
                        help="allow overlapping with other IP blocks in other layer3domains in whitelisted IP spaces")

complete_view_from_name = complete_view('name')
complete_view_from_zone = complete_view('zonename')
rr_view_group = Group(Token('view'),
                      Argument('view', action='append_unique', nargs='*', completions=complete_view_from_name,
                               stop_at='layer3domain'), nargs='?')
zoneview_group = Group(Token('view'), Argument('view', action='append_unique', nargs='*', completions=complete_view_from_zone), nargs='?')
rr_single_view_group = Group(Token('view'), Argument('view', completions=complete_view_from_name), nargs='?')
zone_single_view_group = Group(Token('view'), Argument('view', completions=complete_view_from_zone), nargs='?')
rr_comment_option = Option('c', 'comment', help='rr comment', action='store')
rr_ttl_option = Option('t', 'ttl', help='rr ttl', action='store')
zoneprofile_arg = Argument('profilename', completions=complete_zoneprofile)
output_arg = Argument('output', completions=complete_output)
zone_group_arg = Argument('zonegroup', completions=complete_zone_group)
group_arg = Argument('group', completions=complete_group, metavar='USERGROUP')
zone_arg = Argument('zonename', completions=complete_zone)
user_arg = Argument('user', completions=complete_username)
layer3domain_arg = Argument('layer3domain', completions=complete_layer3domain)
layer3domain_group = Group(Token('layer3domain'), layer3domain_arg, nargs='?')
registrar_account_arg = Argument('registrar_account', completions=complete_registrar_account,
                                 metavar='REGISTRAR_ACCOUNT')


cmd = Command('ndcli',
              Option('D', 'detailed', help='detailed return codes'),
              Option('q', 'quiet',    help="don't print WARNING or INFO messages"),
              Option('w', 'warnings', help="don't print INFO messages"),
              Option('d', 'debug',    help='also print DEBUG messages'),
              Option('s', 'server',   help='Dim server URL', action='store'),
              Option('u', 'username', help='Dim username', action='store'),
              Option('p', 'password', help='Dim password', action='store'),
              Option('h', 'help',     help='display usage information'),
              Option('V', 'version',  help='print version and exit'),

              Command('create',
                      dryrun_option,
                      Command('rr',
                              Argument('name'),
                              Group(Token('ttl'), Argument('ttl'), nargs='?')),
                      Command('output',
                              Argument('name'),
                              Token('plugin')),
                      ),
              Command('list',
                      Command('user',
                              Argument('user'),
                              default_subcommand='rights'),
                      Command('user-group',
                              group_arg),
                      Command('pool',
                              Argument('poolname', completions=complete_poolname),
                              script_option,
                              full_option,
                              default_subcommand='subnets'),
                      Command('zone',
                              zone_arg,
                              Group(Token('view'), Argument('view', completions=complete_view_from_zone), nargs='?'),
                              script_option,
                              default_subcommand='records'),
                      Command('zone-group',
                              zone_group_arg,
                              script_option,
                              default_subcommand='views')),
              Command('modify',
                      dryrun_option,
                      Command('container',
                              Argument('container'),
                              layer3domain_group,
                              Command('remove'),
                              Command('set'),
                              Command('move'),
                              defaults=dict(block_type='container')),
                      Command('pool',
                              Argument('poolname', completions=complete_allocate_poolname),
                              Command('add'),
                              Command('free'),
                              Command('get'),
                              Command('mark'),
                              Command('remove'),
                              Command('set'),
                              Command('subnet',
                                      Argument('subnet', completions=complete_subnet),
                                      Command('set'),
                                      Command('get'),
                                      Command('remove'),
                                      defaults=dict(block_type='subnet')),
                              Command('delegation',
                                      Argument('delegation', completions=complete_delegation),
                                      Command('set'),
                                      Command('remove'),
                                      Command('get'),
                                      Command('mark'),
                                      Command('free'),
                                      defaults=dict(block_type='delegation')),
                              Command('ip',
                                      Argument('ip'),
                                      Command('set'),
                                      Command('remove'),
                                      defaults=dict(block_type='ip'))),
                      Command('zone',
                              zone_arg,
                              Command('create',
                                      Command('rr',
                                              Argument('name'),
                                              Group(Token('ttl'), Argument('ttl'), nargs='?'))),
                              Command('delete',
                                      Command('rr',
                                              Argument('name'),
                                              Option('R', 'recursive', help='recursively delete references'),
                                              Option('f', 'force',
                                                     help='delete the rr even if it has references'),
                                              Option(None, 'keep-ip-reservation',
                                                     help='keep IP reservations'),
                                              default_subcommand='any')),
                              Command('rename'),
                              Command('dnssec',
                                      Command('delete'),
                                      Command('new'))),
                      Command('zone-profile',
                              zoneprofile_arg,
                              Command('create',
                                      Command('rr',
                                              Argument('name'),
                                              Group(Token('ttl'), Argument('ttl'), nargs='?'))),
                              Command('delete',
                                      Command('rr',
                                              Argument('name')))),
                      Command('rr',
                              rr_single_view_group,
                              rr_comment_option,
                              rr_ttl_option,
                              Argument('name'),
                              default_subcommand='any'),
                      Command('user-group',
                              group_arg,
                              Command('add'),
                              Command('remove'),
                              Command('set'),
                              Command('grant'),
                              Command('revoke')),
                      Command('output',
                              output_arg),
                      Command('zone-group',
                              zone_group_arg,
                              Command('set'),
                              Command('add')),
                      Command('layer3domain',
                              layer3domain_arg,
                              Command('set')),
                      Command('registrar-account',
                              registrar_account_arg)),
              Command('import'),
              Command('rename',
                      dryrun_option),
              Command('delete',
                      dryrun_option,
                      Command('rr',
                              Argument('name'),
                              Option('R', 'recursive', help='recursively delete references'),
                              Option('f', 'force',
                                     help='delete the rr even if it has references'),
                              Option(None, 'keep-ip-reservation',
                                     help='keep IP reservations'),
                              default_subcommand='any')),
              Command('show',
                      Command('zone',
                              zone_arg,
                              default_subcommand='attrs'),
                      Command('rr',
                              Argument('name'),
                              default_subcommand='any')),
              Command('history',
                      script_option,
                      Option('L', 'limit',
                             help='max number of results (default: 10)',
                             action='store',
                             default='10'),
                      Option('b', 'begin',
                             help='begin timestamp (default: None)',
                             action='store',
                             default=None),
                      Option('e', 'end',
                             help='end timestamp (default: None)',
                             action='store',
                             default=None),
                      Command('zone',
                              zone_arg,
                              default_subcommand='any'),
                      default_subcommand='any'),
              Command('dump'))


RR_FIELDS = {
    'a':   {'arguments': [Argument('ip')],
            'create_rr_description': '''Creates an A or AAAA resource record and the corresponding reverse record.

If A/AAAA records with the same name (but with different IPs) already exist:

- with --overwrite-a, they are deleted before creating the new RR
- otherwise a round robin RR is created and a warning is printed

If the reverse zone and the forward record exist, the corresponding PTR record
is also created. If the PTR record already exists but points to a different
name:

- with --overwrite-ptr it is overwritten
- otherwise a warning is printed'''},
    'ptr': {'arguments': [Argument('ptrdname')],
            'create_rr_description': '''Creates a PTR resource record and the corresponding forward record.

If the PTR record already exists with a different ptrdname:

- with --overwrite-ptr it is overwritten
- otherwise an error is returned

If the forward zone and the PTR record exist, the corresponding A/AAAA record is
also created. If A/AAAA records with the same name (but with different IPs)
already exist:

- with --overwrite-a, they are deleted before creating the new RR
- otherwise a round robin RR is created and a warning is printed'''},
    'txt': {'arguments': [Argument('strings', metavar='string', nargs='+', stop_at='view')],
            'delete_arguments': [Argument('strings', metavar='string', nargs='*', stop_at='view')],
            'create_rr_description': '''Creates a TXT resource record.

If one of the strings is "view", the optional view parameter becomes
mandatory to resolve the ambiguity (the last "view" argument signals
the start of the view list).'''},
    'spf': {'arguments': [Argument('strings', metavar='string', nargs='+', stop_at='view')],
            'delete_arguments': [Argument('strings', metavar='string', nargs='*', stop_at='view')],
            'create_rr_description': '''Creates an SPF resource record.

If one of the strings is "view", the optional view parameter becomes
mandatory to resolve the ambiguity (the last "view" argument signals
the start of the view list).'''},
    'mx':  {'arguments': [Argument('preference'), Argument('exchange')]},
    'ns':  {'arguments': [Argument('nsdname')]},
    'srv': {'arguments': [Argument('priority'), Argument('weight'), Argument('port'), Argument('target')]},
    'cname': {'arguments': [Argument('cname')]},
    'rp': {'arguments': [Argument('mbox'), Argument('txtdname')]},
    'cert': {'arguments': [Argument('certificate_type'), Argument('key_tag'), Argument('algorithm'), Argument('certificate')]},
    'hinfo': {'arguments': [Argument('cpu'), Argument('os')]},
    'naptr': {'arguments': [Argument('order'),
                            Argument('preference'),
                            Argument('flags'),
                            Argument('service'),
                            Argument('regexp'),
                            Argument('replacement', nargs='?', default='.')]},
    'tlsa': {'arguments': [Argument('certificate_usage'),
                           Argument('selector'),
                           Argument('matching_type'),
                           Argument('certificate')]},
    'sshfp': {'arguments': [Argument('algorithm'),
                            Argument('fingerprint_type'),
                            Argument('fingerprint')]},
    'ds': {'arguments': [Argument('key_tag'),
                         Argument('algorithm'),
                         Argument('digest_type'),
                         Argument('digest')]},
    'caa': {'arguments': [Argument('caa_flags'),
                          Argument('property_tag'),
                          Argument('property_value')]},
    'dname': {'arguments': [Argument('target')]},
    }
RR_FIELDS['aaaa'] = RR_FIELDS['a']
rr_types = list(RR_FIELDS.keys()) + ['soa']
rr_type_arg = Group(Argument('type', choices=rr_types+[t.upper() for t in rr_types]), nargs='?')


def _fill_rr_options(options, rr_type, params, args):
    if rr_type == 'PTR' and re.match(r'^(((\d+\.){3}\d+)|(.*:.*))$', args.name):
        options['ip'] = args.name
    else:
        options['name'] = args.name
    for param in params:
        if args[param] is not None:
            if param == 'strings':
                if args.strings:
                    options[param] = args[param]
            else:
                options[param] = args[param]
    # Convert the RP mbox parameter from normal@address to normal.address
    if 'mbox' in options:
        options['mbox'] = email2fqdn(options['mbox'])
    return options


def _rr_options(rr_type, params, profile, args, zonearg):
    options = OptionDict()
    options.set_if(type=rr_type,
                   profile=profile,
                   ttl=args.get('ttl', None),
                   comment=args.get('comment', None),
                   views=args.get('view', None),
                   zone=args.get(zonearg, None))
    return _fill_rr_options(options, rr_type, params, args)


def _make_create_rr(rr_type, params, profile, zonearg, create_linked=None):
    def create_simple_rr(self, args):
        options = _rr_options(rr_type, params, profile, args, zonearg)
        if rr_type in ('A', 'AAAA', 'PTR'):
            options.set_if(layer3domain=get_layer3domain(args.layer3domain),
                           allow_overlap=args.get('allow-overlap'),
                           overwrite_a=args.get('overwrite', args.get('overwrite-a', False)),
                           overwrite_ptr=args.get('overwrite-ptr', False),)
            options['create_linked'] = not args.get('only-forward', args.get('only-reverse', False))
            if create_linked is not None:
                options['create_linked'] = create_linked
            if rr_type in ('A', 'AAAA') and 'name' in options and options['name'].startswith('*.') and\
                    not args.get('only-forward', False):
                raise Exception('Use --only-forward to create a wildcard record')
        else:
            options.set_if(overwrite=args.overwrite)
        if rr_type != 'PTR' and '.' in args.name and not args.name.endswith('.'):
            self.warning("The left hand side of this record contains '.'. This will probably not do what you expect it to do.")

        if "ttl" not in options and "default-ttl" in config:
            options["ttl"] = config["default-ttl"]

        result = self.client.rr_create(**options)
        _print_messages(result)
    return create_simple_rr


def _make_delete_rr(rr_type, params, profile, zonearg, ignore_references=False):
    def delete_rr(self, args):
        options = _rr_options(rr_type, params, profile, args, zonearg)
        options.set_if(free_ips=not args.get('keep-ip-reservation', True))
        if rr_type in ('A', 'AAAA', 'PTR'):
            options.set_if(layer3domain=get_layer3domain(args.layer3domain))
        force = args.get('force', True)
        recursive = args.get('recursive', False)
        if force and recursive:
            raise Exception('--force and --recursive can not be combined')
        if force:
            options['references'] = 'warn'
        if recursive:
            options['references'] = 'delete'
        if ignore_references:
            options['references'] = 'ignore'
        result = self.client.rr_delete(**options)
        _print_messages(result)
    return delete_rr


def _delete_rr(self, args, zonearg, ignore_references=False):
    '''
    If autocomplete was used, the rr value is in args['value'],
    else the first argument is in args['value'] and the rest in args['values'].

    args['value'] is split into individual arguments and concatenated with args['values'],
    then parsed by the "create rr" command. The result of the parsing is sent to the rr_delete API call.

    For TXT and SPF rr types, no split is needed since the API accepts also the whole rr value as string (instead
    of a list of strings). Since at this point there's no way to know if autocomplete was used or not,
    args['value'] is not split if there's only one argument for rr value and the first char is ". This is ok
    since " cannot show up in a TXT/SPF value unescaped.
    '''
    rr_type = None
    params = ()
    if args['type'] is not None:
        params = [a.name for a in RR_FIELDS[args['type'].lower()]['arguments']]
        rr_type = args['type'].upper()
        if args.get('value'):
            if rr_type in ['TXT', 'SPF']:
                split_value = [args['value']]
            else:
                split_value = shlex.split(args['value'])
            create_rr_arguments = [args['name'], args['type']] + split_value + (
                args['values'] if args.get('values') else [])
            parsed = cmd.parse(['create', 'rr'] + create_rr_arguments)
            if parsed.errors:
                logger.error(parsed.errors[0])
                sys.exit(_exitcode_error(args['detailed']))
            for param in params:
                if parsed.values.get(param):
                    args[param] = parsed.values[param]
                    if param == 'strings' and len(args[param]) == 1 and args[param] and args[param][0][0] == '"':
                        args[param] = args[param][0]
        else:
            for param in params:
                args[param] = None
    _make_delete_rr(rr_type=rr_type, params=params, profile=False, zonearg=zonearg,
                    ignore_references=ignore_references)(self, args)


def _make_show_rr(rr_type, params):
    def show_rr(self, args):
        options = OptionDict()
        options.set_if(type=rr_type,
                       view=args.get('view', None))
        _fill_rr_options(options, rr_type, params, args)
        _print_attributes(self.client.rr_get_attrs(**options),
                          script=False)
    return show_rr


def _make_modify_rr(rr_type, params):
    def modify_rr(self, args):
        options = OptionDict()
        options.set_if(type=rr_type,
                       view=args.view,
                       comment=args.comment)
        if args.ttl is not None:
            if args.ttl.lower() == 'default':
                args.ttl = None
            options['ttl'] = args.ttl
        _fill_rr_options(options, rr_type, params, args)
        attrs = self.client.rr_set_attrs(**options)
        if 'comment' in options:
            _print_attributes(attrs, script=False)
    return modify_rr


def _make_show_ip(status):
    def show_ip(self, args):
        options = OptionDict()
        options.set_if(full=args.full,
                       layer3domain=get_layer3domain(args.layer3domain))
        if status == 'ip':
            options['host'] = True
        else:
            options['status'] = status.capitalize()
        _print_attributes(self.client.ipblock_get_attrs(args.ip, **options), args.script)
    return show_ip


class OptionDict(dict):
    def set_if(self, **kwargs):
        for key, value in kwargs.items():
            if value:
                self[key] = value

    def set_attributes(self, attrs):
        if attrs:
            self['attributes'] = _parse_attributes(attrs)


def _get_soa_attributes(args):
    soa_attributes = dict(list(zip(args['fields'], args['values'])))
    if 'mail' in soa_attributes:
        soa_attributes['mail'] = email2fqdn(soa_attributes['mail'])
    return soa_attributes


def _parse_attributes(cmd_attrs):
    attributes = {}
    for keyval in cmd_attrs:
        m = re.match(r'^(.*?):(.*)$', keyval)
        if not m:
            raise Exception("'%s' must have the form NAME:VALUE" % keyval)
        else:
            attributes[m.group(1)] = m.group(2)
    return attributes


def _check_type_options(block_type):
    type2status = dict(container='Container',
                       subnet='Subnet',
                       delegation='Delegation')
    ret = {}
    if block_type in type2status:
        ret['status'] = type2status[block_type]
    if block_type == 'ip':
        ret['host'] = True
    return ret


def _layer3domain_column(rrs):
    if len(set([rr.get('layer3domain') for rr in rrs if rr.get('layer3domain')])) > 1:
        return ['layer3domain', {}]
    return []


def _parse_query(query):
    if query is None:
        return dict(pool='*')
    elif query.isdigit() and int(query) >= 2 and int(query) <= 4096:
        return dict(vlan=int(query))
    elif re.match(r'^.*/\d{1,3}$', query):
        return dict(cidr=query)
    else:
        return dict(pool=query)


def _utc2local(utc):
    from dateutil import tz
    # If datetime object has microseconds then use this format for ndcli history command
    try:
        utc_datetime = datetime.strptime(utc, '%Y-%m-%d %H:%M:%S.%f')
    except:
        utc_datetime = datetime.strptime(utc, '%Y-%m-%d %H:%M:%S')
    return utc_datetime.replace(tzinfo=tz.tzutc()).astimezone(tz.tzlocal()).strftime(
        '%Y-%m-%d %H:%M:%S' + ('.%f' if utc_datetime.microsecond else ''))


def _local2utc(local):
    if local is None:
        return None
    from dateutil import tz
    local_datetime = datetime.strptime(local, '%Y-%m-%d %H:%M:%S')
    return local_datetime.replace(tzinfo=tz.tzlocal()).astimezone(tz.tzutc()).strftime('%Y-%m-%d %H:%M:%S')


def _convert_attr_times(attrs):
    for attr_name in ('created', 'modified'):
        if attr_name in attrs:
            attrs[attr_name] = _utc2local(attrs[attr_name])
    return attrs


def _convert_table_times(table, columns):
    for row in table:
        for col in columns:
            if col in row:
                if row[col]:
                    row[col] = _utc2local(row[col])
    return table


def _print_attributes(data, script):
    _convert_attr_times(data)
    sep = '\t' if script else '\n'
    print(sep.join('%s:%s' % (k, v) for k, v in sorted(data.items())))


def get_terminal_size():
    def ioctl_GWINSZ(fd):
        try:
            import fcntl
            import termios
            import struct
            cr = struct.unpack('hh', fcntl.ioctl(fd, termios.TIOCGWINSZ, '1234'))
            if cr != (0, 0):
                return cr
        except:
            pass
        return None
    cr = ioctl_GWINSZ(0) or ioctl_GWINSZ(1) or ioctl_GWINSZ(2)
    if not cr:
        try:
            fd = os.open(os.ctermid(), os.O_RDONLY)
            cr = ioctl_GWINSZ(fd)
            os.close(fd)
        except:
            pass
    if not cr:
        try:
            cr = (os.environ['LINES'], os.environ['COLUMNS'])
        except:
            cr = (25, 80)
    return cr


def _print_table(column_desc, rows, script, width=None):
    columns = [name for i, name in enumerate(column_desc) if i % 2 == 0]

    def twobytwo(l):
        i = iter(l)
        return zip(i, i)

    def data():
        if not script:
            yield [a.get('header', n) for n, a in twobytwo(column_desc)]
        for row in rows:
            yield [row[c] if (c in row and row[c] is not None) else '' for c in columns]
    if script:
        for row in data():
            print('\t'.join(str(c) for c in row))
    else:
        if width is None:
            if sys.stdout.isatty():
                _, width = get_terminal_size()
            else:
                width = sys.maxsize
        min_width = [0] * len(columns)
        max_width = [0] * len(columns)
        for row in data():
            for i, val in enumerate(row):
                min_width[i] = max(min_width[i], max(len(s) for s in (str(val).split() or [''])))
                max_width[i] = max(max_width[i], len(str(val)))
        col_width = list(max_width)
        # compress the longest column if it would make the table fit
        min_table_width = sum(min_width) + len(columns) - 1
        max_table_width = sum(max_width) + len(columns) - 1
        if min_table_width <= width < max_table_width:
            longest_idx = max(enumerate(max_width), key=itemgetter(1))[0]
            without_longest_width = max_table_width - max_width[longest_idx]
            if without_longest_width + min_width[longest_idx] <= width:
                col_width[longest_idx] = width - without_longest_width
        align = [cd.get('align', None) for i, cd in enumerate(column_desc) if i % 2 == 1]
        fmt = []
        for i in range(len(columns)):
            align_str = '-' if align[i] != 'r' else ''
            if align[i] != 'r' and i == len(columns) - 1:
                # don't add spaces after the last column
                fmt.append('%s')
            else:
                fmt.append('%' + align_str + str(col_width[i]) + 's')
        for row in data():
            # Using textwrap.wrap(break_on_hyphens=False) in order to avoid quadratic complexity
            # https://bugs.python.org/issue22687
            cells = [textwrap.wrap(str(val), col_width[i], break_on_hyphens=False) for i, val in enumerate(row)]
            height = max(len(c) for c in cells)
            for line in range(height):
                print(' '.join(fmt[i] % (cells[i][line] if line < len(cells[i]) else '') for i in range(len(columns))))


def _print_messages(result):
    logging_levels = {10: logging.DEBUG,
                      20: logging.INFO,
                      25: 25,
                      30: logging.WARNING,
                      40: logging.ERROR}
    for level, message in result['messages']:
        logger.log(logging_levels[level], message)


def _exitcode_error(detailed):
    return 32 if detailed else 1


class CLI(object):
    def __init__(self):
        self._client = None

    def warning(self, message):
        logger.warning(message)
        self._warnings = True

    @property
    def client(self):
        if self._client is None:
            self._client = dim_client(self.args)
        return self._client

    def run(self, argv):
        logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
        logging.addLevelName(25, 'RECORD')
        cmd.handle_shell_completion()
        self._warnings = False

        def exitcode_warning():
            return 1 if args.detailed else 0

        def exitcode_error():
            return _exitcode_error(args.detailed)

        try:
            parsed = cmd.parse(argv[1:])
            args = self.args = parsed.values

            # Global options
            if args.help:
                cmd.print_help(parsed.subcommands)
                return 0
            if args.version:
                print("ndcli version", __version__)
                return 0
            log_levels = 0
            if args.quiet:
                log_levels += 1
                logging.getLogger().setLevel(logging.ERROR)
            if args.debug:
                log_levels += 1
                logging.getLogger().setLevel(logging.DEBUG)
            if args.warnings:
                log_levels += 1
                logging.getLogger().setLevel(logging.WARNING)
            if log_levels > 1:
                raise Exception('--quiet, --warnings and --debug are mutually exclusive')
            elif log_levels == 0:
                logging.getLogger().setLevel(logging.INFO)
            if parsed.errors:
                # print only the first parser error
                logger.error(parsed.errors[0])
                cmd.print_help(parsed.subcommands)
                return exitcode_error()

            # Run command
            if args.get('dryrun', False):
                logger.info('Dryrun mode, no data will be modified')
            args.run(self, args)

            if self._warnings:
                return exitcode_warning()
            else:
                return 0
        except Exception as e:
            logger.error(str(e))
            logger.debug('trace', exc_info=True)
            return exitcode_error()

    def _get_delegation(self, args, method, from_object, options):
        options.set_if(full=args.full)
        options.set_attributes(args.attributes)
        if args.maxsplit is not None:
            options['maxsplit'] = int(args.maxsplit)
        prefix = int(args.prefixsize.lstrip('/'))
        delegations = getattr(self.client, method)(from_object, prefix, **options)
        if delegations:
            for i, delegation in enumerate(delegations):
                if not args.script and i > 0:
                    print()
                _print_attributes(delegation, args.script)
        else:
            raise Exception("No suitable blocks found")

    def _mark_ip(self, args):
        options = OptionDict(pool=args.poolname)
        options.set_attributes(args.attributes)
        options.set_if(full=args.full,
                       delegation=args.get('delegation', None))
        # set layer3domain only if pool is unset, as pools have a layer3domain already
        if args.poolname == None:
             options.set_if(layer3domain=get_layer3domain(args.layer3domain))
        _print_attributes(self.client.ip_mark(args.ip, **options), args.script)

    def _free_ip(self, args):
        options = OptionDict(pool=args.poolname,
                             include_messages=True)
        options.set_if(reserved=args.force,
                       delegation=args.get('delegation', None))
        # set layer3domain only if pool is unset, as pools have a layer3domain already
        if args.poolname == None:
             options.set_if(layer3domain=get_layer3domain(args.layer3domain))
        result = self.client.ip_free(args.ip, **options)
        _print_messages(result)
        freed = result['freed']
        if freed == 0:
            logger.info("%s was already free" % args.ip)
        elif freed == -1:
            self.warning("%s is Reserved; you must use --force to free it" % args.ip)

    def _modify_pool_remove_ipblock(self, args):
        if args.cleanup and not args.force:
            raise Exception('--cleanup does not work without --force')
        options = OptionDict(pool=args.poolname,
                             status=args.status,
                             include_messages=True)
        options.set_if(force=args.force,
                       recursive=args.cleanup)
        result = self.client.ipblock_remove(args.block, **options)
        _print_messages(result)
        if not result['deleted']:
            self.warning("%(status)s %(block)s still contains objects" % args)

    def _print_get_ip_data(self, ip_data, args):
        if ip_data:
            _print_attributes(ip_data, args.script)
        else:
            raise Exception("No free IP address found")

    @cmd.register('show server-info')
    def show_server_info(self, args):
        _print_attributes(self.client.server_info(), script=False)

    @cmd.register('login')
    def login(self, args):
        self.client.get_username()

    @cmd.register('create layer3domain',
                  Argument('name'),
                  Token('type'),
                  Argument('type'),
                  Group(Token('rd'), Argument('rd'), nargs='?'),
                  Group(Token('comment'), Argument('comment'), nargs='?'))
    def create_layer3domain(self, args):
        options = OptionDict()
        options.set_if(comment=args.comment)
        options.set_if(rd=args.rd)
        self.client.layer3domain_create(args.name, args.type, **options)

    @cmd.register('modify layer3domain set comment',
                  Argument('comment'))
    def modify_layer3domain_set_comment(self, args):
        '''
        Set the comment for LAYER3DOMAIN.
        '''
        self.client.layer3domain_set_comment(args.layer3domain, args.comment)

    @cmd.register('modify layer3domain set rd',
                  Argument('rd'))
    def modify_layer3domain_set_rd(self, args):
        '''
        Set the rd for LAYER3DOMAIN.
        '''
        options = OptionDict()
        options.set_if(rd=args.rd)
        self.client.layer3domain_set_attrs(args.layer3domain, **options)

    @cmd.register('modify layer3domain set type',
                    Argument('type'),
                    Group(Token('rd'), Argument('rd'), nargs='?'))
    def modify_layer3domain_set_type(self, args):
        '''
        Set the type for LAYER3DOMAIN.
        '''
        options = OptionDict()
        options.set_if(rd=args.rd)
        self.client.layer3domain_set_type(args.layer3domain, args.type, **options)

    @cmd.register('delete layer3domain',
                  layer3domain_arg)
    def delete_layer3domain(self, args):
        self.client.layer3domain_delete(args.layer3domain)

    @cmd.register('rename layer3domain',
                  Argument('oldname', completions=complete_layer3domain),
                  Token('to'),
                  Argument('newname'))
    def rename_layer3domain(self, args):
        self.client.layer3domain_rename(args.oldname, args.newname)

    @cmd.register('show layer3domain',
                  layer3domain_arg,
                  script_option,
                  help='show LAYER3DOMAIN')
    def show_layer3domain(self, args):
        _print_attributes(self.client.layer3domain_get_attrs(args.layer3domain), script=args.script)


    @cmd.register('list layer3domains',
                  script_option,
                  help='list of layer3domains')
    def list_layer3domains(self, args):
        _print_table(['type', {},
                      'name', {},
                      'properties', {},
                      'comment', {}],
                     [{'name': l['name'],
                       'type': l['type'],
                       'properties': ' '.join('%s:%s' % (k, v) for k, v in l['properties'].items()) if 'properties' in l else '',
                       'comment': l['comment']} for l in self.client.layer3domain_list()],
                     script=args.script)

    @cmd.register('create pool',
                  Argument('poolname'),
                  Group(Token('vlan'), Argument('vlan'), nargs='?'),
                  Group(Token('owning-user-group'), group_arg, nargs='?'),
                  layer3domain_group,
                  attributes_arg)
    def create_pool(self, args):
        '''
        Creates the pool POOLNAME. The type of the pool (IPv4 or IPv6) will be
        decided when the first subnet is added to it.
        '''
        options = OptionDict()
        options.set_if(owner=args.group,
                       layer3domain=get_layer3domain(args.layer3domain))
        options.set_attributes(args.attributes)
        if args.vlan is not None:
            options['vlan'] = int(args.vlan)
        self.client.ippool_create(args.poolname, **options)

    @cmd.register('create container',
                  Argument('container'),
                  layer3domain_group,
                  attributes_arg)
    def create_container(self, args):
        '''
        Creates a container.
        '''
        options = OptionDict(status='Container')
        options.set_if(layer3domain=get_layer3domain(args.layer3domain))
        options.set_attributes(args.attributes)
        attrs = self.client.ipblock_create(args.container, **options)
        logger.log(logging.INFO, 'Creating container %s in layer3domain %s' % (attrs['ip'], attrs['layer3domain']))

    @cmd.register('modify pool set attrs',
                  attributes_arg,
                  help='set attributes for POOLNAME')
    def modify_pool_set_attrs(self, args):
        '''
        Set attributes for POOLNAME.

        Special attributes:

        assignmentsize

          Used by `list pool` to show an estimate for the number of /assignmentsize
          blocks (the free and total columns). Must be an integer between 0 and 32 or
          between 0 and 128 (depending on the IP version of POOLNAME).

        allocation_strategy

          Strategy used for `get ip`. Possible values are:

          * "first": the first available candidate is selected (the default)
          * "random": a random candidate is selected
        '''
        self.client.ippool_set_attrs(args.poolname, _parse_attributes(args.attributes))

    @cmd.register('modify pool set vlan',
                  Argument('vlan'),
                  help='set vlan for POOLNAME')
    def modify_pool_set_vlan(self, args):
        '''
        Sets the vlan for POOLNAME.
        '''
        self.client.ippool_set_vlan(args.poolname, int(args.vlan))

    @cmd.register('modify pool owning-user-group', group_arg)
    def modify_pool_owning_user_group(self, args):
        '''
        Sets the pool owning user group.
        '''
        self.client.ippool_set_owner(args.poolname, args.group)

    @cmd.register('modify pool set owning-user-group',
            group_arg,
            help='set owner group for POOLNAME')
    def modify_pool_set_owning_user_group(self, args):
        '''
        Sets the pool owning user group.
        '''
        self.client.ippool_set_owner(args.poolname, args.group)

    @cmd.register('modify pool set layer3domain',
            layer3domain_arg,
            help='set new layer3domain for POOLNAME')
    def modify_pool_set_layer3domain(self, args):
        '''
        Changes the layer3domain of a pool and all its dependencies.
        '''
        result = self.client.ippool_set_layer3domain(args.poolname, args.layer3domain)
        if result:
            _print_messages(result)

    @cmd.register('modify pool remove owning-user-group',
            help='unset owner group for POOLNAME')
    def modify_pool_set_owning_user_group(self, args):
        '''
        Unsets the pool owning user group.
        '''
        self.client.ippool_unset_owner(args.poolname)

    @cmd.register('modify pool remove attrs',
                  attr_names_arg,
                  help='remove attributes from POOLNAME')
    def modify_pool_remove_attrs(self, args):
        '''
        Removes the specified attributes from POOLNAME.
        '''
        self.client.ippool_delete_attrs(args.poolname, args.attr_names)

    @cmd.register('modify pool remove vlan',
                  help='remove vlan from POOLNAME')
    def modify_pool_remove_vlan(self, args):
        '''
        Removes the vlan from POOLNAME.
        '''
        self.client.ippool_remove_vlan(args.poolname)

    @cmd.register('modify pool remove subnet',
                  Argument('block', metavar='SUBNET', completions=complete_subnet),
                  Option('f', 'force',
                         help='delete the subnet even if it still has ip allocations'),
                  Option('c', 'cleanup',
                         help='also delete all ip allocations'),
                  defaults=dict(status='Subnet'),
                  help='remove a subnet from POOLNAME')
    def modify_pool_remove_subnet(self, args):
        '''
        Removes SUBNET from POOLNAME.
        '''
        self._modify_pool_remove_ipblock(args)

    @cmd.register('modify pool remove delegation',
                  Argument('block', metavar='DELEGATION'),
                  Option('f', 'force',
                         help='delete the delegation even if it still has ip allocations'),
                  Option('c', 'cleanup',
                         help='also delete all ip allocations'),
                  defaults=dict(status='Delegation'),
                  help='delete delegation')
    def modify_pool_remove_delegation(self, args):
        '''
        Deletes DELEGATION.
        '''
        self._modify_pool_remove_ipblock(args)

    @cmd.register('modify pool get ip',
                  attributes_arg,
                  full_option,
                  script_option,
                  help='allocate ip from POOLNAME')
    def modify_pool_get_ip(self, args):
        '''
        Allocates a single IP address from POOLNAME.
        '''
        options = OptionDict()
        options.set_attributes(args.attributes)
        options.set_if(full=args.full)
        ip_data = self.client.ippool_get_ip(args.poolname, **options)
        self._print_get_ip_data(ip_data, args)

    @cmd.register('modify pool get delegation',
                  Argument('prefixsize'),
                  Group(Token('maxsplit'), Argument('maxsplit'), nargs='?'),
                  attributes_arg,
                  full_option,
                  script_option,
                  help='allocate delegation from POOLNAME')
    def modify_pool_get_delegation(self, args):
        '''
        Allocates one or more delegations from POOLNAME that have combined the same
        number of IP addresses as a single block with the prefix PREFIXSIZE.

        If no continuous block with prefix length PREFIXSIZE can be found, the returned
        blocks can have prefixes with at most MAXSPLIT bits added to PREFIXSIZE.
        '''
        self._get_delegation(args,
                             method='ippool_get_delegation',
                             from_object=args.poolname,
                             options=OptionDict())

    @cmd.register('modify pool mark ip',
                  Argument('ip'),
                  attributes_arg,
                  full_option,
                  script_option,
                  help="mark ip from POOLNAME as allocated")
    def modify_pool_mark_ip(self, args):
        '''
        Mark IP as allocated (with status 'Static').
        '''
        self._mark_ip(args)

    @cmd.register('modify pool mark delegation',
                  Argument('delegation'),
                  attributes_arg,
                  Option('f', 'force', help="mark DELEGATION even if it has IPs allocated"),
                  full_option,
                  script_option,
                  help="mark DELEGATION from POOLNAME")
    def modify_pool_mark_delegation(self, args):
        '''
        Mark DELEGATION.
        '''
        options = OptionDict(pool=args.poolname,
                             status="Delegation")
        options.set_if(disallow_children=not args.force,
                       full=args.full)
        options.set_attributes(args.attributes)
        _print_attributes(self.client.ipblock_create(args.delegation, **options), args.script)

    @cmd.register('modify pool free ip',
                  Argument('ip'),
                  Option('f', 'force', help='free IP even if it has the Reserved status'),
                  help="mark ip from POOLNAME as available")
    def modify_pool_free_ip(self, args):
        '''
        Marks IP with status 'Available'.
        '''
        self._free_ip(args)

    @cmd.register('modify pool add subnet',
                  Argument('subnet'),
                  Group(Token('gw'), Argument('gw'), nargs='?'),
                  attributes_arg,
                  Option(None, 'allow-move',
                         help="move the subnet to POOLNAME even if it's part of another pool"),
                  overlap_option,
                  Option(None, 'no-default-reserve',
                         help="don't reserve network and broadcast addresses"),
                Option(None, 'no-reserve-class-c-boundaries',
                         help="don't reserve class C boundaries"),
                  help='(create and) add a subnet to POOLNAME')
    def modify_pool_add_subnet(self, args):
        '''
        Add SUBNET to POOLNAME. If the subnet does not exist, it is created.

        If the subnet exists, and it is part of a different pool, --allow-move must be
        specified to move it to POOLNAME.

        If the subnet overlaps with other IP blocks in other layer3domains
        and the overlap IP space is whitelisted, --allow-overlap must be specified.

        SUBNET will have a priority lower than any of the existing subnets in POOLNAME.

        If the subnet did not previously exist, the corresponding reverse zones are also
        created. The zone profile used for creating the reverse zones is taken from the
        reverse_dns_profile attribute of an ancestor ipblock.
        '''
        
        options = OptionDict(include_messages=True)
        options.set_attributes(args.attributes)
        options.set_if(gateway=args.gw,
                       allow_move=args['allow-move'],
                       allow_overlap=args['allow-overlap'],
                       dont_reserve_network_broadcast=args['no-default-reserve'],
                       no_reserve_class_c_boundaries=args['no-reserve-class-c-boundaries'])
        result = self.client.ippool_add_subnet(args.poolname, args.subnet, **options)
        if result['created']:
            logger.info("Created subnet %s in layer3domain %s" % (args.subnet, result['layer3domain']))
        _print_messages(result)

    @cmd.register('modify container move to', Argument('to_layer3domain'))
    def ipblock_move_to(self, args):
        result = self.client.ipblock_move_to(
			args.container,
			get_layer3domain(args.get('layer3domain')),
			args.get('to_layer3domain'))
        _print_messages(result)

    # modify [block_type] set/remove attrs
    def ipblock_set_attrs(self, args):
        options = OptionDict(_check_type_options(args.block_type))
        options.set_if(pool=args.get('poolname', None))
        # set layer3domain only if block is not a pool, as those have a layer3domain already
        if args.get('poolname', None) == None:
            options.set_if(layer3domain=get_layer3domain(args.layer3domain))
        self.client.ipblock_set_attrs(args[args.block_type],
                                      _parse_attributes(args.attributes),
                                      **options)

    def ipblock_remove_attrs(self, args):
        options = OptionDict(_check_type_options(args.block_type))
        options.set_if(pool=args.get('poolname', None))
        # set layer3domain only if block is not a pool, as those have a layer3domain already
        if args.get('poolname', None) == None:
            options.set_if(layer3domain=get_layer3domain(args.layer3domain))
        self.client.ipblock_delete_attrs(args[args.block_type],
                                         args.attr_names,
                                         **options)
    cmd.register('modify container set attrs',
                 attributes_arg,
                 help='set attributes for CONTAINER')(ipblock_set_attrs)
    cmd.register('modify container remove attrs',
                 attr_names_arg,
                 help='remove attributes from CONTAINER')(ipblock_remove_attrs)
    for block_type in ('subnet', 'delegation', 'ip'):
        cmd.register('modify pool %s set attrs' % block_type,
                     attributes_arg,
                     help='set attributes for %s' % block_type.upper())(ipblock_set_attrs)
        cmd.register('modify pool %s remove attrs' % block_type,
                     attr_names_arg,
                     help='remove attributes from %s' % block_type.upper())(ipblock_remove_attrs)

    @cmd.register('modify pool subnet set prio',
                  Argument('priority'),
                  help='set allocator priority for SUBNET')
    def modify_pool_subnet_set_prio(self, args):
        '''
        Set the priority of SUBNET to PRIORITY. If there is a subnet with the same priority in
        POOLNAME, it is demoted to a lower priority.
        '''
        self.client.subnet_set_priority(args.subnet,
                                        int(args.priority),
                                        pool=args.poolname)

    @cmd.register('modify pool subnet set gw',
                  Argument('gateway'),
                  help='set gateway for SUBNET')
    def modify_pool_subnet_set_gw(self, args):
        '''
        Set the default gateway of SUBNET to GATEWAY.
        '''
        self.client.subnet_set_gateway(args.subnet,
                                       args.gateway,
                                       pool=args.poolname)

    @cmd.register('modify pool subnet remove gw',
                  help='remove gateway from SUBNET')
    def modify_pool_subnet_remove_gw(self, args):
        '''
        Remove the default gateway from SUBNET.
        '''
        self.client.subnet_remove_gateway(args.subnet,
                                          pool=args.poolname)

    @cmd.register('modify pool subnet get delegation',
                  Argument('prefixsize'),
                  Group(Token('maxsplit'), Argument('maxsplit'), nargs='?'),
                  attributes_arg,
                  full_option,
                  script_option,
                  help='allocate delegation from SUBNET')
    def modify_pool_subnet_get_delegation(self, args):
        '''
        Allocates one or more delegations from SUBNET that have combined the same number
        of IP addresses as a single block with the prefix PREFIXSIZE.

        If no continuous block with prefix length PREFIXSIZE can be found, the returned
        blocks can have prefixes with at most MAXSPLIT bits added to PREFIXSIZE.
        '''
        self._get_delegation(args,
                             method='ipblock_get_delegation',
                             from_object=args.subnet,
                             options=OptionDict(status='Subnet',
                                                pool=args.poolname))

    @cmd.register('modify pool delegation mark ip',
                  Argument('ip'),
                  attributes_arg,
                  full_option,
                  script_option,
                  help="mark ip from DELEGATION as allocated")
    def modify_delegation_mark_ip(self, args):
        '''
        Mark IP as allocated (with status 'Static').
        '''
        self._mark_ip(args)

    @cmd.register('modify pool delegation get ip',
                  attributes_arg,
                  full_option,
                  script_option,
                  help='allocate ip from DELEGATION')
    def modify_delegation_get_ip(self, args):
        '''
        Allocates a single IP address from DELEGATION.
        '''
        options = OptionDict(pool=args.poolname,
                             status='Delegation')
        options.set_attributes(args.attributes)
        options.set_if(full=args.full)
        ip_data = self.client.ipblock_get_ip(args.delegation, **options)
        self._print_get_ip_data(ip_data, args)

    @cmd.register('modify pool delegation free ip',
                  Argument('ip'),
                  Option('f', 'force', help='free IP even if it has the Reserved status'),
                  help="mark ip from DELEGATION as available")
    def modify_pool_delegation_free_ip(self, args):
        '''
        Marks IP with status 'Available'.
        '''
        self._free_ip(args)

    @cmd.register('rename pool',
                  Argument('oldname', completions=complete_poolname),
                  Token('to'),
                  Argument('newname'))
    def rename_pool(self, args):
        '''
        Renames a pool.
        '''
        self.client.ippool_rename(args.oldname, args.newname)

    @cmd.register('delete pool',
                  Argument('poolname', completions=complete_poolname),
                  Option('f', 'force', help='force delete non-empty pools'),
                  help='delete pool')
    def delete_pool(self, args):
        forceDelete = False
        if args.force is not None:
            forceDelete = args.force
        subnets = self.client.ippool_get_subnets(args.poolname)
        if len(subnets) != 0 and not forceDelete:
            raise Exception('You cannot delete pool because it contains %d subnet(s). However, you can use --force option to delete it.' % len(subnets))

        self.client.ippool_delete(args.poolname, force=forceDelete)

    @cmd.register('delete container',
                  Argument('container'),
                  layer3domain_group,
                  help='delete CONTAINER')
    def delete_container(self, args):
        '''
        Deletes CONTAINER. Any blocks inside the deleted container are left intact.
        '''
        options = OptionDict(include_messages=True)
        options.set_if(layer3domain=get_layer3domain(args.layer3domain))
        result = self.client.ipblock_remove(args.container, force=True, status='Container', **options)
        _print_messages(result)

    @cmd.register('list pools',
                  layer3domain_group,
                  Argument('query', metavar='VLANID|CIDR|POOL', nargs='?'),
                  Option('o', 'can-allocate', help='limit results to pools where you can allocate'),
                  Option('a', 'attributes',
                         help='comma-separated list of attributes to display for each pool (default: name,vlan,subnets,layer3domain)',
                         action='store',
                         dest='attr_names',
                         default='name,vlan,subnets,layer3domain'),
                  full_option,
                  script_option,
                  help='list of pools',
                  description='Displays the list of matching pools.\n\n' + query_description)
    def list_pools(self, args):
        attr_names = args.attr_names.split(',')
        options = OptionDict(include_subnets=True,attributes=attr_names)
        options.set_if(full=args.full)
        options.set_if(can_allocate=args['can-allocate'])
        layer3domain = get_layer3domain(args.layer3domain)
        if layer3domain not in ['all', None]:
            options.set_if(layer3domain=layer3domain)
        options.update(_parse_query(args.query))
        pools = self.client.ippool_list(**options)
        for pool in pools:
            pool['subnets'] = ' '.join(pool['subnets'])
        if args.query:
            logger.info('Result for list pools %s', args.query)
        _print_table(sum(([a,{}] for a in attr_names), []),
                     sorted(pools, key=itemgetter('name')),
                     script=args.script)

    @cmd.register('list pool subnets',
                  help='list subnets from a pool')
    def list_pool_subnets(self, args):
        '''
        Displays the list of subnets from POOLNAME.

        The subnets are sorted by priority.
        '''
        options = OptionDict(include_usage=True)
        options.set_if(full=args.full)
        subnets = self.client.ippool_get_subnets(args.poolname, options)
        assignmentsize = self.client.ippool_get_attrs(args.poolname).get('assignmentsize', '')
        if assignmentsize.isdigit():
            assignmentsize = int(assignmentsize)
        else:
            assignmentsize = None
        div_header = ''
        total_bits = None
        if subnets:
            total_bits = 128 if subnets[0]['subnet'].count(':') else 32
        if total_bits and assignmentsize and assignmentsize <= total_bits and assignmentsize >= 0:
            div_header = "(/%d)" % assignmentsize
            div_bits = total_bits - assignmentsize
            div_addrs = 2 ** div_bits
            for subnet in subnets:
                subnet['free'] = int(subnet['free'] / div_addrs)
                subnet['total'] = int(subnet['total'] / div_addrs)
        _print_table(['priority', dict(header='prio', align='r'),
                      'subnet',   {},
                      'gateway',  {},
                      'free',     dict(header='free' + div_header, align='r'),
                      'total',    dict(header='total' + div_header, align='r')],
                     subnets,
                     script=args.script)
        total_free = sum(s['free'] for s in subnets)
        if not args.script:
            logger.info("Total free IPs: %d" % total_free)

    @cmd.register('list pool delegations',
                  help='list delegations from a pool')
    def list_pool_delegations(self, args):
        '''
        Displays the list of delegations from POOLNAME.
        '''
        options = OptionDict(include_usage=True)
        options.set_if(full=args.full)
        delegations = self.client.ippool_get_delegations(args.poolname, options)
        _print_table(['delegation', {},
                      'free',       dict(align='r'),
                      'total',      dict(align='r')],
                     delegations,
                     script=args.script)
        total_free = sum(s['free'] for s in delegations)
        logger.info("Total free IPs: %d" % total_free)

    @cmd.register('list pool rights',
                  help='list rights for a pool')
    def list_pool_rights(self, args):
        '''
        Displays the list of rights for POOLNAME.
        '''
        _print_table(['action', {},
                      'object', {},
                      'group', {}],
                     self.client.ippool_get_access(args.poolname),
                     script=args.script)

    @cmd.register('list vlans',
                  script_option,
                  help='list of vlans')
    def list_vlans(self, args):
        '''
        Displays the list of vlans used by pools.
        '''
        options = OptionDict(pool='*', include_subnets=False)
        pools = self.client.ippool_list(**options)
        vlans = {}
        for pool in pools:
            vlans.setdefault(pool['vlan'] or '', []).append(pool['name'])
        vlan_list = sorted(list(vlans.keys()), key=lambda x: x if x else 0)
        table = [dict(vlan=v, pools=' '.join(vlans[v])) for v in vlan_list]
        _print_table(['vlan',  dict(align='r'),
                      'pools', dict()],
                     table,
                     script=args.script)

    @cmd.register('list containers',
                  layer3domain_group,
                  Argument('container', nargs='?'),
                  help='list the children of a container')
    def list_containers(self, args):
        def print_tree(tree, level: int):
            for node in tree:
                attributes = ['(%s)' % node['status']]
                if 'pool' in node:
                    attributes.append('pool:%s' % node['pool'])
                attributes.extend(['%s:%s' % (k, v) for k, v in sorted(node.get('attributes', {}).items())])
                print('  ' * level + '%s %s' % (node['ip'], ' '.join(attributes)))
                if 'children' in node:
                    print_tree(node['children'], level + 1)
        layer3domains: List[str] = []
        if args.layer3domain == "all" or get_layer3domain(args.layer3domain) is None:
            layer3domains = [l['name'] for l in self.client.layer3domain_list()]
        else:
            layer3domains = [get_layer3domain(args.layer3domain)]
        # loop through layer3domains and collect results,
        # so we can raise an error if there are no results at all
        results = OrderedDict()
        dim_errors = []
        for layer3domain in layer3domains:
            options = OptionDict(include_messages=True)
            options.set_if(container=args.container)
            options.set_if(layer3domain=layer3domain)
            try:
                results[layer3domain] = self.client.container_list(**options)
            except Exception as e:
                if e.code == 3: # InvalidIPError
                    dim_errors.append(e)
                    next
                else:
                    raise
        if len(dim_errors) == len(layer3domains):
            # raise an error if there are no results at all
            raise DimError('; '.join([str(e) for e in dim_errors]), code=3) # InvalidIPError
        elif len(dim_errors) > 0:
            for dimerror in dim_errors:
                logging.debug(dimerror)
        idx = 0
        for layer3domain, result in results.items():
            if idx > 0:
                print()
            _print_messages(result)
            if result['containers']:
                print('layer3domain: ' + layer3domain)
                print_tree(result['containers'], 0)
                idx += 1

    @cmd.register('list ips',
                  Argument('query', metavar='VLANID|CIDR|POOL'),
                  Group(Token('status'),
                        Argument('status', choices=['all', 'used', 'free']),
                        nargs='?'),
                  Option('L', 'limit',
                         help='max number of results (default: 10)',
                         action='store',
                         default='10'),
                  Option('a', 'attributes',
                         help='comma-separated list of attributes to display for each ip (default: ip,status,ptr_target,comment)',
                         action='store',
                         dest='attr_names',
                         default='ip,status,ptr_target,comment'),
                  layer3domain_group,
                  full_option,
                  script_option,
                  help='list of ips',
                  description=query_description + '''

Note: The set of attributes returned for each IP does not include the inherited
system attributes (like subnet, mask, prefixlength, pool, gateway or
delegation).''')
    def list_ips(self, args):
        attr_names = args.attr_names.split(',')
        options = OptionDict(type=args.status or 'all',
                             limit=int(args.limit) + 1,
                             layer3domain=get_layer3domain(args.layer3domain),
                             attributes=attr_names)
        options.set_if(full=args.full)
        options.update(_parse_query(args.query))
        ips = self.client.ip_list_all(**options)
        more = False
        if len(ips) > int(args.limit):
            more = True
            ips.pop()
        for query_type in ('pool', 'vlan', 'cidr'):
            if query_type in options:
                logger.info("Result for list ips %s" % options[query_type])
        _print_table(sum(([a, {}] for a in attr_names), []) + _layer3domain_column(ips),
                     ips,
                     script=args.script)
        if more:
            logger.warn("More results available")

    @cmd.register('show ipblock',
                  Argument('ip'),
                  layer3domain_group,
                  script_option,
                  full_option,
                  help="show ipblock attributes")
    def show_ipblock(self, args):
        '''
        Prints the attributes of any ipblock (single ip, delegation, subnet or container).
        '''
        options = OptionDict()
        options.set_if(full=args.full,
                       layer3domain=get_layer3domain(args.layer3domain))
        _print_attributes(self.client.ipblock_get_attrs(args.ip, **options), args.script)

    for status in ('ip', 'delegation', 'subnet', 'container'):
        description = 'single ip' if status == 'ip' else status
        cmd.register('show %s' % status,
                     Argument('ip', metavar=status.upper()),
                     layer3domain_group,
                     script_option,
                     full_option,
                     help="show %s attributes" % status,
                     description='Prints the attributes of a %s.' % description)(_make_show_ip(status))

    @cmd.register('show pool',
                  Argument('poolname', completions=complete_poolname),
                  script_option,
                  help='show pool attributes')
    def show_pool(self, args):
        '''
        Displays pool attributes.
        '''
        _print_attributes(self.client.ippool_get_attrs(args.poolname), args.script)

    # User rights

    @cmd.register('create user-group',
                  Argument('usergroup'),
                  Argument('department', completions=complete_department, nargs='?'),
                  help='create user group')
    def create_group(self, args):
        '''
        Create a user group. If a department is specified, it will be linked to the user-group.
        '''
        options = OptionDict()
        if args.department:
            department_number = self.client.department_number(args.department)
            if department_number is None:
                raise Exception('Department %s not found' % args.department)
            options.set_if(department_number=department_number)
        self.client.group_create(args.usergroup, **options)

    @cmd.register('delete user-group',
                  group_arg,
                  help='delete user group')
    def delete_group(self, args):
        self.client.group_delete(args.group)

    @cmd.register('modify user-group add user',
                  user_arg,
                  help='add USER to USERGROUP')
    def modify_group_add_user(self, args):
        self.client.group_add_user(args.group, args.user)

    @cmd.register('modify user-group remove user',
                  Argument('user', completions=complete_username_from_group),
                  help='remove USER from USERGROUP')
    def modify_group_remove_user(self, args):
        self.client.group_remove_user(args.group, args.user)

    @cmd.register('modify user-group set department',
                  Argument('department', completions=complete_department),
                  help='set department for USERGROUP. Only one department can be set for a user-group')
    def modify_group_set_department(self, args):
        department_number = self.client.department_number(args.department)
        if department_number is None:
            raise Exception('Department %s not found' % args.department)
        self.client.group_set_department_number(args.group, department_number)

    @cmd.register('modify user-group remove department',
                  help='remove department number for USERGROUP, unlinking it from LDAP')
    def modify_group_remove_department(self, args):
        self.client.group_set_department_number(args.group, None)

    def _make_grant_access(right, obj=None):
        def f(self, args):
            extra_args = obj(args) if obj else ()
            self.client.group_grant_access(args.group, right, *extra_args)
        return f

    def _make_revoke_access(right, obj=None):
        def f(self, args):
            extra_args = obj(args) if obj else ()
            self.client.group_revoke_access(args.group, right, *extra_args)
        return f

    RIGHTS = {
        'network_admin': dict(desc='full access to all pools'),
        'dns_admin': dict(desc='full access to all zones'),
        'dns_update_agent': dict(desc='ability to delete output updates'),
        'zone_create': dict(desc='ability to create forward zones'),
        'attr': dict(desc='set attributes on pools with the specified prefix',
                arguments=(Argument('prefix'), Argument('poolname', completions=complete_poolname), ),
                extra=lambda args: ([args.prefix, args.poolname], )),
        'allocate':
            dict(desc='modify the contents of subnets from POOLNAME',
                 arguments=(Argument('poolname', completions=complete_poolname), ),
                 extra=lambda args: (args.poolname, )),
        'zone_admin':
            dict(desc='full access to zone',
                 arguments=(zone_arg, ),
                 extra=lambda args: (args.zonename, )),
        'create_rr':
            dict(desc='create rr',
                 arguments=(zone_arg, zoneview_group),
                 extra=lambda args: [(args.zonename, args.view)]),
        'delete_rr':
            dict(desc='delete rr',
                 arguments=(zone_arg, zoneview_group),
                 extra=lambda args: [(args.zonename, args.view)])}
    for right, prop in RIGHTS.items():
        obj = prop.get('extra', None)
        args = prop.get('arguments', [])
        cmd.register('modify user-group grant ' + right, *args,
                     **dict(help='grant ' + prop['desc']))\
                    (_make_grant_access(right, obj))
        cmd.register('modify user-group revoke ' + right, *args,
                     **dict(help='grant ' + prop['desc']))\
                    (_make_revoke_access(right, obj))

    @cmd.register('show user-group',
                  group_arg,
                  script_option,
                  help='show USERGROUP')
    def show_group(self, args):
        _print_attributes(self.client.group_get_attrs(args.group), script=args.script)

    @cmd.register('rename user-group',
                  Argument('oldname'),
                  Token('to'),
                  Argument('newname'))
    def rename_group(self, args):
        '''
        Renames a user group.
        '''
        self.client.group_rename(args.oldname, args.newname)

    @cmd.register('list user-groups',
                  script_option,
                  help='list of user groups')
    def list_groups(self, args):
        _print_table(['name', {}],
                     [{'name': g} for g in self.client.group_list()],
                     script=args.script)

    @cmd.register('list user-group users',
                  Option('l', 'ldap', default=False, help='display ldap queries'),
                  script_option,
                  help='list of users from USERGROUP')
    def list_group_users(self, args):
        column_desc = ['username', {}]
        if args.ldap:
            column_desc += ['ldap_cn', {},
                            'ldap_uid', {},
                            'department_number', {}]
        users = self.client.group_get_users(args.group, include_ldap=args.ldap)
        if not args.ldap:
            users = [{'username': u} for u in users]
        _print_table(column_desc,
                     sorted(users, key=itemgetter('username')),
                     script=args.script)

    @cmd.register('list user-group rights',
                  script_option,
                  help='list of rights granted to USERGROUP')
    def list_group_rights(self, args):
        _print_table(['action', {},
                      'object', {}],
                     [{'action': a[0],
                       'object': a[1]} for a in self.client.group_get_access(args.group)],
                     script=args.script)

    @cmd.register('list user-group pools',
                  script_option,
                  help='list pools owned by USERGROUP')
    def list_group_pools(self, args):
        pools = self.client.ippool_list(owner=args.group, include_subnets=False)
        _print_table(['name', {}],
                     sorted(pools, key=itemgetter('name')),
                     script=args.script)

    @cmd.register('list user-group zones',
                  script_option,
                  Option('L', 'limit',
                         help='limit the number of results (default: 10)',
                         action='store',
                         default=10),
                  help='list zones owned by USERGROUP')
    def list_group_zones(self, args):
        self._list_zones(args, pattern=None, profile=False, fields=False)

    @cmd.register('list user rights',
                  script_option,
                  help='show rights of USER')
    def list_user_rights(self, args):
        groups = self.client.user_get_groups(args.user)
        result = []
        for group in groups:
            rights = self.client.group_get_access(group)
            for right in rights:
                result.append(dict(group=group, object=right[1], right=right[0]))
            if len(rights) == 0:
                result.append(dict(group=group))
        _print_table(['group', {},
                      'object', {},
                      'right', {}],
                     result,
                     script=args.script)

    @cmd.register('list user groups',
                  script_option,
                  help='show groups of USER')
    def list_user_groups(self, args):
        _print_table(['group', {}],
                     [{'group': g} for g in self.client.user_get_groups(args.user)],
                     script=args.script)

    @cmd.register('list users',
                  script_option)
    def list_users(self, args):
        users = self.client.user_list(include_groups=True)
        for user in users:
            user['groups'] = ' '.join(user['groups'])
        _print_table(['name', {},
                      'groups', {}],
                     users,
                     script=args.script)

    @cmd.register('show user',
                  Argument('user', completions=complete_username),
                  script_option,
                  help='show USER attributes')
    def show_user(self, args):
        _print_attributes(self.client.user_get_attrs(args.user),
                          script=args.script)

    #
    # DNS
    #
    @cmd.register('create zone',
                  Argument('zonename'),
                  Group(Token('profile'), zoneprofile_arg, nargs='?'),
                  Group(Token('owning-user-group'), group_arg, nargs='?'),
                  dns_field_value_group,
                  Option(None, 'no-inherit', help='do not inherit anything from parent zone'),
                  Option(None, 'no-inherit-zone-groups', help='do not inherit zone-group membership from parent zone'),
                  Option(None, 'no-inherit-rights', help='do not inherit user rights from parent zone'),
                  Option(None, 'no-inherit-owner', help='do not inherit owner from parent zone'),
                  attributes_arg)
    def create_zone(self, args):
        '''
        Creates a new zone.

        If a zone profile is not specified, the new zone will have the following
        default values:

        - primary: localhost.
        - mail: hostmaster@ZONENAME
        - refresh: server config value DNS_DEFAULT_REFRESH
        - retry: server config value DNS_DEFAULT_RETRY
        - expire: server config value DNS_DEFAULT_EXPIRE
        - minimum: server config value DNS_DEFAULT_MINIMUM
        - ttl: server config value DNS_DEFAULT_ZONE_TTL

        If a zone profile is specified, the default values and existing records are
        copied from ZONEPROFILE.

        The serial will default to the current date with the format YYYYMMDD01.
        '''
        options = OptionDict()
        options.set_attributes(args.attributes)
        options.set_if(soa_attributes=_get_soa_attributes(args),
                       from_profile=args.profilename,
                       owner=args.group)
        options['inherit_rights'] = not (args['no-inherit'] or args['no-inherit-rights'])
        options['inherit_zone_groups'] = not (args['no-inherit'] or args['no-inherit-zone-groups'])
        options['inherit_owner'] = not (args['no-inherit'] or args['no-inherit-owner'])
        result = self.client.zone_create(args.zonename, **options)
        _print_messages(result)

    @cmd.register('create zone-profile',
                  Argument('profilename'),
                  dns_field_value_group,
                  attributes_arg)
    def create_zoneprofile(self, args):
        '''
        Creates a zone profile. The default values are:

        - primary: localhost.
        - mail: hostmaster@ZONENAME
        - refresh: server config value DNS_DEFAULT_REFRESH
        - retry: server config value DNS_DEFAULT_RETRY
        - expire: server config value DNS_DEFAULT_EXPIRE
        - minimum: server config value DNS_DEFAULT_MINIMUM
        - ttl: server config value DNS_DEFAULT_ZONE_TTL
        - serial: current date with the format YYYYMMDD01
        '''
        options = OptionDict()
        options.set_attributes(args.attributes)
        options.set_if(soa_attributes=_get_soa_attributes(args))
        self.client.zone_create(args.profilename, profile=True, **options)

    @cmd.register('rename zone-profile',
                  Argument('oldname', completions=complete_zoneprofile),
                  Token('to'),
                  Argument('newname'))
    def rename_zoneprofile(self, args):
        '''
        Renames a zone profile.
        '''
        options = OptionDict(profile=True)
        self.client.zone_rename(args.oldname, args.newname, **options)

    @cmd.register('delete zone',
                  zone_arg,
                  Option('c', 'cleanup', help='also delete zone RRs and free IPs'))
    def delete_zone(self, args):
        '''
        Deletes ZONENAME.
        '''
        if args.cleanup and not args.dryrun:
            from . import zonedelete
            zonedelete.delete_zone(self.client, args.zonename, profile=False, print_messages=_print_messages)
        else:
            options = OptionDict(profile=False)
            options.set_if(cleanup=args.cleanup)
            result = self.client.zone_delete(args.zonename, **options)
            _print_messages(result)

    @cmd.register('delete zone-profile',
                  zoneprofile_arg)
    def delete_zoneprofile(self, args):
        '''
        Deletes ZONEPROFILE.
        '''
        if args.dryrun:
            self.client.zone_delete(args.profilename, profile=True, cleanup=True, dryrun=True)
        else:
            from . import zonedelete
            zonedelete.delete_zone(self.client, args.profilename, profile=True, print_messages=lambda _: None)

    def _set_zone_attrs(self, args, profile):
        options = OptionDict()
        options.set_if(profile=profile)
        if profile:
            zonename = args.profilename
        else:
            zonename = args.zonename
        if args.attributes and args.fields:
            raise Exception('SOA values and attributes cannot be set at the same time')
        if args.attributes:
            if args.get('view', None):
                raise Exception('Views cannot have attributes')
            else:
                self.client.zone_set_attrs(zonename, _parse_attributes(args.attributes), **options)
        if args.fields:
            options.set_if(view=args.get('view', None))
            self.client.zone_set_soa_attrs(zonename, _get_soa_attributes(args), **options)

    @cmd.register('modify zone set',
                  zone_single_view_group,
                  dns_field_value_group,
                  Group(Token('registrar-account'), registrar_account_arg, nargs='?'),
                  Group(Token('attrs'), attributes_arg, nargs='?'))
    def modify_zone_set(self, args):
        '''
        Sets zone attributes, or a zone view's SOA attributes or the zone's registrar-account.
        '''
        if args.registrar_account is not None:
            self.client.registrar_account_add_zone(args.registrar_account, args.zonename)
        else:
            self._set_zone_attrs(args, profile=False)

    @cmd.register('modify zone delete registrar-account', registrar_account_arg)
    def modify_zone_delete_registrar_account(self, args):
        _print_messages(self.client.registrar_account_delete_zone(args.registrar_account, args.zonename))

    @cmd.register('modify zone-profile set',
                  dns_field_value_group,
                  Group(Token('attrs'), attributes_arg, nargs='?'))
    def modify_zoneprofile_set(self, args):
        '''
        Sets zone profile attributes.
        '''
        self._set_zone_attrs(args, profile=True)

    @cmd.register('modify zone owning-user-group', group_arg)
    def modify_zone_owning_user_group(self, args):
        '''
        Sets the zone owning user group.
        '''
        self.client.zone_set_owner(args.zonename, args.group)

    @cmd.register('modify zone create view',
                  Argument('view'),
                  Group(Token('profile'), zoneprofile_arg, nargs='?'),
                  dns_field_value_group)
    def modify_zone_create_view(self, args):
        '''
        Create a view for zone ZONENAME.
        '''
        options = OptionDict()
        options.set_if(soa_attributes=_get_soa_attributes(args),
                       from_profile=args.profilename)
        result = self.client.zone_create_view(args.zonename, args.view, **options)
        if not args.profilename:
            self.warning('You created a view without specifing a profile, your view is totally empty.')
        _print_messages(result)

    @cmd.register('modify zone delete view',
                  Argument('view', completions=complete_view_from_zone),
                  Option('c', 'cleanup', help='also delete zone view RRs and free IPs'))
    def modify_zone_delete_view(self, args):
        '''
        Delete VIEW from zone ZONENAME.
        '''
        if args.cleanup and not args.dryrun:
            from . import zonedelete
            zonedelete.delete_zone_view(self.client, args.zonename, args.view, print_messages=_print_messages)
        else:
            options = OptionDict()
            options.set_if(cleanup=args.cleanup)
            _print_messages(self.client.zone_delete_view(args.zonename, args.view, **options))

    @cmd.register('modify zone rename view',
                  Argument('view', completions=complete_view_from_zone),
                  Token('to'),
                  Argument('newname'))
    def modify_zone_rename_view(self, args):
        '''
        Rename VIEW from zone ZONENAME to NEWNAME.
        '''
        self.client.zone_rename_view(args.zonename, args.view, args.newname)

    @cmd.register('modify zone dnssec enable',
                  Argument('algorithm', choices=['8']),
                  Token('ksk'),
                  Argument('ksk_bits'),
                  Token('zsk'),
                  Argument('zsk_bits'),
                  Group(Token('nsec3', dest='nsec3', action='store'),
                        Group(Argument('iterations', default=0),
                              Token('salted', dest='salted', action='store', nargs='?'),
                              nargs='?'),
                        nargs='?'))
    def modify_zone_dnssec_enable(self, args):
        '''
        Enable DNSSEC for zone ZONENAME. A KSK and a KSK are created and used to sign
        the zone. ALGORITHM, KSK_BITS and ZSK_BITS are recorded in zone
        properties and later used for key rollover.
        '''
        options = dict(algorithm=args.algorithm,
                       ksk_bits=args.ksk_bits,
                       zsk_bits=args.zsk_bits)
        if args.nsec3:
            options.update(nsec3_algorithm=1,
                           nsec3_iterations=int(args.iterations),
                           nsec3_salt=generate_salt() if args.salted else '-')
        result = self.client.zone_dnssec_enable(args.zonename, **options)
        _print_messages(result)

    @cmd.register('modify zone dnssec disable')
    def modify_zone_dnssec_disable(self, args):
        '''
        Disable DNSSEC for zone ZONENAME. All keys and NSEC3PARAM records are deleted.
        '''
        result = self.client.zone_dnssec_disable(args.zonename)
        _print_messages(result)

    @cmd.register('create registrar-account',
                  Argument('name'),
                  Token('plugin'),
                  Argument('plugin', choices=['autodns3']),
                  Token('url'),
                  Argument('url'),
                  Token('user'),
                  Argument('user'),
                  Token('password'),
                  Argument('password'),
                  Token('subaccount'),
                  Argument('subaccount'))
    def create_registrar_account(self, args):
        self.client.registrar_account_create(args.name, args.plugin, args.url, args.user, args.password,
                                             args.subaccount)

    @cmd.register('show registrar-account',
                  Argument('name', completions=complete_registrar_account),
                  script_option)
    def show_registrar_account_attrs(self, args):
        _print_attributes(self.client.registrar_account_get_attrs(args.name), script=args.script)

    @cmd.register('list registrar-accounts',
                  Option('t', 'status', help='include status information'),
                  script_option)
    def list_registrar_accounts(self, args):
        headers = ['plugin', {}, 'username', {'header': 'account'}]
        if args.status:
            headers.extend(['total_actions', {'align': 'r'}])
        _print_table(headers, self.client.registrar_account_list(include_actions=args.status), script=args.script)

    @cmd.register('list registrar-account',
                  registrar_account_arg,
                  Option('t', 'status', help='include status information'),
                  Option('v', 'verbose', help='detailed status information'),
                  script_option)
    def list_registrar_accounts(self, args):
        headers = ['zone', {}]
        if args.status:
            headers.extend(['last_run', {}, 'status', {}])
            if args.verbose:
                headers.extend(['error', {}])
        _print_table(headers,
                     _convert_table_times(
                         self.client.registrar_account_list_zones(args.registrar_account,
                                                                  include_status=args.status),
                         ('last_run',)),
                     script=args.script)

    @cmd.register('delete registrar-account',
                  Argument('name', completions=complete_registrar_account))
    def delete_registrar_account(self, args):
        _print_messages(self.client.registrar_account_delete(args.name))

    @cmd.register('modify registrar-account add', zone_arg)
    def modify_registrar_account_add(self, args):
        self.client.registrar_account_add_zone(args.registrar_account, args.zonename)

    @cmd.register('modify registrar-account delete', zone_arg)
    def modify_registrar_account_delete(self, args):
        _print_messages(self.client.registrar_account_delete_zone(args.registrar_account, args.zonename))

    @cmd.register('modify registrar-account run-all',
                  help='Update the DS records at the registrar for all the zones connected to REGISTRAR_ACCOUNT')
    def modify_registrar_account_run_all(self, args):
        self.client.registrar_account_update_zones(args.registrar_account)

    @cmd.register('list zone registrar-actions')
    def list_zone_registrar_actions(self, args):
        result = self.client.zone_registrar_actions(args.zonename)
        _print_table(['action', {}, 'data', {}, 'status', {}], result, script=args.script)

    @cmd.register('modify zone run-registrar-actions',
                  Option('f', 'force', help='update registrar keys'))
    def modify_zone_run_registrar_actions(self, args):
        self.client.registrar_account_update_zone(args.zonename)

    @cmd.register('modify zone dnssec delete key',
                  Argument('label', completions=complete_zone_keys))
    def modify_zone_dnssec_delete_key(self, args):
        result = self.client.zone_delete_key(args.zonename, args.label)
        _print_messages(result)

    @cmd.register('modify zone dnssec new ksk')
    def modify_zone_dnssec_new_ksk(self, args):
        result = self.client.zone_create_key(args.zonename, 'ksk')
        _print_messages(result)

    @cmd.register('modify zone dnssec new zsk')
    def modify_zone_dnssec_new_zsk(self, args):
        result = self.client.zone_create_key(args.zonename, 'zsk')
        _print_messages(result)

    for rr, properties in RR_FIELDS.items():
        params = [arg.name for arg in properties['arguments']]
        create_rr_options = properties['arguments'] + [rr_view_group, rr_comment_option]
        create_profile_rr_options = create_rr_options + [
            Option(None, 'overwrite', help='overwrite records with the same name and type')]
        create_profile_rr_description = 'Creates a ' + rr.upper() + ' resource record.'
        if rr in ('a', 'aaaa', 'ptr'):
            modify_zone_create_rr_options = list(create_rr_options) + [layer3domain_group]
            overwrite_a = Option(None, 'overwrite-a', help='overwrite A records with the same name')
            overwrite_ptr = Option(None, 'overwrite-ptr', help='overwrite PTR records with the same name')
            create_rr_options += [layer3domain_group, overwrite_a, overwrite_ptr, overlap_option]
            if rr == 'ptr':
                modify_zone_create_rr_options += [overwrite_ptr, overlap_option]
            else:
                modify_zone_create_rr_options += [overwrite_a, overlap_option]
            if rr == 'ptr':
                create_rr_options += [Option(None, 'only-reverse', help="don't create the A/AAAA record")]
            else:
                create_rr_options += [Option(None, 'only-forward', help="don't create the PTR record")]
        else:
            modify_zone_create_rr_options = create_rr_options = create_profile_rr_options
        create_rr_description = properties.get('create_rr_description', create_profile_rr_description)
        delete_arguments = properties.get('delete_arguments', properties['arguments'])
        delete_rr_options = [Group(*delete_arguments, **{'nargs': '?', 'stop_at': 'view'}), rr_view_group]
        show_rr_options = [Group(*delete_arguments, **{'nargs': '?', 'stop_at': 'view'}), rr_single_view_group]
        cmd.register('modify zone create rr ' + rr,
                     description=create_rr_description,
                     *modify_zone_create_rr_options)\
                    (_make_create_rr(rr_type=rr.upper(), params=params, profile=False, zonearg='zonename', create_linked=False))
        cmd.register('create rr ' + rr,
                     description=create_rr_description,
                     *create_rr_options)\
                    (_make_create_rr(rr_type=rr.upper(), params=params, profile=False, zonearg=None))
        cmd.register('show rr ' + rr,
                     description='Shows a ' + rr.upper() + ' resource record.',
                     *show_rr_options)\
                    (_make_show_rr(rr_type=rr.upper(), params=params))
        cmd.register('modify rr ' + rr,
                     description='Modifies a ' + rr.upper() + ' resource record.',
                     *show_rr_options)\
                    (_make_modify_rr(rr_type=rr.upper(), params=params))
        if rr not in ('ptr', 'a', 'aaaa'):
            cmd.register('modify zone-profile create rr ' + rr,
                         description='Adds a ' + rr.upper() + ' resource record to PROFILENAME.',
                         *create_profile_rr_options)\
                        (_make_create_rr(rr_type=rr.upper(), params=params, profile=True, zonearg='profilename'))
            cmd.register('modify zone-profile delete rr ' + rr,
                         description='Remove a ' + rr.upper() + ' resource record from PROFILENAME.',
                         *delete_rr_options)\
                        (_make_delete_rr(rr_type=rr.upper(), params=params, profile=True, zonearg='profilename'))

    delete_rr_options = [Group(Group(Argument('value', completions=complete_rr_value, stop_at='view'),
                                     Argument('values', action='append', nargs='*', stop_at='view'),
                                     nargs='?', stop_at='view'),
                               nargs='?', stop_at='view'),
                         rr_view_group]

    delete_rr_ip_options = [Group(Argument('value', completions=complete_rr_value),
                                  nargs='?', stop_at=['layer3domain', 'view']),
                            rr_view_group,
                            layer3domain_group]

    for rr, properties in RR_FIELDS.items():
        if rr in ('a', 'aaaa', 'ptr'):
            options = delete_rr_ip_options
        else:
            options = delete_rr_options

        def make_delete_rr(rr, zone_arg=None, **kwargs):
            def add_type(self, args):
                args['type'] = rr
                return _delete_rr(self, args, zone_arg, **kwargs)
            return add_type
        cmd.register('delete rr ' + rr, *options)(make_delete_rr(rr))
        cmd.register('modify zone delete rr ' + rr, *options)(make_delete_rr(rr, 'zonename', ignore_references=True))

    @cmd.register('delete rr any',
                  Group(Argument('type', completions=complete_rr_type),
                        Group(Argument('value', completions=complete_rr_value, stop_at='view'),
                              Argument('values', action='append', nargs='*', stop_at='view'),
                              nargs='?', stop_at='view'),
                        nargs='?', stop_at='view'),
                  rr_view_group)
    def delete_rr(self, args):
        return _delete_rr(self, args, None)

    @cmd.register('create rr from',
                  Argument('poolname', completions=complete_allocate_poolname),
                  zoneview_group,
                  rr_comment_option,
                  script_option,
                  help='allocate an ip from POOLNAME and create A/AAAA/PTR records')
    def create_rr_from(self, args):
        '''
        Allocate an ip from POOLNAME and create A/AAAA/PTR records.

        Instead of a pool name you can supply a vlan number (if there is only one pool
        with the specified vlan number).
        '''
        options = OptionDict()
        options.set_if(ttl=args.get('ttl', None),
                       comment=args.get('comment', None),
                       views=args.get('view', None))
        poolname = args.poolname
        if poolname.isdigit() and 2 <= int(poolname) <= 4096:
            vlan = int(poolname)
            pools = self.client.ippool_list(vlan=vlan, include_subnets=False)
            if len(pools) == 0:
                raise Exception('No pools with vlan %d exist' % vlan)
            elif len(pools) > 1:
                raise Exception('Multiple pools with vlan %d exist: %s' %
                                (vlan, ' '.join(p['name'] for p in pools)))
            else:
                poolname = pools[0]['name']
        result = self.client.rr_create_from_pool(args.name, poolname, **options)
        _print_messages(result)
        self._print_get_ip_data(result['attributes'], args)

    @cmd.register('modify zone delete rr any',
                  Group(Argument('type', completions=complete_rr_type),
                        Group(Argument('value', completions=complete_rr_value, stop_at='view'),
                              Argument('values', action='append', nargs='*', stop_at='view'),
                              nargs='?', stop_at='view'),
                        nargs='?', stop_at='view'),
                  rr_view_group,
                  layer3domain_group)
    def modify_zone_delete_rr(self, args):
        return _delete_rr(self, args, 'zonename', ignore_references=True)

    @cmd.register('show rr any',
                  rr_single_view_group,
                  help='show any records named NAME')
    def show_rr_any(self, args):
        '''
        Show a resource record. If multiple records with the name NAME exist, an error is returned.
        '''
        _make_show_rr(rr_type=None, params=())(self, args)

    @cmd.register('modify rr any',
                  help='modify any records named NAME')
    def modify_rr_any(self, args):
        '''
        Modify a resource record. If multiple records with the name NAME exist, an error is returned.
        '''
        _make_modify_rr(rr_type=None, params=())(self, args)

    @cmd.register('show zone attrs',
                  script_option,
                  help='show zone attributes')
    def show_zone_attrs(self, args):
        '''
        Shows zone attributes.
        '''
        _print_attributes(self.client.zone_get_attrs(args.zonename, profile=False),
                          script=args.script)

    @cmd.register('show zone view',
                  Argument('view', completions=complete_view_from_zone),
                  script_option,
                  help='show zone view attributes')
    def show_zone_view(self, args):
        '''
        Shows zone view attributes.
        '''
        _print_attributes(self.client.zone_view_get_attrs(args.zonename, args.view),
                          script=args.script)

    @cmd.register('show zone-profile',
                  zoneprofile_arg,
                  script_option,
                  help='show zone profile attributes')
    def show_zone_profile(self, args):
        '''
        Shows zone profile attributes.
        '''
        _print_attributes(self.client.zone_get_attrs(args.profilename, profile=True),
                          script=args.script)

    def _list_zones(self, args, pattern, profile, fields):
        options = OptionDict(profile=profile, fields=fields)
        options.set_if(pattern=pattern,
                       limit=int(args.limit) + 1)
        table_info = ['name', {}]
        if fields:
            table_info += ['views', {}, 'zone_groups', {}]
        zones = self.client.zone_list(**options)
        more = False
        if len(zones) > int(args.limit):
            more = True
            zones.pop()
        _print_table(table_info,
                     zones,
                     script=args.script)
        if more:
            logger.warn("More results available")

    @cmd.register('list zones',
                  Argument('wildcard', nargs='?'),
                  script_option,
                  Option('L', 'limit',
                         help='limit the number of results (default: 10)',
                         action='store',
                         default=10))
    def list_zones(self, args):
        '''
        Displays the list of zones (matching WILDCARD).
        '''
        if args.wildcard:
            logger.info("Result for list zones %s" % args.wildcard)
        self._list_zones(args, pattern=args.wildcard, profile=False, fields=True)

    @cmd.register('list zone-profiles',
                  Argument('wildcard', nargs='?'),
                  script_option,
                  Option('L', 'limit',
                         help='limit the number of results (default: 10)',
                         default=10,
                         action='store'))
    def list_zoneprofiles(self, args):
        '''
        Displays the list of zone names (matching WILDCARD).
        '''
        if args.wildcard:
            logger.info("Result for list zone-profiles %s" % args.wildcard)
        self._list_zones(args, pattern=args.wildcard, profile=True, fields=False)

    @cmd.register('list rrs',
                  Argument('wildcard'),
                  rr_type_arg,
                  layer3domain_group,
                  script_option)
    def list_rrs(self, args):
        '''
        Displays resource records matching WILDCARD.
        '''
        options = OptionDict(pattern=args.wildcard)
        # do not use get_layer3domain() as CNAMEs etc. do not have a layer3domain
        options.set_if(type=args.type,
                       layer3domain=args.get('layer3domain'))
        logger.info("Result for list rrs %s" % args.wildcard)
        rrs = self.client.rr_list(**options)
        _print_table(['record', {},
                      'zone', {},
                      'view', {},
                      'ttl', {},
                      'type', {},
                      'value', {}] + _layer3domain_column(rrs),
                     rrs,
                     script=args.script)

    @cmd.register('dump zone',
                  zone_arg,
                  Group(Token('view'), Argument('view', completions=complete_view_from_zone), nargs='?'))
    def dump_zone(self, args):
        '''
        Displays the zone records in BIND zonefile format.
        '''
        options = OptionDict()
        options.set_if(view=args.view)
        print(self.client.zone_dump(args.zonename, **options))

    @cmd.register('list zone keys')
    def list_zone_keys(self, args):
        '''
        Displays the zone DNSSEC keys.
        '''
        _print_table(['label', {},
                      'type', {},
                      'tag', {},
                      'algorithm', {},
                      'bits', {},
                      'created', {}],
                     self.client.zone_list_keys(args.zonename),
                     script=args.script)

    @cmd.register('list zone dnskeys',
                  Option('r', 'rr', help='DNS RR format'))
    def list_zone_dnskeys(self, args):
        '''
        Displays the zone DNSSEC keys.
        '''
        if args.rr:
            for key in self.client.zone_list_keys(args.zonename):
                key['zone'] = args.zonename
                print('%(zone)s.\tIN\tDNSKEY %(flags)s 3 %(algorithm)s %(pubkey)s' % key)
        else:
            _print_table(['tag', {},
                          'flags', {},
                          'algorithm', {},
                          'pubkey', {}],
                         self.client.zone_list_keys(args.zonename),
                         script=args.script)

    @cmd.register('list zone delegationsigners')
    @cmd.register('list zone ds')
    def list_zone_ds(self, args):
        '''
        Displays the zone DS keys.
        '''
        _print_table(['tag', {},
                      'algorithm', {},
                      'digest_type', {},
                      'digest', {}],
                     self.client.zone_list_delegation_signers(args.zonename),
                     script=args.script)

    @cmd.register('list zone records',
                  rr_type_arg,
                  layer3domain_group)
    def list_zone(self, args):
        '''
        Displays the zone records.
        '''
        options = OptionDict(zone=args.zonename)
        # do not use get_layer3domain() as CNAMEs etc. do not have a layer3domain
        options.set_if(view=args.view,
                       type=args.type,
                       layer3domain=args.get('layer3domain'))
        rrs = self.client.rr_list(**options)
        _print_table(['record', {'align': 'r' if is_reverse_zone(args.zonename) else 'l'},
                      'zone', {},
                      'ttl', {},
                      'type', {},
                      'value', {}] + _layer3domain_column(rrs),
                     rrs,
                     script=args.script)

    @cmd.register('list zone-profile',
                  zoneprofile_arg,
                  script_option)
    def list_zoneprofile(self, args):
        '''
        Displays the zone profile records.
        '''
        options = OptionDict(profile=True)
        _print_table(['record', {},
                      'ttl', {},
                      'type', {},
                      'value', {}],
                     self.client.rr_list(zone=args.profilename, **options),
                     script=args.script)

    @cmd.register('list zone zone-groups')
    def list_zone_zone_groups(self, args):
        '''
        Displays the zone groups that contain views from this zone.
        '''
        options = OptionDict()
        options.set_if(view=args.view)
        _print_table(['zone_group', {'header': 'zone-group'},
                      'view', {}],
                     sorted(self.client.zone_list_zone_groups(args.zonename, **options),
                            key=itemgetter('zone_group')),
                     script=args.script)

    @cmd.register('list zone views')
    def list_zone_views(self, args):
        '''
        Displays the zone views.
        '''
        _print_table(['name', {}],
                     self.client.zone_list_views(args.zonename),
                     script=args.script)

    @cmd.register('list zone rights')
    def list_zone_rights(self, args):
        '''
        Displays the zone rights.
        '''
        options = OptionDict()
        options.set_if(view=args.view)
        _print_table(['action', {},
                      'object', {},
                      'group', {}],
                     self.client.zone_get_access(args.zonename, **options),
                     script=args.script)

    @cmd.register('import zone',
                  zone_arg,
                  Group(Token('view'), Argument('view'), nargs='?'))
    def import_zone(self, args):
        '''
        Imports BIND zonefile data from stdin.
        '''
        content = sys.stdin.read()
        zoneimport.import_zone(self.client, content, zone_name=args.zonename, view=args.view)

    @cmd.register('import rev-zone')
    def import_revzone(self, args):
        '''
        Imports BIND reverse zonefile data from stdin.
        '''
        content = sys.stdin.read()
        zoneimport.import_zone(self.client, content, zone_name=None, revzone=True)

    @cmd.register('create zone-group',
                  zone_group_arg,
                  Group(Token('comment'), Argument('comment'), nargs='?'))
    def create_zone_group(self, args):
        '''
        Creates zone-group named GROUP.
        '''
        options = OptionDict()
        options.set_if(comment=args.comment)
        self.client.zone_group_create(args.zonegroup, **options)

    @cmd.register('modify zone-group add zone',
                  zone_arg,
                  zone_single_view_group)
    def modify_zone_group_add_zone(self, args):
        '''
        Adds to zone-group GROUP either:
        - ZONE's only view
        - VIEW of ZONE
        '''
        options = OptionDict()
        options.set_if(view=args.view)
        self.client.zone_group_add_zone(args.zonegroup, args.zonename, **options)

    @cmd.register('show zone-group',
                  zone_group_arg,
                  script_option,
                  help='show ZONE-GROUP')
    def show_zone_group(self, args):
        _print_attributes(self.client.zone_group_get_attrs(args.zonegroup), script=args.script)

    @cmd.register('modify zone-group set comment',
                  Argument('comment'),
                  help="set GROUP's comment")
    def modify_zone_group_set_comment(self, args):
        self.client.zone_group_set_comment(args.zonegroup, args.comment)

    @cmd.register('modify zone-group remove',
                  Token('zone'),
                  zone_arg,
                  help="Remove ZONE from GROUP")
    def modify_zone_group_remove_zone(self, args):
        self.client.zone_group_remove_zone(args.zonegroup, args.zonename)

    @cmd.register('list zone-group views',
                  help='list zone views of GROUP')
    def list_zone_group(self, args):
        views = [{'zone': v['zone'],
                  'view': v['view']}
                 for v in self.client.zone_group_get_views(args.zonegroup)]
        _print_table(['zone', {},
                      'view', {}],
                     sorted(views, key=itemgetter('zone')),
                     script=args.script)

    @cmd.register('list zone-group outputs',
                  help='list zone-group outputs')
    def list_zone_group_outputs(self, args):
        _print_table(['name', {},
                      'plugin', {},
                      'comment', {}],
                     self.client.zone_group_list_outputs(args.zonegroup),
                     script=args.script)

    @cmd.register('delete zone-group',
                  zone_group_arg)
    def delete_zone_group(self, args):
        '''
        Deletes zone-group named GROUP.
        '''
        self.client.zone_group_delete(args.zonegroup)

    @cmd.register('rename zone-group',
                  Argument('oldname'),
                  Token('to'),
                  Argument('newname'))
    def rename_zone_group(self, args):
        '''
        Renames a zone-group.
        '''
        self.client.zone_group_rename(args.oldname, args.newname)

    @cmd.register('list zone-groups',
                  script_option,
                  help='list zone-groups')
    def list_zone_groups(self, args):
        _print_table(['name', {},
                      'comment', {}],
                     sorted(self.client.zone_group_list(), key=itemgetter('name')),
                     script=args.script)

    @cmd.register('list outputs',
                  Option('t', 'status', help='include status information'),
                  script_option,
                  help='list outputs')
    def list_outputs(self, args):
        headers = ['name', {},
                   'plugin', {}]
        if args.status:
            headers.extend(['pending_records', {'align': 'r'},
                            'last_run', {},
                            'status', {}])
        _print_table(headers,
                     _convert_table_times(self.client.output_list(include_status=args.status),
                                          ('last_run', )),
                     script=args.script)

    def register_create_output(type, cmdline_options):
        def create_output(self, args):
            options = OptionDict()
            options.set_if(comment=args.comment)
            options.set_if(db_uri=args.db_uri)
            self.client.output_create(args.name, type, **options)
        create_output.__doc__ = 'Create a %s output' % type
        cmd.register('create output ' + type, *cmdline_options)(create_output)

    register_create_output('bind', [Group(Token('db-uri'), Argument('db_uri'), nargs='?'),
                                    Group(Token('comment'), Argument('comment'), nargs='?')])
    register_create_output('pdns-db', [Group(Token('db-uri'), Argument('db_uri')),
                                       Group(Token('comment'), Argument('comment'), nargs='?')])

    @cmd.register('modify output set',
                  Token('comment'),
                  Argument('comment'))
    def modify_output_set_comment(self, args):
        '''
        Set the comment for OUTPUT.
        '''
        self.client.output_set_comment(args.output, args.comment)

    @cmd.register('modify output set',
                  Argument('attr'),
                  Argument('value'))
    def modify_output_set_attr(self, args):
        '''
        Set the attribute ATTR to VALUE for OUTPUT.
        '''
        attrs = {}
        attrs[args.attr] = args.value
        self.client.output_set_attrs(args.output, attrs)

    @cmd.register('modify output add',
                  Token('zone-group'),
                  zone_group_arg)
    def modify_output_add(self, args):
        '''
        Add ZONE-GROUP to OUTPUT.
        '''
        self.client.output_add_group(args.output, args.zonegroup)

    @cmd.register('modify output remove',
                  Token('zone-group'),
                  zone_group_arg)
    def modify_output_remove(self, args):
        '''
        Remove ZONE-GROUP from OUTPUT.
        '''
        self.client.output_remove_group(args.output, args.zonegroup)

    @cmd.register('show output',
                  output_arg,
                  script_option,
                  help='show OUTPUT')
    def show_output(self, args):
        _print_attributes(self.client.output_get_attrs(args.output), script=args.script)

    @cmd.register('list output',
                  output_arg,
                  script_option,
                  help='list zone-groups of OUTPUT')
    def list_output(self, args):
        _print_table(['zone_group', {'header': 'zone-group'},
                      'comment', {}],
                     sorted(self.client.output_get_groups(args.output), key=itemgetter('zone_group')),
                     script=args.script)

    @cmd.register('rename output',
                  Argument('oldname'),
                  Token('to'),
                  Argument('newname'))
    def rename_output(self, args):
        '''
        Renames an output.
        '''
        self.client.output_rename(args.oldname, args.newname)

    @cmd.register('delete output',
                  output_arg)
    def delete_output(self, args):
        '''
        Deletes OUTPUT.
        '''
        self.client.output_delete(args.output)


def register_history(htype, cmd_args, arg_meta: str, f, fargs, layer3domain_option: bool = False):
    def _get_history(self, args):
        func = getattr(self.client, f)
        fargs_l = fargs(args)
        options = OptionDict(limit=int(args.limit),
                             begin=_local2utc(args.get('begin')),
                             end=_local2utc(args.get('end')))
        # add layer3domain option only if the history function supports it
        if layer3domain_option:
            options['layer3domain'] = get_layer3domain(args.layer3domain)
        columns = ['timestamp', {},
                   'user', {},
                   'tool', {},
                   'originating_ip', {},
                   'objclass', {},
                   'name', {},
                   'action', {}]
        _print_table(columns,
                     _convert_table_times(func(*fargs_l, **options), ('timestamp', )),
                     script=args.script)
    cmd.register('history ' + htype, *cmd_args, **dict(description='Displays %s history' % arg_meta))(_get_history)


register_history('any', arg_meta='', cmd_args=(),
                 f='history', fargs=lambda args: [None])
register_history('user', arg_meta='USER', cmd_args=(Argument('user'), ),
                 f='history', fargs=lambda args: [args.user])
register_history('zones', arg_meta='zones', cmd_args=(),
                 f='history_zones', fargs=lambda args: [False])
register_history('zone-profiles', arg_meta='zone-profiles', cmd_args=(),
                 f='history_zones', fargs=lambda args: [True])
register_history('zone any', arg_meta='ZONE', cmd_args=(),
                 f='history_zone', fargs=lambda args: [args.zonename, False])
register_history('zone views', arg_meta='ZONE\'s views', cmd_args=(),
                 f='history_zone_views', fargs=lambda args: [args.zonename])
register_history('zone view', arg_meta='ZONE VIEW', cmd_args=(Argument('view', action='store', completions=complete_view_from_zone), ),
                 f='history_zone_view', fargs=lambda args: [args.zonename, args.view])
register_history('zone-profile', arg_meta='ZONEPROFILE', cmd_args=(zoneprofile_arg, ),
                 f='history_zone', fargs=lambda args: [args.profilename, True])
register_history('rr', arg_meta='resource records named NAME', cmd_args=(Argument('name'), ),
                 f='history_rr', fargs=lambda args: [args.name])
register_history('rrs', arg_meta='resource records', cmd_args=(),
                 f='history_rr', fargs=lambda args: [None])
register_history('zone-group', arg_meta='ZONEGROUP', cmd_args=(zone_group_arg, ),
                 f='history_zone_group', fargs=lambda args: [args.zonegroup])
register_history('zone-groups', arg_meta='zone-groups', cmd_args=(),
                 f='history_zone_group', fargs=lambda args: [None])
register_history('output', arg_meta='OUTPUT', cmd_args=(output_arg, ),
                 f='history_output', fargs=lambda args: [args.output])
register_history('outputs', arg_meta='outputs', cmd_args=(),
                 f='history_output', fargs=lambda args: [None])
register_history('user-group', arg_meta='USERGROUP', cmd_args=(group_arg, ),
                 f='history_group', fargs=lambda args: [args.group])
register_history('user-groups', arg_meta='groups', cmd_args=(),
                 f='history_group', fargs=lambda args: [None])
register_history('pool', arg_meta='POOL', cmd_args=(Argument('poolname', completions=complete_allocate_poolname), ),
                 f='history_ippool', fargs=lambda args: [args.poolname])
register_history('ipblock', arg_meta='IPBLOCK', cmd_args=(Argument('ipblock'), layer3domain_group),
                 f='history_ipblock', fargs=lambda args: [args.ipblock], layer3domain_option=True)
register_history('registrar-account', arg_meta='REGISTRAR_ACCOUNT', cmd_args=(registrar_account_arg, ),
                 f='history_registrar_account', fargs=lambda args: [args.registrar_account])
register_history('layer3domain', arg_meta='LAYER3DOMAIN', cmd_args=(layer3domain_arg, ),
                 f='history_layer3domain', fargs=lambda args: [get_layer3domain(args.layer3domain)])


def main():
    sys.exit(CLI().run(sys.argv))
