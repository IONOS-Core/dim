import os.path
import re
import sys
from glob import glob
from subprocess import Popen, PIPE, call, check_call


def this_dir(path):
    return os.path.join(os.path.dirname(os.path.realpath(__file__)), path)


def exists(prog):
    p = Popen(['which', prog], stdout=PIPE)
    p.communicate()
    return p.returncode == 0


def diff_files(a, b):
    prog = ['diff', '-uw']
    if sys.stdout.isatty() and exists('colordiff'):
        prog = ['colordiff', '-uw']
    if os.getenv('DIFF_TOOL') is not None:
        prog = [os.getenv('DIFF_TOOL')]
    call(prog + [a, b])


def onetab(s): return re.sub('\t+', '\t', s)


def compact(zone):
    return sorted(set(onetab(l) for l in zone.splitlines() if not re.match(r'^\s*(;.*)?\s*?$', l)))


def zones_equal(a, b):
    '''Ignores differences in whitespace even inside strings'''
    ac = compact(a)
    bc = compact(b)
    if len(ac) != len(bc) or any(re.sub(r'\s+', '', l1) != re.sub(r'\s+', '', l2)
                                 for l1, l2 in zip(ac, bc)):
        pdns_file = '/tmp/pdns.zone'
        dim_file = '/tmp/dim.zone'
        with open(pdns_file, 'w') as f:
            f.write('\n'.join(compact(a)) + '\n')
        with open(dim_file, 'w') as f:
            f.write('\n'.join(compact(b)) + '\n')
        diff_files(pdns_file, dim_file)
        return False
    return True


PDNS_OUTPUT_JAR = None


def pdns_output_jar():
    def some(l, p):
        for e in l:
            if p(e):
                return e
        return None

    global PDNS_OUTPUT_JAR
    if PDNS_OUTPUT_JAR is None:
        PDNS_OUTPUT_JAR = some(glob(this_dir('../pdns-output/build/libs/pdns-output-*.jar')) +
                               ['/opt/dim/pdns-output.jar'],
                               os.path.exists)
        if PDNS_OUTPUT_JAR is None:
            raise Exception('pdns-output.jar not found')
    return PDNS_OUTPUT_JAR


def test_pdns_output_process(log):
    jvm_options = ['-Dlog4j.configurationFile=' + this_dir('log4j2.properties')] if not log else []
    cmd = ['java'] + jvm_options + ['-jar', pdns_output_jar(),
                                    '--config', this_dir('pdns-output-test.properties')]
    return Popen(cmd, stdout=PIPE, close_fds=True)


def verify_zone(zone):
    '''Verify if zone is correctly signed'''
    pdns_file = '/tmp/signed.zone'
    with open(pdns_file, 'w') as f:
        f.write('\n'.join(compact(zone)) + '\n')
    check_call(['java', '-cp', pdns_output_jar(), 'com.verisignlabs.dnssec.cl.VerifyZone', pdns_file])


def is_generated(l):
    for r in ['RRSIG', 'NSEC', 'NSEC3', 'NSEC3PARAM', 'DNSKEY']:
        if r in l:
            return True
    return False


def setup_pdns_output(dim):
    '''Add all zones to zone group pdns_group and return a zone->view mapping'''
    zone_list = dim.zone_list(fields=True)
    zones = [z['name'] for z in zone_list]
    zone_views = dict((z['name'], z['views']) for z in zone_list)
    pdns_group = dict((v['zone'], v['view']) for v in dim.zone_group_get_views('pdns_group'))
    for zone in set(zones) - set(pdns_group.keys()):
        if zone_views[zone] > 1:
            view = dim.zone_list_views(zone)[0]['name']
        else:
            view = None
        dim.zone_group_add_zone('pdns_group', zone, view=view)
        pdns_group[zone] = view
    return pdns_group


def compare_dim_pdns_zones(dim, pdns_ip, zone_view):
    '''
    Compare dim dump zone with pdns axfr

    zone_view is a map zone_name -> view_name
    '''
    for zone, view in zone_view.items():
        pdns = Popen(['dig', 'axfr', zone, '@' + pdns_ip], stdout=PIPE).communicate()[0]
        dump = dim.zone_dump(zone, view=view)
        if 'RRSIG' in pdns:
            verify_zone(pdns)
            # Strip DNSSEC records
            pdns = '\n'.join([l for l in pdns.splitlines() if not is_generated(l)])
        if not zones_equal(pdns, dump):
            return False
    return True
