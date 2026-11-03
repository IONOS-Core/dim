'''
DNSSEC tests, in three layers:

* DNSKEYTest, NSEC3ParamsTest: the dnssec primitives in dim.models.dns, no
  database needed.
* ZoneDnssecTest: key management through the rpc layer, needs the dim database.
* PdnsOutputDnssecTest: end to end. pdns-output signs a zone into a pdns
  database, the records are checked and the axfr from the pdns instance is
  verified with jdnssec-verifyzone. Skipped unless pdns-output.jar, the pdns
  database and a pdns instance are available - see dim-testsuite/compose.yaml
  for an environment that provides all three.
'''
import base64
import datetime
import hashlib
import os
import select
import shutil
import socket
import struct
import time
import unittest
from contextlib import contextmanager
from subprocess import PIPE, Popen

import pytest
from sqlalchemy import create_engine

from dim import db
from dim.errors import DimError, InvalidParameterError
from dim.models.dns import OutputUpdate, Zone, dnskey_rdata, dnskey_tag, ds_hash

from tests.pdns_test import PDNSTest
# imported as a module: pytest would collect test_pdns_output_process as a test
from tests import pdns_util
from tests.util import RPCTest, raises

pubkey = 'AwEAAbJKLSzK2R+DpWt9Vudk02db2actR3NsS6TZhLBrs4ud5S4U9e/3Yz90YrQwcJC83seoAH942nTMOt+NlKfXWRjqVZUP+tkxWqFvwKU82UGg/TWmbqH4BQY/ZM6ybdo2Z2fkpWwpTpf6/37vnS7/Rq7TI/sFA7R81PHAvr1dN5AQYpYNB/RhRaQ0q+0k/kBVuReeffNVN36uO9ECpBDjiE1j3uMbEzwxI/ht/OyXJ3w7S8iE06qwS/44KzEYmkxYIUwhjyvWAMbfEzw4rE7fEHgvnQKoHXe4azamtLbyDTn/NZDwzBjU7ewZIUpANV/fUm7t5ksvAmXi2JsE3YZHshs=' # noqa
algorithm = 8
flags = 257
protocol = 3

DIGEST_HASH = {1: hashlib.sha1, 2: hashlib.sha256, 4: hashlib.sha384}


class DNSKEYTest(unittest.TestCase):
    def test_dnskey_tag(self):
        assert dnskey_tag(dnskey_rdata(flags, protocol, algorithm, pubkey)) == 20842

    def test_ds_hash(self):
        assert ds_hash('a.com'.encode('utf-8'), dnskey_rdata(flags, protocol, algorithm, pubkey), hashlib.sha1) == \
            '91053B9A59B05FB08D5469472A5F1B588C5CA092'
        assert ds_hash('a.com'.encode('utf-8'), dnskey_rdata(flags, protocol, algorithm, pubkey), hashlib.sha256) == \
            'FFB15B5EF961E0AE3474E7B868FBD3C8F7C861D3BEA4527382CBDA791D4B9FF4'

    def test_dnskey_rdata_layout(self):
        rdata = dnskey_rdata(flags, protocol, algorithm, pubkey)
        assert struct.unpack('!HBB', rdata[:4]) == (flags, protocol, algorithm)
        assert rdata[4:] == base64.b64decode(pubkey)

    def test_zsk_flags_differ_from_ksk(self):
        ksk = dnskey_rdata(257, protocol, algorithm, pubkey)
        zsk = dnskey_rdata(256, protocol, algorithm, pubkey)
        assert ksk != zsk
        assert dnskey_tag(ksk) != dnskey_tag(zsk)

    def test_ds_hash_digest_lengths(self):
        rdata = dnskey_rdata(flags, protocol, algorithm, pubkey)
        for digest_type, length in ((1, 40), (2, 64), (4, 96)):
            digest = ds_hash(b'a.com', rdata, DIGEST_HASH[digest_type])
            assert len(digest) == length
            assert set(digest) <= set('0123456789ABCDEF')

    def test_ds_hash_covers_owner_name(self):
        rdata = dnskey_rdata(flags, protocol, algorithm, pubkey)
        assert ds_hash(b'a.com', rdata, hashlib.sha256) != ds_hash(b'b.com', rdata, hashlib.sha256)


class NSEC3ParamsTest(unittest.TestCase):
    def test_valid_params(self):
        Zone.check_nsec3params(0, 0, '-')
        Zone.check_nsec3params(1, 0, '-')
        Zone.check_nsec3params(1, 65535, 'abcdef')
        Zone.check_nsec3params(1, 10, 'A' * 510)

    def test_invalid_algorithm(self):
        for nsec3_algorithm in (-1, 2, 8):
            with raises(InvalidParameterError):
                Zone.check_nsec3params(nsec3_algorithm, 0, '-')

    def test_invalid_iterations(self):
        for iterations in (-1, 65536):
            with raises(InvalidParameterError):
                Zone.check_nsec3params(1, iterations, '-')

    def test_invalid_salt(self):
        for salt in ('', 'xyz', 'A' * 511):
            with raises(InvalidParameterError):
                Zone.check_nsec3params(1, 0, salt)


class ZoneDnssecTest(RPCTest):
    '''Key management. Uses small keys, the crypto is not what is tested here.'''

    ZONE = 'test.com'
    KSK_BITS = 1024
    ZSK_BITS = 1024

    def setUp(self):
        RPCTest.setUp(self)
        self.r.zone_create(self.ZONE)

    def enable(self, zone=None, **kwargs):
        kwargs.setdefault('ksk_bits', self.KSK_BITS)
        kwargs.setdefault('zsk_bits', self.ZSK_BITS)
        return self.r.zone_dnssec_enable(zone or self.ZONE, **kwargs)

    def keys(self, zone=None):
        return dict((key['type'], key) for key in self.r.zone_list_keys(zone or self.ZONE))

    def zone(self, zone=None):
        return Zone.query.filter_by(name=zone or self.ZONE).one()

    def test_enable_creates_a_ksk_and_a_zsk(self):
        self.enable()
        keys = self.keys()
        assert sorted(keys.keys()) == ['KSK', 'ZSK']
        assert keys['KSK']['flags'] == 257
        assert keys['ZSK']['flags'] == 256
        assert keys['KSK']['bits'] == self.KSK_BITS
        assert keys['ZSK']['bits'] == self.ZSK_BITS
        for key in keys.values():
            assert key['algorithm'] == 8
            assert key['label'].startswith(self.ZONE + '_' + key['type'].lower())
            # the tag the server reports must match the one derived from the key
            assert key['tag'] == dnskey_tag(dnskey_rdata(key['flags'], protocol, key['algorithm'], key['pubkey']))

    def test_enable_stores_defaults_as_zone_attributes(self):
        self.enable()
        attrs = self.r.zone_get_attrs(self.ZONE)
        assert str(attrs['default_algorithm']) == '8'
        assert str(attrs['default_ksk_bits']) == str(self.KSK_BITS)
        assert str(attrs['default_zsk_bits']) == str(self.ZSK_BITS)

    def test_enable_sets_the_validity_window(self):
        self.enable()
        now = datetime.datetime.utcnow()
        zone = self.zone()
        assert zone.valid_begin is not None and zone.valid_end is not None
        assert zone.valid_end > zone.valid_begin
        # valid_begin must be backdated by 1 hour (RFC 6781 skew)
        assert now - datetime.timedelta(seconds=3660) <= zone.valid_begin <= now - datetime.timedelta(seconds=3540)
        assert now + datetime.timedelta(days=13, hours=23) <= zone.valid_end <= now + datetime.timedelta(days=14, hours=1)

    def test_enable_without_nsec3(self):
        self.enable()
        # left unset (NULL), which pdns-output treats as NSEC
        assert not self.zone().nsec3_algorithm
        assert 'nsec3_algorithm' not in self.r.zone_get_attrs(self.ZONE)

    def test_enable_with_nsec3(self):
        self.enable(nsec3_algorithm=1, nsec3_iterations=10, nsec3_salt='AB')
        attrs = self.r.zone_get_attrs(self.ZONE)
        assert attrs['nsec3_algorithm'] == 1
        assert attrs['nsec3_iterations'] == 10
        assert attrs['nsec3_salt'] == 'AB'
        # algorithm, opt-out flag, iterations, salt
        assert self.zone().nsec3param == '1 0 10 AB'

    def test_enable_rejects_other_algorithms(self):
        for bad_algorithm in (5, 13, 15):
            with raises(InvalidParameterError):
                self.enable(algorithm=bad_algorithm)

    def test_enable_rejects_invalid_nsec3_params(self):
        with raises(InvalidParameterError):
            self.enable(nsec3_algorithm=1, nsec3_iterations=66000)
        with raises(InvalidParameterError):
            self.enable(nsec3_algorithm=2)
        assert self.r.zone_list_keys(self.ZONE) == []

    def test_disable_removes_keys_and_nsec3_params(self):
        self.enable(nsec3_algorithm=1, nsec3_iterations=10)
        self.r.zone_dnssec_disable(self.ZONE)
        assert self.r.zone_list_keys(self.ZONE) == []
        assert self.r.zone_list_delegation_signers(self.ZONE) == []
        zone = self.zone()
        assert zone.nsec3_algorithm == 0
        assert zone.nsec3param == ''
        assert zone.valid_begin is None and zone.valid_end is None
        assert not zone.is_signed()

    def test_create_and_delete_key(self):
        self.enable()
        self.r.zone_create_key(self.ZONE, 'zsk')
        zsks = [k for k in self.r.zone_list_keys(self.ZONE) if k['type'] == 'ZSK']
        assert len(zsks) == 2
        self.r.zone_delete_key(self.ZONE, zsks[0]['label'])
        labels = [k['label'] for k in self.r.zone_list_keys(self.ZONE)]
        assert zsks[0]['label'] not in labels
        assert zsks[1]['label'] in labels

    def test_delete_unknown_key(self):
        self.enable()
        with raises(DimError):
            self.r.zone_delete_key(self.ZONE, 'no-such-key')

    def test_create_key_invalid_type(self):
        self.enable()
        with raises(InvalidParameterError):
            self.r.zone_create_key(self.ZONE, 'csk')

    def test_create_key_without_dnssec_enabled(self):
        with raises(InvalidParameterError):
            self.r.zone_create_key(self.ZONE, 'ksk')

    def test_delegation_signers_for_every_digest_type(self):
        self.enable()
        ksk = self.keys()['KSK']
        rdata = dnskey_rdata(ksk['flags'], protocol, ksk['algorithm'], ksk['pubkey'])
        ds = self.r.zone_list_delegation_signers(self.ZONE)
        assert sorted(d['digest_type'] for d in ds) == [1, 2, 4]
        for signer in ds:
            assert signer['tag'] == ksk['tag']
            assert signer['algorithm'] == ksk['algorithm']
            assert signer['digest'] == ds_hash(self.ZONE.encode('utf-8'), rdata,
                                               DIGEST_HASH[signer['digest_type']])

    def test_no_delegation_signers_for_zsk_only(self):
        self.enable()
        ksk_label = self.keys()['KSK']['label']
        self.r.zone_delete_key(self.ZONE, ksk_label)
        assert self.r.zone_list_delegation_signers(self.ZONE) == []

    def test_ds_rr_created_in_signed_parent(self):
        self.enable()
        subzone = 'sub.' + self.ZONE
        self.r.zone_create(subzone)
        self.enable(subzone)
        ds_rrs = self.r.rr_list(zone=self.ZONE, type='DS')
        assert len(ds_rrs) == 1
        assert ds_rrs[0]['record'] == 'sub'
        assert str(self.keys(subzone)['KSK']['tag']) in ds_rrs[0]['value']

    def test_ds_rr_removed_when_subzone_is_unsigned(self):
        self.enable()
        subzone = 'sub.' + self.ZONE
        self.r.zone_create(subzone)
        self.enable(subzone)
        self.r.zone_dnssec_disable(subzone)
        assert self.r.rr_list(zone=self.ZONE, type='DS') == []

    def test_unsigned_parent_gets_no_ds_rr(self):
        subzone = 'sub.' + self.ZONE
        self.r.zone_create(subzone)
        self.enable(subzone)
        assert self.r.rr_list(zone=self.ZONE, type='DS') == []


PDNS_DB_URI = os.getenv('PDNS1_DB_URI', 'mysql://pdns:pdns@127.0.0.1:3307/pdns1')
PDNS_ADDRESS = os.getenv('PDNS1_ADDRESS', '127.1.1.1')


def jar_available():
    try:
        pdns_util.pdns_output_jar()
        return True
    except Exception:
        return False


def pdns_db_available():
    try:
        create_engine(PDNS_DB_URI).connect().close()
        return True
    except Exception:
        return False


def pdns_available():
    if shutil.which('dig') is None:
        return False
    sock = socket.socket()
    sock.settimeout(2)
    try:
        sock.connect((PDNS_ADDRESS, 53))
        return True
    except OSError:
        return False
    finally:
        sock.close()


@contextmanager
def running_pdns_output():
    proc = pdns_util.test_pdns_output_process(False)
    try:
        yield proc
    finally:
        proc.kill()
        proc.wait()


def drain(proc):
    '''Keep the stdout pipe of pdns-output from filling up'''
    while select.select([proc.stdout], [], [], 0)[0]:
        if not os.read(proc.stdout.fileno(), 65536):
            return


def wait_for_updates(proc, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        drain(proc)
        db.session.rollback()
        if OutputUpdate.query.count() == 0:
            return
        if proc.poll() is not None:
            raise AssertionError('pdns-output exited with %s' % proc.returncode)
        time.sleep(0.1)
    raise AssertionError('pdns-output did not process all updates in %ds' % timeout)


@pytest.mark.skipif(not jar_available(), reason='pdns-output.jar not found')
@pytest.mark.skipif(not pdns_db_available(), reason='pdns database %s not reachable' % PDNS_DB_URI)
class PdnsOutputDnssecTest(PDNSTest):
    '''Signing done by pdns-output, checked in the pdns database and over axfr'''

    ZONE = 'dnssec.com'

    def setUp(self):
        PDNSTest.setUp(self)
        self.cleanup_pdns_db(PDNS_DB_URI)
        self.pdns = create_engine(PDNS_DB_URI)

    def sign_zone(self, **dnssec_options):
        '''Create a small signed zone and wait until pdns-output exported it'''
        self.r.zone_create(self.ZONE)
        self.create_output_for_zone(self.ZONE, 'pdns_output', 'pdns_group', db_uri=PDNS_DB_URI)
        self.r.zone_dnssec_enable(self.ZONE, ksk_bits=1024, zsk_bits=1024, **dnssec_options)
        self.r.rr_create(name='t.%s.' % self.ZONE, type='A', ip='1.1.1.1')
        self.r.rr_create(name='txt.%s.' % self.ZONE, type='TXT', strings=['hello'])
        with running_pdns_output() as proc:
            wait_for_updates(proc)

    def records(self, rrtype=None):
        query = "select records.name, records.type, records.content, records.ordername, records.auth" \
                " from records join domains on domains.id=records.domain_id where domains.name='%s'" % self.ZONE
        if rrtype:
            query += " and records.type='%s'" % rrtype
        with self.pdns.begin() as conn:
            return conn.execute(query).fetchall()

    def metadata(self):
        query = "select domainmetadata.kind, domainmetadata.content from domainmetadata" \
                " join domains on domains.id=domainmetadata.domain_id where domains.name='%s'" % self.ZONE
        with self.pdns.begin() as conn:
            return dict((row[0], row[1]) for row in conn.execute(query).fetchall())

    def test_zone_is_signed(self):
        self.sign_zone()
        dnskeys = self.records('DNSKEY')
        assert len(dnskeys) == 2, dnskeys
        assert all(row[0] == self.ZONE for row in dnskeys)
        rrsigs = self.records('RRSIG')
        assert rrsigs, 'no RRSIGs were exported'
        covered = set(row[2].split()[0] for row in rrsigs)
        # apex records and the rrsets below the apex must all be signed (a zone
        # created without a profile has no NS rrset)
        assert set(['SOA', 'DNSKEY', 'NSEC', 'A', 'TXT']) <= covered, covered
        assert self.metadata().get('SIGN_PARAM')

    def test_rrsig_inception_is_backdated_in_powerdns(self):
        now = datetime.datetime.utcnow()
        self.sign_zone()
        rrsigs = self.records('RRSIG')
        assert rrsigs, 'no RRSIGs were exported'
        for row in rrsigs:
            tokens = row[2].split()
            expiration_dt = datetime.datetime.strptime(tokens[4], '%Y%m%d%H%M%S')
            inception_dt = datetime.datetime.strptime(tokens[5], '%Y%m%d%H%M%S')
            assert now - datetime.timedelta(seconds=3660) <= inception_dt <= now - datetime.timedelta(seconds=3540)
            assert now + datetime.timedelta(days=13, hours=23) <= expiration_dt <= now + datetime.timedelta(days=14, hours=1)

    def test_nsec3_zone_is_signed(self):
        self.sign_zone(nsec3_algorithm=1, nsec3_iterations=10)
        assert self.metadata().get('NSEC3PARAM', '').startswith('1 0 10')
        # pdns computes the NSEC3 records from ordername, which pdns-output sets
        # for every authoritative rrset; RRSIG rows carry none
        assert all(row[3] is not None for row in self.records() if row[4] and row[1] != 'RRSIG'), self.records()

    def test_keys_are_removed_when_dnssec_is_disabled(self):
        self.sign_zone(nsec3_algorithm=1, nsec3_iterations=10)
        self.r.zone_dnssec_disable(self.ZONE)
        with running_pdns_output() as proc:
            wait_for_updates(proc)
        assert self.records('DNSKEY') == []
        assert self.records('RRSIG') == []
        metadata = self.metadata()
        assert 'NSEC3PARAM' not in metadata
        assert 'SIGN_PARAM' not in metadata

    @pytest.mark.skipif(not pdns_available(),
                        reason='no pdns instance with dig available at %s' % PDNS_ADDRESS)
    def test_axfr_verifies_with_jdnssec(self):
        self.sign_zone(nsec3_algorithm=1, nsec3_iterations=10)
        axfr = Popen(['dig', '+onesoa', '+noall', '+answer', 'axfr', self.ZONE, '@' + PDNS_ADDRESS],
                     stdout=PIPE).communicate()[0].decode('utf-8')
        # jdnssec-verifyzone is happy with an empty zone, so check there is
        # something to verify first
        assert 'RRSIG' in axfr, axfr
        assert 'NSEC3' in axfr, axfr
        pdns_util.verify_zone(axfr)
