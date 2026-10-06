package dim;

import org.junit.Before;
import org.junit.Rule;
import org.junit.Test;
import org.xbill.DNS.*;
import org.xbill.DNS.Record;

import java.util.List;

import static org.junit.Assert.*;

public class PdnsDatabaseTest {
    @Rule
    public PdnsConnection pdns = new PdnsConnection();
    private Storage storage;

    @Before
    public void setUp() {
        storage = new PdnsDatabase(pdns.sql);
    }

    @Test
    public void addDelete() throws TextParseException {
        Name txtName = new Name("txt.web.de.");
        Name domain = new Name("web.de.");
        Row row = new Row(new TXTRecord(txtName, DClass.IN, 666, "wot"));

        storage.createDomain(domain);
        storage.addRow(domain, row);

        assertEquals(1, storage.getRRSet(domain, txtName).size());
        assertEquals(1, storage.getRRSet(domain, txtName, Type.TXT).size());
        assertTrue(storage.rrsetExists(domain, txtName));
        assertTrue(storage.rrsetExists(domain, txtName, Type.TXT));

        storage.deleteRowById(domain, storage.getRRSet(domain, txtName).get(0).id);
        assertFalse(storage.rrsetExists(domain, txtName));
    }

    @Test
    public void ordername() throws TextParseException {
        Name txtName = new Name("txt.web.de.");
        Name domain = new Name("web.de.");
        String ordername = "ordername", rdata = "wot";

        storage.createDomain(domain);
        storage.addRow(domain, new Row(new TXTRecord(txtName, DClass.IN, 666, rdata)));
        storage.updateAuthAndOrdernameForName(domain, txtName, ordername, true);

        List<Row> rrs = storage.getRRSet(domain, txtName);
        assertEquals(1, rrs.size());
        Row row = rrs.get(0);
        assertEquals(true, row.auth);
        assertEquals(ordername, row.ordername);

        assertNotNull(storage.getPrevName(domain, "zzzz"));
        assertNotNull(storage.getNextName(domain, "zzzz"));
        storage.deleteRecord(domain, row.record);
        assertFalse(storage.rrsetExists(domain, txtName, Type.TXT));
    }
}
