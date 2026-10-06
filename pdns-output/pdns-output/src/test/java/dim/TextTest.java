package dim;

import com.verisignlabs.dnssec.security.ZoneUtils;
import org.junit.Before;
import org.junit.Rule;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.Parameterized;
import org.xbill.DNS.Master;
import org.xbill.DNS.Name;
import org.xbill.DNS.Record;
import org.xbill.DNS.Type;

import java.io.*;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.stream.Collectors;
import java.util.stream.Stream;

class RecordOp {
    public final boolean delete;
    public final Record record;

    private RecordOp(Record r, boolean del) {
        record = r;
        delete = del;
    }

    public static RecordOp add(Record r) {
        return new RecordOp(r, false);
    }

    public static RecordOp del(Record r) {
        return new RecordOp(r, true);
    }
}

/**
 * Open a file with 1 resource record on each line.
 * First line is SOA, followed by DNSKEY rrs.
 * For each of the following lines, the record is added or removed (if line startsWith -), signed and checked.
 */
@RunWith(Parameterized.class)
public class TextTest {
    @Parameterized.Parameters(name = "{0}")
    public static String[] data() {
        ArrayList<String> files = new ArrayList<>();
        File folder = new File(TextTest.class.getResource("/text").getPath());
        for (final File fileEntry : folder.listFiles()) {
            files.add(fileEntry.getName());
        }
        return files.toArray(new String[0]);
    }

    @Parameterized.Parameter
    public String filename;

    private Signer signer;
    private Storage storage;
    private Name domain;
    private ZoneSignParameters zsp;

    @Rule
    public PdnsConnection pdns = new PdnsConnection();

    @Before
    public void setUp() throws IOException {
        storage = new PdnsDatabase(pdns.sql);
        signer = new Signer(storage);
    }

    @Test
    public void test() throws Exception {
        ArrayList<RecordOp> recordOps = readFile("/text/" + filename);
        Record soa = recordOps.remove(0).record;
        if (soa.getType() != Type.SOA)
            throw new Exception("The first line must be a SOA");
        HashSet<Record> dnskeys = new HashSet<>();
        while (!recordOps.isEmpty() && recordOps.get(0).record.getType() == Type.DNSKEY && !recordOps.get(0).delete) {
            dnskeys.add(recordOps.remove(0).record);
        }

        zsp = TestUtil.getZSP(dnskeys);
        List<Record> initialRecords = Stream.concat(
                Stream.of(soa, zsp.kskpairs.get(0).getDNSKEYRecord()), dnskeys.stream())
                .collect(Collectors.toList());
        domain = ZoneUtils.findZoneName(initialRecords);

        storage.createDomain(domain);
        storage.setNSECParameters(domain, zsp.nsecParameters);
        signer.signZone(domain, initialRecords, zsp);
        TestUtil.writeToFile(TestUtil.dumpToString(storage, domain, pdns.conn), "done");
        dumpOnFailure(() -> {
        });
        // TODO lame; this reads the files everytime there's a change to the dnskey rrset
        for (RecordOp op : recordOps) {
            dumpOnFailure(() -> {
                try {
                    if (op.delete) {
                        if (op.record.getType() == Type.DNSKEY) {
                            dnskeys.remove(op.record);
                            zsp = TestUtil.getZSP(dnskeys);
                        }
                        signer.incrementalDelete(domain, op.record, zsp);
                    } else {
                        if (op.record.getType() == Type.DNSKEY) {
                            dnskeys.add(op.record);
                            zsp = TestUtil.getZSP(dnskeys);
                        }
                        signer.incrementalAdd(domain, op.record, zsp);
                    }
                } catch (Exception e) {
                    throw new RuntimeException(e);
                }
            });
            TestUtil.writeToFile(TestUtil.dumpToString(storage, domain, pdns.conn), "done");
        }
    }

    // TODO lame
    private void dumpOnFailure(Runnable r) throws Exception {
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
            throw e;
        }
    }

    private ArrayList<RecordOp> readFile(String filename) throws Exception {
        ArrayList<RecordOp> l = new ArrayList<>();
        BufferedReader br = new BufferedReader(new InputStreamReader(TextTest.class.getResourceAsStream(filename)));
        String line;
        while ((line = br.readLine()) != null) {
            // Ignore comments and empty lines
            if (line.isEmpty() || line.startsWith("#"))
                continue;
            if (line.startsWith("-"))
                l.add(RecordOp.del(parseRecord(line.substring(1))));
            else
                l.add(RecordOp.add(parseRecord(line)));
        }
        return l;
    }

    private static Record parseRecord(String s) throws Exception {
        InputStream is = new ByteArrayInputStream(s.getBytes());
        Master m = new Master(is, null, 600);
        Record r;
        if ((r = m.nextRecord()) != null) {
            return r;
        } else
            throw new Exception("Failed to parse " + s);
    }
}
