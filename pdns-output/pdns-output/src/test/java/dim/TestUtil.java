package dim;

import com.verisignlabs.dnssec.security.DnsKeyPair;
import com.verisignlabs.dnssec.security.RecordComparator;
import com.verisignlabs.dnssec.security.ZoneVerifier;
import org.apache.commons.io.IOUtils;
import org.xbill.DNS.*;
import org.xbill.DNS.Record;

import java.io.*;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URL;
import java.nio.charset.Charset;
import java.sql.Connection;
import java.text.SimpleDateFormat;
import java.util.*;
import java.util.stream.Collectors;
import java.util.stream.Stream;

class KeysPairs {
    public List<DnsKeyPair> kskpairs, zskpairs;

    public KeysPairs(List<DnsKeyPair> k, List<DnsKeyPair> z) {
        kskpairs = k;
        zskpairs = z;
    }
}

public class TestUtil {
    private static String pdnsIp = "127.0.0.1";
    private static int pdnsPort = 5153;

    private static DNSKEYRecord loadPublicKeyFile(URL url) throws IOException {
        try (InputStream stream = url.openStream()) {
            Master m = new Master(stream, null, 600);
            Record r;
            DNSKEYRecord result = null;
            while ((r = m.nextRecord()) != null) {
                if (r.getType() == Type.DNSKEY) {
                    result = (DNSKEYRecord) r;
                }
            }
            return result;
        }
    }

    private static DnsKeyPair loadKeyPair(String resourceName) throws IOException {
        DnsKeyPair kp = new DnsKeyPair();

        DNSKEYRecord kr = loadPublicKeyFile(TestUtil.class.getResource(resourceName + ".key"));
        kp.setDNSKEYRecord(kr);

        String pk = IOUtils.toString(TestUtil.class.getResource(resourceName + ".private"), Charset.defaultCharset());
        kp.setPrivateKeyString(pk);

        return kp;
    }

    public static KeysPairs loadKeys() throws IOException {
        List<DnsKeyPair> kskpairs, zskpairs;
        kskpairs = Collections.singletonList(TestUtil.loadKeyPair("/web.de.ksk"));
        zskpairs = Stream.of("/web.de.zsk", "/web.de.zsk2").map(f -> {
            try {
                return TestUtil.loadKeyPair(f);
            } catch (IOException e) {
                throw new RuntimeException(e);
            }
        }).collect(Collectors.toList());
        return new KeysPairs(kskpairs, zskpairs);
    }

    public static ZoneSignParameters getZSP(HashSet<Record> dnskeyRecords) throws Exception {
        SimpleDateFormat df = new SimpleDateFormat("yyyy-MM-dd z");
        Date begin = df.parse("2016-01-01 GMT");
        Date end = df.parse("2037-01-01 GMT");
        KeysPairs keys = TestUtil.loadKeys();
        List<DnsKeyPair> zkspairs = keys.zskpairs.stream()
                .filter(pair -> dnskeyRecords.contains(pair.getDNSKEYRecord())).collect(Collectors.toList());
        return new ZoneSignParameters(keys.kskpairs, zkspairs, begin, end, NSECParameters.NSEC3(0, null));
    }

    public static ArrayList<Record> readFile(String filename) throws Exception {
        ArrayList<Record> records = new ArrayList<>();
        Master m = new Master(TestUtil.class.getResourceAsStream(filename));
        Record r;
        while ((r = m.nextRecord()) != null) {
            records.add(r);
        }
        if (records.size() == 0)
            throw new Exception("error: empty zone file");
        return records;
    }

    public static void writeToFile(String content, String filename) {
        try {
            PrintWriter out = new PrintWriter(filename);
            out.write(content);
            out.close();
        } catch (FileNotFoundException e) {
            e.printStackTrace();
        }
    }

    public static String dumpToString(Storage storage, Name domain, Connection conn) throws Exception {
        List<Record> records = getZoneRecords(storage, domain, conn);
        return dumpToString(records);
    }

    public static String dumpToString(List<Record> records) {
        Collections.sort(records, new RecordComparator());
        StringWriter sw = new StringWriter();
        for (Record r : records) {
            sw.write(r.toString());
            sw.write("\n");
        }
        return sw.toString();
    }

    public static List<Record> getZoneRecords(Storage storage, Name domain, Connection conn) throws Exception {
        List<Record> records = null;
        if (storage instanceof MockStorage) {
            records = storage.getSubzoneRecords(domain, domain, ExcludeType.EMPTY_NON_TERMINAL).stream()
                    .map(row -> row.record).collect(Collectors.toList());
        } else if (storage instanceof PdnsDatabase) {
            conn.commit();
            ZoneTransferIn zfi = ZoneTransferIn.newAXFR(new Name("web.de."),
                    new InetSocketAddress(InetAddress.getByName(pdnsIp), pdnsPort), null);
            List<Object> l = zfi.run();
            // Skip the first record (always SOA) because it's also present at the end of the list
            records = l.stream().skip(1).map(o -> (Record) o).collect(Collectors.toList());
        }
        return records;
    }

    public static void checkZone(Storage storage, Name domain, Connection conn) throws Exception {
        List<Record> records = getZoneRecords(storage, domain, conn);
        if (records == null)
            return;

        ZoneVerifier zv = new ZoneVerifier();
        if (zv.verifyZone(records) > 0)
            throw new Exception("Verification failed");
    }
}
