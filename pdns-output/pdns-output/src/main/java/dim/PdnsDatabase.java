package dim;

import org.jooq.DSLContext;
import org.jooq.Result;
import org.jooq.exception.DataAccessException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.xbill.DNS.*;
import org.xbill.DNS.Record;

import java.io.IOException;
import java.util.*;

import static org.jooq.impl.DSL.field;

class NonexistentDomainException extends RuntimeException {
    public NonexistentDomainException(String message) {
        super(message);
    }
}

class PdnsDatabase implements Storage {
    private static final Logger log = LoggerFactory.getLogger(Signer.class);
    private static final String INSERT_STATEMENT = "INSERT INTO records (domain_id, name, rev_name, type, content, " +
            "ttl, prio, ordername, auth)";
    private static final String INSERT_BIND_VARS = "(?, ?, ?, ?, ?, ?, ?, ?, ?)";

    private final DSLContext sql;
    private final long maxQuerySize;

    PdnsDatabase(DSLContext sql) {
        this.sql = sql;
        maxQuerySize = 4000000;
    }

    PdnsDatabase(DSLContext sql, long maxQuerySize) {
        this.sql = sql;
        this.maxQuerySize = maxQuerySize;
    }

    @Override
    public boolean createDomain(Name domain) {
        try {
            getDomainId(domain);
            return false;
        } catch (RuntimeException e) {
        }

        sql.execute("INSERT INTO domains (name, type) VALUES (?, 'NATIVE')", str(domain));
        return true;
    }

    @Override
    public void deleteDomain(Name domain) {
        int domainId = getDomainId(domain);
        sql.execute("DELETE FROM records WHERE domain_id=?", domainId);
        sql.execute("DELETE FROM domainmetadata WHERE domain_id=:domain_id", domainId);
        sql.execute("DELETE FROM domains WHERE id=:domain_id", domainId);
    }

    @Override
    public void updateSerial(Name domain, long serial) {
        List<Row> soaList = getRRSet(domain, domain, Type.SOA);
        if (soaList.size() != 1)
            throw new RuntimeException("Missing or duplicate SOA for zone " + domain);
        Row soa = soaList.get(0);
        SOARecord old = (SOARecord) soa.record;
        SOARecord updated = new SOARecord(old.getName(), old.getDClass(), old.getTTL(), old.getHost(), old.getAdmin(),
                serial, old.getRefresh(), old.getRetry(), old.getExpire(), old.getMinimum());
        sql.execute("UPDATE records SET content=? WHERE id=?", content(updated), soa.id);
    }

    @Override
    public void updateSOA(Name domain, long ttl, String rdata) {
        // TODO strip dots??
        sql.execute("UPDATE records SET ttl=?, content=? WHERE name=? AND type=?", ttl, rdata, str(domain), "SOA");
    }

    @Override
    public List<Row> getRRSet(Name domain, Name name, int type) {
        return getDNSRows(sql.fetch(rrQuery("records.*", type), str(domain), str(name)));
    }

    @Override
    public boolean rrsetExists(Name domain, Name name, int type) {
        return sql.fetchOne(rrQuery("COUNT(*)", type), str(domain), str(name)).get(0, int.class) != 0;
    }

    private String addTypeFilter(String query, int type) {
        if (type == Type.NULL)
            query += " AND records.type IS NULL";
        else if (type != 0)
            query += String.format(" AND records.type = '%s'", Type.string(type));
        return query;
    }

    private String rrQuery(String select, int type) {
        String query = String.format("SELECT %s FROM records JOIN domains ON domains.id=records.domain_id" +
                " WHERE domains.name = ? AND records.name = ?", select);
        return addTypeFilter(query, type);
    }

    private boolean addToPdns(Row row) {
        // NSEC3PARAM, NSEC and NSEC3 records must not be added to pdns zones
        return !(row.record.getType() == Type.NSEC || row.record.getType() == Type.NSEC3 || row.record.getType() ==
                Type.NSEC3PARAM);
    }

    private String reverseString(String s) {
        return new StringBuilder(s).reverse().toString();
    }

    @Override
    public void addRow(Name domain, Row row) {
        if (!addToPdns(row))
            return;

        int domain_id = getDomainId(domain);
        Record record = row.record;
        if (sql.selectCount().from("records")
                .where("records.domain_id=?", domain_id)
                .and("records.name=?", name(record))
                .and(field("records.type").eq(type(record)))
                .and(field("records.content").eq(content(record))).fetchOne(0, int.class) == 0) {
            String record_name = name(record);
            sql.execute(INSERT_STATEMENT + " VALUES " + INSERT_BIND_VARS,
                    domain_id, record_name, reverseString(record_name), type(record), content(record), ttl(record),
                    prio(record), row.ordername, row.auth);
        }
    }

    @Override
    public void addRows(Name domain, List<Row> rows) {
        int domain_id = getDomainId(domain);

        ListIterator<Row> it = rows.listIterator();
        // There's a limit on the number of bind variables for a query
        // Going here with 64K bind variables / 9 bind variables/row = 7K inserts / packet
        final int MAX_VALUES = 7000;
        while (it.hasNext()) {
            List<Row> values = new ArrayList<>();
            int packetSize = 100;
            int count = 0;

            while (it.hasNext()) {
                if (count >= MAX_VALUES)
                    break;
                Row row = it.next();
                if (!addToPdns(row))
                    continue;
                int rowBytes = 100 + 2 * name(row.record).length();
                String rowContent = content(row.record);
                if (rowContent != null)
                    rowBytes += rowContent.length();
                if (rowBytes + packetSize >= maxQuerySize && it.hasPrevious()) {
                    it.previous();
                    break;
                }
                values.add(row);
                packetSize += rowBytes;
                count++;
            }
            if (values.isEmpty())
                break;

            ArrayList<Object> binds = new ArrayList<>();
            for (Row row : values) {
                Record record = row.record;
                String record_name = name(record);
                Object tmp[] = {domain_id, record_name, reverseString(record_name), type(record), content(record), ttl(record), prio(record),
                        row.ordername, row.auth};
                binds.addAll(new ArrayList<Object>(Arrays.asList(tmp)));
            }
            log.debug("Running insert query with {} rows and {} bytes", values.size(), packetSize);
            sql.execute(INSERT_STATEMENT + " VALUES " + String.join(", ",
                    Collections.nCopies(values.size(), INSERT_BIND_VARS)), binds.toArray());
        }
    }

    @Override
    public int deleteRowById(Name domain, Integer id) {
        if (id == null)
            throw new RuntimeException("Tried to delete a row with null id");
        return sql.execute("DELETE records FROM records JOIN domains ON domains.id=records.domain_id" +
                " WHERE domains.name=? AND records.id=?", str(domain), id);
    }

    @Override
    public int deleteRRSet(Name domain, Name name, int type) {
        return deleteRecordString(domain, name, type, null);
    }

    @Override
    public int deleteRecord(Name domain, Record record) {
        return deleteRecordString(domain, record.getName(), record.getType(), content(record));
    }

    private int deleteRecordString(Name domain, Name name, int type, String rdata) {
        String query = "DELETE records FROM records JOIN domains ON domains.id=records.domain_id" +
                " WHERE domains.name=? AND records.name=?";
        query = addTypeFilter(query, type);
        if (rdata != null)
            return sql.execute(query + " AND records.content=?", str(domain), str(name), rdata);
        else
            return sql.execute(query, str(domain), str(name));
    }

    @Override
    public Name getNextName(Name domain, String ordername) {
        return getNextPrevName(domain, ordername, ">");
    }

    @Override
    public Name getPrevName(Name domain, String ordername) {
        return getNextPrevName(domain, ordername, "<");
    }

    private Name getNextPrevName(Name domain, String ordername, String dir) {
        String query = "SELECT records.name FROM records JOIN domains ON domains.id=records.domain_id" +
                " WHERE domains.name=? AND %s ORDER BY ordername " + (dir.equals("<") ? "DESC" : "ASC") + " LIMIT 1";
        org.jooq.Record result;
        result = sql.fetchOne(String.format(query, "ordername " + dir + " ?"), str(domain), ordername);
        if (result == null)
            result = sql.fetchOne(String.format(query, "ordername IS NOT NULL"), str(domain));
        if (result == null)
            return null;
        else
            try {
                return new Name(result.get(0, String.class) + ".");
            } catch (TextParseException e) {
                throw new DataAccessException("Bad data", e);
            }
    }

    @Override
    public void addOrReplaceNSEC(Name domain, Record nsec) {
        // NSEC and NSEC3 records must not be added to pdns zones, nothing to do here
    }

    private final String subzoneCondition = "domains.name = ? AND (records.name = ? " +
            " OR records.rev_name LIKE CONCAT(?, '.%'))";

    @Override
    public List<Row> getSubzoneRecords(Name domain, Name name, ExcludeType exclude) {
        String query = "SELECT records.* FROM records JOIN domains ON domains.id=records.domain_id WHERE " +
                subzoneCondition;
        switch (exclude) {
            case EMPTY_NON_TERMINAL:
                query += " AND records.type IS NOT NULL";
                break;
            case GENERATED:
                query += " AND records.type IS NOT NULL AND records.type NOT IN ('RRSIG', 'NSEC', 'NSEC3', 'DNSKEY')";
                break;
        }
        return getDNSRows(sql.fetch(query, str(domain), str(name), reverseString(str(name))));
    }

    @Override
    public void removeAuthAndOrdernameForSubzone(Name domain, Name name) {
        sql.execute("UPDATE records JOIN domains ON domains.id=records.domain_id SET auth = 0, ordername = NULL WHERE "
                        + subzoneCondition + " AND records.type NOT IN ('NS', 'DS')",
                str(domain), str(name), reverseString(str(name)));
    }

    @Override
    public void updateAuthAndOrdernameForName(Name domain, Name name, String ordername, boolean auth) {
        sql.execute("UPDATE records JOIN domains ON domains.id=records.domain_id SET auth = ?, ordername = ?" +
                        " WHERE domains.name = ? AND records.name = ? AND records.type NOT IN ('RRSIG', 'NSEC', " +
                        "'NSEC3')",
                auth, ordername, str(domain), str(name));
    }

    @Override
    public int subzoneHasOrdernames(Name domain, Name name) {
        String query = "SELECT COUNT(*) FROM records JOIN domains ON domains.id=records.domain_id WHERE" +
                " ordername IS NOT NULL AND " + subzoneCondition;
        return sql.fetchOne(query, str(domain), str(name), reverseString(str(name))).get(0, int.class);
    }

    @Override
    public void setNSECParameters(Name domain, NSECParameters np) {
        if (!isSigned(domain))
            setSignStatus(domain, true);
        sql.execute("DELETE domainmetadata FROM domainmetadata JOIN domains ON domains.id=domainmetadata.domain_id" +
                " WHERE domains.name=? AND kind='NSEC3PARAM'", str(domain));
        if (np.nsecMode != NSECParameters.NSECMode.NSEC_MODE) {
            int domain_id = getDomainId(domain);
            sql.execute("INSERT INTO domainmetadata (domain_id, kind, content) VALUES (?, 'NSEC3PARAM', ?)",
                    domain_id, np.toString());
        }
    }

    @Override
    public void setSignParameters(Name domain, String parameters) {
        sql.execute("DELETE domainmetadata FROM domainmetadata JOIN domains ON domains.id=domainmetadata.domain_id" +
                " WHERE domains.name=? AND kind='SIGN_PARAM'", str(domain));
        int domain_id = getDomainId(domain);
        sql.execute("INSERT INTO domainmetadata (domain_id, kind, content) VALUES (?, 'SIGN_PARAM', ?)",
                domain_id, parameters);
    }

    @Override
    public String getSignParameters(Name domain) {
        String query = "SELECT domainmetadata.content FROM domainmetadata JOIN domains ON domains.id=domainmetadata" +
                ".domain_id WHERE" +
                " domains.name=? AND kind=?";
        org.jooq.Record result = sql.fetchOne(query, str(domain), "SIGN_PARAM");
        if (result == null)
            return null;
        return result.get(0, String.class);
    }

    @Override
    public NSECParameters getNSECParameters(Name domain) {
        String query = "SELECT domainmetadata.content FROM domainmetadata JOIN domains ON domains.id=domainmetadata" +
                ".domain_id WHERE" +
                " domains.name=? AND kind=?";
        org.jooq.Record result = sql.fetchOne(query, str(domain), "NSEC3PARAM");
        if (result == null)
            return NSECParameters.NSEC();
        else
            return NSECParameters.fromString(result.get(0, String.class));
    }

    @Override
    public void deleteRecords(Name domain) {
        sql.execute("DELETE records FROM records JOIN domains ON domains.id=records.domain_id WHERE domains.name=?",
                str(domain));
    }

    @Override
    public boolean isSigned(Name domain) {
        String query = "SELECT COUNT(*) FROM domainmetadata JOIN domains ON domains.id=domainmetadata.domain_id WHERE" +
                " domains.name=? AND kind=?";
        return sql.fetchOne(query, str(domain), "PRESIGNED").get(0, int.class) == 1;
    }

    @Override
    public void setSignStatus(Name domain, boolean status) {
        if (status) {
            int domain_id = getDomainId(domain);
            sql.execute("INSERT INTO domainmetadata (domain_id, kind, content) VALUES (?, 'PRESIGNED', 1)", domain_id);
        } else {
            sql.execute("DELETE domainmetadata FROM domainmetadata" +
                            " JOIN domains ON domains.id=domainmetadata.domain_id" +
                            " WHERE domains.name=? AND domainmetadata.kind in ('PRESIGNED', 'NSEC3PARAM', " +
                            "'SIGN_PARAM')",
                    str(domain));
        }
    }

    @Override
    public void deleteDNSSECRecords(Name domain) {
        sql.execute("DELETE records FROM records JOIN domains ON domains.id=records.domain_id WHERE domains.name=?" +
                " AND records.type in ('RRSIG', 'DNSKEY')", str(domain));
    }

    private static Record rowToRecord(org.jooq.Record row) {
        try {
            String strType = row.get("type", String.class);
            if (strType != null) {
                int rtype = Type.value(strType);
                if (rtype < 0)
                    throw new TextParseException("Invalid type '" + strType + "'");
                String content = row.get("content", String.class);
                if (rtype == Type.MX || rtype == Type.SRV)
                    content = row.get("prio", int.class) + " " + content;
                return Record.fromString(
                        Name.fromString(row.get("name", String.class), new Name(".")),
                        rtype,
                        DClass.IN,
                        row.get("ttl", int.class),
                        content,
                        new Name("."));
            } else {
                return new NULLRecord(new Name(row.get("name", String.class) + "."), DClass.IN, 0, new byte[0]);
            }
        } catch (IOException e) {
            throw new DataAccessException("Bad data", e);
        }
    }

    private List<Row> getDNSRows(Result<org.jooq.Record> results) {
        ArrayList<Row> result = new ArrayList<>();
        for (org.jooq.Record row : results) {
            result.add(new Row(rowToRecord(row), row.get("ordername", String.class), row.get("auth", Boolean.class),
                    row.get("id", Integer.class)));
        }
        return result;
    }

    private int getDomainId(Name domain) {
        final org.jooq.Record record = sql.fetchOne("SELECT id FROM domains WHERE name=?", str(domain));
        if (record == null)
            throw new NonexistentDomainException("Zone " + str(domain) + " not found");
        return record.get(0, int.class);
    }

    /**
     * Return the string representation without the final dot (as pdns requires it)
     */
    private static String str(Name domain) {
        return domain.toString(true);
    }

    private static String name(Record r) {
        return str(r.getName());
    }

    // NULL records are used for empty non-terminals
    private static String type(Record r) {
        return r.getType() == Type.NULL ? null : Type.string(r.getType());
    }

    private static Long ttl(Record r) {
        return r.getType() == Type.NULL ? null : r.getTTL();
    }

    private static String content(Record r) {
        if (r.getType() == Type.NULL)
            return null;
        else
            return r.pdnsContent();
    }

    private static Integer prio(Record r) {
        switch (r.getType()) {
            case Type.MX:
                return ((MXRecord) r).getPriority();
            case Type.SRV:
                return ((SRVRecord) r).getPriority();
            default:
                return null;
        }
    }
}
