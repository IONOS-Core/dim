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
}
