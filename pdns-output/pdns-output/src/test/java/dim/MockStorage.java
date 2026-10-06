package dim;

import org.xbill.DNS.*;
import org.xbill.DNS.Record;

import java.io.IOException;
import java.util.*;
import java.util.stream.Collectors;

/**
 * Mock pdns mysql storage
 */
class MockStorage implements Storage {
    // Map domain name to mysql rows (each row represents a record)
    private final Map<Name, List<Row>> rows = new TreeMap<>();
    private final Map<Name, NSECParameters> nsecParameters = new TreeMap<>();
    private int id = 1;

    private static Row cloneRow(Row row) {
        return new Row(row.record.withName(row.record.getName()), row.ordername, row.auth, row.id);
    }

    public List<Row> getRRSet(Name domain, Name name, int type) {
        if (type != 0)
            Type.check(type);
        if (!rows.containsKey(domain))
            return new ArrayList<>();
        return rows.get(domain).stream()
                .filter(row -> row.record.getName().equals(name) && (type == 0 || row.record.getType() == type))
                .map(MockStorage::cloneRow)
                .collect(Collectors.toList());
    }

    public boolean rrsetExists(Name domain, Name name, int type) {
        return !getRRSet(domain, name, type).isEmpty();
    }

    public void addRow(Name domain, Row row) {
        row.id = id++;
        rows.get(domain).add(row);
        int ents = getRRSet(domain, row.record.getName(), Type.NULL).size();
        if (ents > 1)
            assert false;
        if (ents > 0 && getRRSet(domain, row.record.getName()).size() != ents)
            assert false;
    }

    public void addRows(Name domain, List<Row> rows) {
        for (Row row : rows)
            addRow(domain, row);
    }

    public int deleteRowById(Name domain, Integer id) {
        if (id == null)
            throw new RuntimeException("Tried to delete a row with null id");
        if (!rows.containsKey(domain))
            return 0;
        for (Iterator<Row> iterator = rows.get(domain).iterator(); iterator.hasNext(); ) {
            Row row = iterator.next();
            if (row.id.equals(id)) {
                iterator.remove();
                return 1;
            }
        }
        return 0;
    }

    @Override
    public int deleteRecord(Name domain, Record record) {
        return deleteRecordString(domain, record.getName(), record.getType(), record.rdataToString());
    }

    @Override
    public int deleteRRSet(Name domain, Name name, int type) {
        return deleteRecordString(domain, name, type, null);
    }

    private int deleteRecordString(Name domain, Name name, int type, String rdata) {
        if (!rows.containsKey(domain))
            return 0;
        List<Row> l = rows.get(domain);
        int deleted = 0;
        for (Iterator<Row> iterator = l.iterator(); iterator.hasNext(); ) {
            Row row = iterator.next();
            if (row.record.getName().equals(name) && row.record.getType() == type &&
                    (rdata == null || rdata.equals(row.record.rdataToString()))) {
                iterator.remove();
                deleted++;
            }
        }
        return deleted;
    }

    public Name getNextName(Name domain, String ordername) {
        if (!rows.containsKey(domain) || rows.get(domain).isEmpty())
            return null;
        return rows.get(domain).stream().filter(row -> row.ordername != null && row.ordername.compareTo(ordername) > 0)
                .sorted(new OrdernameComparator()).map(row -> row.record.getName()).findFirst()
                .orElse(rows.get(domain).stream()
                        .filter(row -> row.ordername != null && row.ordername.compareTo(ordername) < 0)
                        .sorted(new OrdernameComparator()).map(row -> row.record.getName()).findFirst().orElse(null));
    }

    public Name getPrevName(Name domain, String ordername) {
        if (!rows.containsKey(domain) || rows.get(domain).isEmpty())
            return null;
        return rows.get(domain).stream().filter(row -> row.ordername != null && row.ordername.compareTo(ordername) < 0)
                .sorted(new OrdernameComparator().reversed()).map(row -> row.record.getName()).findFirst()
                .orElse(rows.get(domain).stream()
                        .filter(row -> row.ordername != null && row.ordername.compareTo(ordername) > 0)
                        .sorted(new OrdernameComparator().reversed()).map(row -> row.record.getName()).findFirst()
                        .orElse(null));
    }

    public void addOrReplaceNSEC(Name domain, Record nsec) {
        if (!rows.containsKey(domain))
            return;
        List<Row> l = rows.get(domain);
        for (Row row : l)
            if (row.record.getName().equals(nsec.getName()) && row.record.getType() == nsec.getType()) {
                row.record = nsec;
                return;
            }
        l.add(new Row(nsec, null, true, id++));
    }

    private boolean filterRow(Row row, ExcludeType exclude) {
        switch (exclude) {
            case EMPTY_NON_TERMINAL:
                return row.record.getType() != Type.NULL;
            case GENERATED:
                return !Signer.isGenerated(row.record.getType());
        }
        return true;
    }

    public List<Row> getSubzoneRecords(Name domain, Name name, ExcludeType exclude) {
        if (!rows.containsKey(domain))
            return new ArrayList<>();
        List<Row> l = rows.get(domain);
        return l.stream().filter(
                row -> row.record.getName().subdomain(name) && filterRow(row, exclude)).map(MockStorage::cloneRow)
                .collect(Collectors.toList());
    }

    public void removeAuthAndOrdernameForSubzone(Name domain, Name name) {
        if (!rows.containsKey(domain))
            return;
        rows.get(domain).stream().filter(row -> row.record.getName().subdomain(name) && (row.record.getType() != Type.NS && row.record.getType() != Type.DS))
                .forEach(row -> {
                    row.auth = false;
                    row.ordername = null;
                });
    }

    public void updateAuthAndOrdernameForName(Name domain, Name name, String ordername, boolean auth) {
        if (!rows.containsKey(domain))
            return;
        rows.get(domain).stream()
                .filter(row -> row.record.getName().equals(name) && row.record.getType() != Type.RRSIG &&
                        row.record.getType() != Type.NSEC && row.record.getType() != Type.NSEC3)
                .forEach(row -> {
                    row.auth = auth;
                    row.ordername = ordername;
                });
    }

    @Override
    public int subzoneHasOrdernames(Name domain, Name name) {
        if (!rows.containsKey(domain))
            return 0;
        List<Row> l = rows.get(domain);
        return (int) l.stream().filter(row -> row.record.getName().subdomain(name) && row.ordername != null).count();
    }

    @Override
    public boolean createDomain(Name domain) {
        if (rows.containsKey(domain))
            return false;
        else {
            rows.put(domain, new ArrayList<>());
            return true;
        }
    }

    @Override
    public void deleteDomain(Name domain) {
        rows.remove(domain);
    }

    @Override
    public void updateSerial(Name domain, long serial) {
        Row soa = getSOA(domain);
        SOARecord old = (SOARecord) soa.record;
        soa.record = new SOARecord(old.getName(), old.getDClass(), old.getTTL(), old.getHost(), old.getAdmin(),
                serial, old.getRefresh(), old.getRetry(), old.getExpire(), old.getMinimum());
    }

    @Override
    public void updateSOA(Name domain, long ttl, String rdata) {
        Row soa = getSOA(domain);
        try {
            soa.record = Record.fromString(domain, Type.SOA, DClass.IN, ttl, rdata, new Name("."));
        } catch (IOException e) {
            throw new RuntimeException(e);
        }
    }

    private Row getSOA(Name domain) {
        List<Row> soaList = rows.get(domain).stream()
                .filter(row -> row.record.getName().equals(domain) && row.record.getType() == Type.SOA)
                .collect(Collectors.toList());
        if (soaList.size() != 1)
            throw new RuntimeException("Missing or duplicate SOA for " + domain);
        return soaList.get(0);
    }

    @Override
    public void setNSECParameters(Name domain, NSECParameters np) {
        nsecParameters.put(domain, np);
    }

    @Override
    public void setSignParameters(Name domain, String parameters) {
    }

    @Override
    public String getSignParameters(Name domain) {
        return "";
    }

    @Override
    public void deleteRecords(Name domain) {
        if (!rows.containsKey(domain))
            return;
        rows.put(domain, new ArrayList<>());
    }

    @Override
    public boolean isSigned(Name domain) {
        return true;
    }

    @Override
    public void setSignStatus(Name domain, boolean status) {
    }

    @Override
    public void deleteDNSSECRecords(Name domain) {
    }

    @Override
    public NSECParameters getNSECParameters(Name domain) {
        return nsecParameters.get(domain);
    }

    private static class OrdernameComparator implements Comparator<Row> {
        public int compare(Row a, Row b) {
            return a.ordername.compareTo(b.ordername);
        }
    }
}
