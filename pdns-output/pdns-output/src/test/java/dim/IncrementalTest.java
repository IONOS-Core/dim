package dim;

import com.verisignlabs.dnssec.security.DnsKeyPair;
import com.verisignlabs.dnssec.security.ZoneUtils;
import org.junit.Before;
import org.junit.Rule;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.Parameterized;
import org.xbill.DNS.*;
import org.xbill.DNS.Record;

import java.sql.SQLException;
import java.text.SimpleDateFormat;
import java.time.Instant;
import java.util.*;
import java.util.stream.Collectors;


@RunWith(Parameterized.class)
public class IncrementalTest {
    private static Name domain;
    private static final List<Record> initialRecords = new ArrayList<>();

    @Parameterized.Parameters
    public static Collection<ZoneSignParameters> data() throws Exception {
        SimpleDateFormat df = new SimpleDateFormat("yyyy-MM-dd z");
        Date begin = df.parse("2016-01-01 GMT");
        Date end = df.parse("2037-01-01 GMT");

        initialRecords.addAll(TestUtil.readFile("/web.de"));
        // calculate the zone name.
        domain = ZoneUtils.findZoneName(initialRecords);
        if (domain == null)
            throw new Exception("error: invalid zone file - no SOA");

        KeysPairs keys = TestUtil.loadKeys();
        List<DnsKeyPair> kskpairs = keys.kskpairs, zskpairs = keys.zskpairs;
        initialRecords.addAll(kskpairs.stream().map(DnsKeyPair::getDNSKEYRecord).collect(Collectors.toList()));
        initialRecords.addAll(zskpairs.stream().map(DnsKeyPair::getDNSKEYRecord).collect(Collectors.toList()));
        ZoneSignParameters zsp = new ZoneSignParameters(kskpairs, zskpairs, begin, end, NSECParameters.NSEC());
        ZoneSignParameters zsp_nsec3 = new ZoneSignParameters(kskpairs, zskpairs, begin, end,
                NSECParameters.NSEC3(0, null));
        return Arrays.asList(zsp, zsp_nsec3);
    }

    private final ZoneSignParameters zsp;

    public IncrementalTest(ZoneSignParameters zsp) {
        this.zsp = zsp;
    }

    @Rule
    public PdnsConnection pdns = new PdnsConnection();
    private Signer signer;
    private Storage storage;

    @Before
    public void setUp() {
        storage = new PdnsDatabase(pdns.sql);
        signer = new Signer(storage);
    }

    public void dumpOnFailure(Runnable r) throws Exception {
        String before = TestUtil.dumpToString(storage, domain, pdns.conn);
        try {
            r.run();
            TestUtil.checkZone(storage, domain, pdns.conn);
        } catch (Exception e) {
            try {
                TestUtil.writeToFile(before, "before");
                TestUtil.writeToFile(TestUtil.dumpToString(storage, domain, pdns.conn), "after");
            } catch (Exception e1) {
                e1.printStackTrace();
            }
            throw new RuntimeException(e);
        }
    }

    @Test
    public void simple() throws Exception {
        storage.createDomain(domain);
        storage.setNSECParameters(domain, zsp.nsecParameters);
        signer.signZone(domain, initialRecords, zsp);
        dumpOnFailure(() -> {
        });
        List<Record> records = Arrays.asList(new TXTRecord(new Name("web.de."), DClass.IN, 666, "u wot m8"),
                new TXTRecord(new Name("newname.web.de."), DClass.IN, 6666, "u wot m9"),
                new NSRecord(new Name("subzone.web.de."), DClass.IN, 1337, new Name("ns1.subzone.web.de.")),
                new TXTRecord(new Name("subzone3.web.de."), DClass.IN, 6666, "sup"));
        for (Record r : records) {
            dumpOnFailure(() -> {
                try {
                    signer.incrementalAdd(domain, r, zsp);
                } catch (Exception e) {
                    throw new RuntimeException(e);
                }
            });
        }
        List<Record> deleted = Arrays.asList(new TXTRecord(new Name("web.de."), DClass.IN, 666, "u wot m8"),
                new TXTRecord(new Name("newname.web.de."), DClass.IN, 6666, "u wot m9"),
                new TXTRecord(new Name("txt.subzone.web.de."), DClass.IN, 100, "wat.com."),
                new NSRecord(new Name("subzone.web.de."), DClass.IN, 1337, new Name("ns1.subzone.web.de.")),
                new TXTRecord(new Name("subzone3.web.de."), DClass.IN, 6666, "sup"));
        for (Record r : deleted) {
            dumpOnFailure(() -> {
                try {
                    signer.incrementalDelete(domain, r, zsp);
                } catch (Exception e) {
                    throw new RuntimeException(e);
                }
            });
        }
    }

    @Test
    public void changeNSECMode() throws Exception {
        storage.createDomain(domain);
        storage.setNSECParameters(domain, zsp.nsecParameters);
        signer.signZone(domain, initialRecords, zsp);
        dumpOnFailure(() -> {
        });
        for (NSECParameters mode : new NSECParameters[]{NSECParameters.NSEC(), NSECParameters.NSEC3(0, null)}) {
            dumpOnFailure(() -> {
                // TODO sup java?
                try {
                    storage.setNSECParameters(domain, mode);
                    signer.resignZone(domain, zsp.withNSECParameters(mode));
                } catch (Exception e) {
                    throw new RuntimeException(e);
                }
            });
        }
    }

    @Test
    public void random() throws Exception {
        final int ITERATIONS = 10;
        long seed = System.nanoTime();
        System.out.println("Using random seed " + seed);
        Random random = new Random(seed);
        List<Record> in = initialRecords.stream().filter(r -> r.getType() != Type.SOA && r.getType() != Type.DNSKEY)
                .collect(Collectors.toList());
        List<Record> out = new ArrayList<>();
        storage.createDomain(domain);
        storage.setNSECParameters(domain, zsp.nsecParameters);
        signer.signZone(domain, initialRecords, zsp);
        for (int i = 0; i < ITERATIONS; ++i) {
            if (!in.isEmpty()) {
                int x = random.nextInt(in.size());
                for (int j = 0; j < x; ++j) {
                    int pos = random.nextInt(in.size());
                    Record record = in.remove(pos);
                    assert record.getType() != Type.NSEC;
                    dumpOnFailure(() -> {
                        try {
                            signer.incrementalDelete(domain, record, zsp);
                        } catch (Exception e) {
                            throw new RuntimeException(e);
                        }
                    });
                    out.add(record);
                }
            }
            if (!out.isEmpty()) {
                int x = random.nextInt(out.size());
                for (int j = 0; j < x; ++j) {
                    int pos = random.nextInt(out.size());
                    Record record = out.remove(pos);
                    assert record.getType() != Type.NSEC;
                    dumpOnFailure(() -> {
                        try {
                            signer.incrementalAdd(domain, record, zsp);
                        } catch (Exception e) {
                            throw new RuntimeException(e);
                        }
                    });
                    in.add(record);
                }
            }
        }
    }

    @Test
    public void testUpdateSoaResignsRecord() throws Exception {
        storage.createDomain(domain);
        storage.setNSECParameters(domain, zsp.nsecParameters);
        signer.signZone(domain, initialRecords, zsp);

        // Initially valid
        TestUtil.checkZone(storage, domain, pdns.conn);

        // Update SOA content and re-sign SOA RRset
        storage.updateSOA(domain, 86400, "ns1.web.de. hostmaster.web.de. 2016010199 28800 7200 604800 86400");
        signer.signRRSet(domain, domain, Type.SOA, zsp);

        // Zone must verify with 0 errors
        TestUtil.checkZone(storage, domain, pdns.conn);
    }

    @Test
    public void testIncrementalAddOnAgedZonePassesGatekeeper() throws Exception {
        // Construct aged parameters: begin 8 days in past, end 6 days in future (~57% elapsed)
        Date agedBegin = Date.from(Instant.now().minusSeconds(86400 * 8));
        Date agedEnd = Date.from(Instant.now().plusSeconds(86400 * 6));
        ZoneSignParameters agedZsp = new ZoneSignParameters(zsp.kskpairs, zsp.zskpairs, agedBegin, agedEnd, zsp.nsecParameters);

        // Pre-validate with gatekeeper (must pass without exception)
        DnssecGatekeeper.validateParameters(domain, agedZsp);

        storage.createDomain(domain);
        storage.setNSECParameters(domain, agedZsp.nsecParameters);
        signer.signZone(domain, initialRecords, agedZsp);

        // Initially valid
        TestUtil.checkZone(storage, domain, pdns.conn);

        // Incrementally add a new record using aged parameters
        Record agedRecord = new ARecord(new Name("aged." + domain), DClass.IN, 86400, java.net.InetAddress.getByName("2.2.2.2"));
        signer.incrementalAdd(domain, agedRecord, agedZsp);

        // Zone must verify with 0 errors
        TestUtil.checkZone(storage, domain, pdns.conn);
    }

    @Test
    public void testIncrementalAddOnNearExpiredZonePassesGatekeeper() throws Exception {
        // Construct near-expired parameters: begin 13 days in past, end 1 day in future (~93% elapsed)
        Date nearExpiredBegin = Date.from(Instant.now().minusSeconds(86400 * 13));
        Date nearExpiredEnd = Date.from(Instant.now().plusSeconds(86400 * 1));
        ZoneSignParameters nearExpiredZsp = new ZoneSignParameters(zsp.kskpairs, zsp.zskpairs, nearExpiredBegin, nearExpiredEnd, zsp.nsecParameters);

        // Pre-validate with gatekeeper (must pass cleanly)
        DnssecGatekeeper.validateParameters(domain, nearExpiredZsp);

        storage.createDomain(domain);
        storage.setNSECParameters(domain, nearExpiredZsp.nsecParameters);
        signer.signZone(domain, initialRecords, nearExpiredZsp);

        // Verify initial state
        TestUtil.checkZone(storage, domain, pdns.conn);

        // Incrementally add a new record using near-expired parameters
        Record record = new ARecord(new Name("near." + domain), DClass.IN, 86400, java.net.InetAddress.getByName("3.3.3.3"));
        signer.incrementalAdd(domain, record, nearExpiredZsp);

        // Zone must verify with 0 errors
        TestUtil.checkZone(storage, domain, pdns.conn);
    }

    @Test(expected = SignatureRefusedException.class)
    public void testIncrementalAddOnExpiredZoneThrowsSignatureRefused() throws Exception {
        // Construct expired parameters: begin 15 days in past, end 1 day in past (already expired!)
        Date expiredBegin = Date.from(Instant.now().minusSeconds(86400 * 15));
        Date expiredEnd = Date.from(Instant.now().minusSeconds(86400 * 1));
        ZoneSignParameters expiredZsp = new ZoneSignParameters(zsp.kskpairs, zsp.zskpairs, expiredBegin, expiredEnd, zsp.nsecParameters);

        // Gatekeeper MUST throw SignatureRefusedException
        DnssecGatekeeper.validateParameters(domain, expiredZsp);
    }
}
