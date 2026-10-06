package dim;

import com.verisignlabs.dnssec.security.DnsKeyPair;
import com.verisignlabs.dnssec.security.JCEDnsSecParallelSigner;
import com.verisignlabs.dnssec.security.JCEDnsSecSigner;
import com.verisignlabs.dnssec.security.SignUtils;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.bridge.SLF4JBridgeHandler;
import org.xbill.DNS.*;
import org.xbill.DNS.Record;
import org.xbill.DNS.utils.base32;

import java.io.IOException;
import java.security.GeneralSecurityException;
import java.security.NoSuchAlgorithmException;
import java.util.*;
import java.util.function.Consumer;
import java.util.function.Function;
import java.util.function.Predicate;
import java.util.function.Supplier;
import java.util.stream.Collectors;
import java.util.stream.IntStream;
import java.util.stream.Stream;

public class Signer {
    private final Storage storage;
    private static final base32 b32 = new base32(base32.Alphabet.BASE32HEX, false, false);

    static {
        SLF4JBridgeHandler.removeHandlersForRootLogger();
        SLF4JBridgeHandler.install();
    }

    private static final Logger log = LoggerFactory.getLogger(Signer.class);

    private static String byteArrayToBase32String(byte[] hash) {
        return b32.toString(hash);
    }

    private static byte[] base32StringToByteArray(String hash) throws IOException {
        Tokenizer st = new Tokenizer(hash);
        return st.getBase32String(b32);
    }

    Signer(Storage storage) {
        this.storage = storage;
    }

    private static String computeNSECOrdername(Name domain, Name name, ZoneSignParameters zsp) {
        if (zsp.nsecParameters.nsecMode == NSECParameters.NSECMode.NSEC3_MODE) {
            try {
                byte[] hash = SignUtils.nsec3hash(name, NSEC3Record.SHA1_DIGEST_ID, zsp.nsecParameters.iterations,
                        zsp.nsecParameters.salt);
                return byteArrayToBase32String(hash).toLowerCase();
            } catch (NoSuchAlgorithmException e) {
                // This would never happen, algorithm is hardcoded to the single valid value
                throw new RuntimeException(e);
            }
        }
        Name relative = name.relativize(domain);
        int n = relative.labels();
        Stream<String> parts = IntStream.range(0, n).boxed().map(i -> n - i - 1).map(relative::getLabelString);
        return String.join(" ", parts.collect(Collectors.toList()));
    }

    static boolean isGenerated(int type) {
        return type == Type.RRSIG || type == Type.NSEC || type == Type.NSEC3 || type == Type.NULL;
    }

    private static boolean shouldSign(Row row) {
        return (row.auth && !isGenerated(row.record.getType())) || row.record.getType() == Type.NS;
    }

    private boolean isDelegated(Name domain, Name name) {
        while (!name.equals(domain)) {
            if (storage.rrsetExists(domain, name, Type.NS))
                return true;
            name = new Name(name, 1);
        }
        return false;
    }

    private boolean delegatedNeedsOrdername(int type) {
        return type == Type.NS || type == Type.DS;
    }

    /**
     * Check each row in rows if it needs non-empty terminals. Return the list ENTs rrs that need to be created.
     * When creating non-empty terminals for a name, look no further than stopAt. (This is fine when a delegation got
     * deleted; all non-empty terminals below it should be already present.)
     */
    private HashSet<Name> computeEmptyNonTerminals(Name stopAt, List<Row> rows, ZoneSignParameters zsp) {
        HashSet<Name> ents = new HashSet<>();
        if (zsp.isNSEC3()) {
            // Compute the map: owner name -> at least one rr has ordername
            HashMap<Name, Boolean> names = new HashMap<>();
            for (Row row : rows) {
                Name name = row.record.getName();
                names.put(name, names.getOrDefault(name, false) || row.ordername != null);
            }
            Set<Name> keys = new HashSet<>(names.keySet());
            // Figure out the set of empty non-terminals and if they need ordername
            for (Name k : keys) {
                if (!names.get(k) || k.equals(stopAt))
                    continue;
                k = new Name(k, 1);
                while (!k.equals(stopAt)) {
                    if (!names.getOrDefault(k, false)) {
                        ents.add(k);
                        names.put(k, true);
                    }
                    k = new Name(k, 1);
                }
            }
        }
        return ents;
    }

    // TODO This doesn't consider subdelegations
    public void signZone(Name domain, List<Record> records, ZoneSignParameters zsp) throws
            IOException, GeneralSecurityException {
        records = new ArrayList<>(records);
        Set<Name> delegations = new HashSet<>();
        records.forEach(r -> {
            if (!r.getName().equals(domain) && r.getType() == Type.NS)
                delegations.add(r.getName());
        });
        Predicate<Name> isDelegated = r -> delegations.stream().anyMatch(r::subdomain);

        Timer signTimer = new Timer();
        JCEDnsSecParallelSigner signer = new JCEDnsSecParallelSigner(false);
        List<Record> signed;
        if (zsp.isNSEC3()) {
            signed = signer.signZoneNSEC3(domain, records, zsp.kskpairs, zsp.zskpairs, zsp.begin, zsp.end, false, false,
                    null, zsp.nsecParameters.salt, zsp.nsecParameters.iterations, NSEC3Record.SHA1_DIGEST_ID, -1);
        } else
            signed = signer.signZone(domain, records, zsp.kskpairs, zsp.zskpairs, zsp.begin, zsp.end, false,
                    DSRecord.SHA1_DIGEST_ID);
        List<Row> rows = signed.stream().map(r -> {
            boolean needOrdername = true;
            // TODO complete the invariant computation
            if (isGenerated(r.getType()) || (!delegatedNeedsOrdername(r.getType()) && isDelegated.test(r.getName())))
                needOrdername = false;
            return new Row(r, needOrdername ? computeNSECOrdername(domain, r.getName(), zsp) : null,
                    r.getType() == Type.RRSIG || r.getType() == Type.NSEC || !isDelegated.test(r.getName()) || (isDelegated.test(r.getName()) && r.getType() == Type.DS));
        }).collect(Collectors.toList());
        double signingTime = signTimer.reset();

        HashSet<Name> ents = computeEmptyNonTerminals(domain, rows, zsp);
        ZoneRecords zr = new ZoneRecords(rows);

        // Get SOA minimum
        long soaMinimum = 0;
        List<Row> soaRecords = zr.getRRSet(domain).stream().filter(row -> row.record.getType() == Type.SOA).collect(
                Collectors.toList());
        if (!soaRecords.isEmpty()) {
            SOARecord r = (SOARecord) soaRecords.get(0).record;
            soaMinimum = r.getMinimum();
        }

        for (Name ent : ents)
            makeEmptyNonTerminalInMemory(domain, ent, soaMinimum, zsp, zr);
        double entTime = signTimer.reset();

        storage.addRows(domain, zr.getRows());
        log.debug("Signing zone {} stats: signing {} s, creating {} ents {} s, db inserts {} s",
                domain, signingTime, ents.size(), entTime, signTimer.elapsed());
    }

    private ArrayList<Integer> getTypesForName(Name domain, Name name, ZoneSignParameters zsp) {
        List<Row> rows = storage.getRRSet(domain, name, 0);
        return getTypesForName(domain, rows, zsp);
    }

    private static ArrayList<Integer> getTypesForName(Name domain, List<Row> rows, ZoneSignParameters zsp) {
        if (rows.isEmpty())
            return new ArrayList<>();
        // For NSEC3 mode, add NSEC3PARAM for apex
        Stream<Integer> nsec3Stream = rows.get(0).record.getName().equals(domain) ?
                Stream.of(Type.NSEC3PARAM) : Stream.empty();
        // In NSEC mode, NSEC and RRSIG for NSEC rrs are always present
        Stream<Integer> addAlso = zsp.isNSEC3() ? nsec3Stream : Stream.of(zsp.nsecType(), Type.RRSIG);
        // Exclude NULL records
        return Stream.concat(rows.stream().filter(
                row -> (shouldSign(row) || row.record.getType() == Type.RRSIG))
                .map(row -> row.record.getType()), addAlso).distinct().collect(Collectors.toCollection(ArrayList::new));
    }

    private void removeRRSIGForNameAndType(Name domain, Name name, int type) {
        List<Row> rrset_rrsigs = storage.getRRSet(domain, name, Type.RRSIG);
        if (!rrset_rrsigs.isEmpty()) {
            for (Row row : rrset_rrsigs) {
                RRSIGRecord r = (RRSIGRecord) row.record;
                if (r.getTypeCovered() == type)
                    storage.deleteRowById(domain, row.id);
            }
        }
    }

    private List<RRSIGRecord> signRecord(Name domain, Record nsec, ZoneSignParameters zsp) throws IOException,
            GeneralSecurityException {
        JCEDnsSecSigner signer = new JCEDnsSecSigner(false);
        RRset rrset = new RRset();
        rrset.addRR(nsec);
        return signer.signRRset(rrset, zsp.zskpairs, zsp.begin, zsp.end);
    }

    private void addNSEC(Name domain, Record nsec, ZoneSignParameters zsp) throws IOException,
            GeneralSecurityException {
        List<RRSIGRecord> rrsigs = signRecord(domain, nsec, zsp);
        removeRRSIGForNameAndType(domain, nsec.getName(), zsp.nsecType());
        rrsigs.forEach(r -> storage.addRow(domain, new Row(r)));
        storage.addOrReplaceNSEC(domain, nsec);
    }

    private void createNSEC(Name domain, Name name, long soaMinimum, ZoneSignParameters zsp) throws IOException {
        createNSEC(domain, name, soaMinimum, zsp, null);
    }

    private static Record newNSECX(Name name, Name next, long ttl, ArrayList<Integer> types, ZoneSignParameters zsp)
            throws IOException {
        int[] typesArray = types.stream().mapToInt(Integer::intValue).toArray();
        if (zsp.isNSEC3()) {
            byte[] nextHash = base32StringToByteArray(next.toString());
            return new NSEC3Record(name, DClass.IN, ttl, NSEC3Record.SHA1_DIGEST_ID, 0, zsp.nsecParameters.iterations,
                    zsp.nsecParameters.salt, nextHash, typesArray);
        } else
            return new NSECRecord(name, DClass.IN, ttl, next, typesArray);
    }

    private void addNSECUnchecked(Name domain, Record nsec, ZoneSignParameters zsp) {
        try {
            addNSEC(domain, nsec, zsp);
        } catch (IOException e) {
            throw new RuntimeException(e);
        } catch (GeneralSecurityException e) {
            throw new RuntimeException(e);
        }
    }

    private void createNSEC(Name domain, Name name, long soaMinimum, ZoneSignParameters zsp, List<Row> rows)
            throws IOException {
        createNSECAlgorithm(domain, name, soaMinimum, zsp,
                ordername -> storage.getNextName(domain, ordername),
                () -> rows == null ? getTypesForName(domain, name, zsp) : getTypesForName(domain, rows, zsp),
                nsec -> addNSECUnchecked(domain, nsec, zsp));
    }

    private void createNSECAlgorithm(Name domain, Name name, long soaMinimum, ZoneSignParameters zsp,
                                     Function<String, Name> getNextName,
                                     Supplier<ArrayList<Integer>> getTypesForNSEC,
                                     Consumer<Record> addNSEC)
            throws IOException {
        String ordername = computeNSECOrdername(domain, name, zsp);
        Name nextName = getNextName.apply(ordername);
        if (nextName == null)
            nextName = name;
        ArrayList<Integer> types = getTypesForNSEC.get();
        if (zsp.isNSEC3()) {
            String hash = computeNSECOrdername(domain, name, zsp);
            name = new Name(hash, domain);
        }
        if (zsp.isNSEC3())
            nextName = new Name(computeNSECOrdername(domain, nextName, zsp));
        Record nsec = newNSECX(name, nextName, soaMinimum, types, zsp);
        addNSEC.accept(nsec);
    }


    private void createPrevNSEC(Name domain, Name name, Name next, long soaMinimum, ZoneSignParameters zsp) throws
            IOException {
        createPrevNSECAlgorithm(domain, name, next, soaMinimum, zsp,
                ordername -> storage.getPrevName(domain, ordername),
                prevName -> getTypesForName(domain, prevName, zsp),
                nsec -> addNSECUnchecked(domain, nsec, zsp));
    }

    private void createPrevNSECAlgorithm(Name domain, Name name, Name next, long soaMinimum, ZoneSignParameters zsp,
                                         Function<String, Name> getPrevName,
                                         Function<Name, ArrayList<Integer>> getTypesForNSEC,
                                         Consumer<Record> addNSEC)
            throws IOException {
        String ordername = computeNSECOrdername(domain, name, zsp);
        Name prevName = getPrevName.apply(ordername);
        if (prevName == null)
            return;
        ArrayList<Integer> types = getTypesForNSEC.apply(prevName);
        if (zsp.isNSEC3()) {
            String hash = computeNSECOrdername(domain, prevName, zsp);
            prevName = new Name(hash, domain);
        }
        if (zsp.isNSEC3())
            next = new Name(computeNSECOrdername(domain, next, zsp));
        Record nsecPrev = newNSECX(prevName, next, soaMinimum, types, zsp);
        addNSEC.accept(nsecPrev);
    }

    // Add or rewrite NSEC and its RRSIGS for this name and the previous one
    private void refreshNSECForRecord(Name domain, Name name, long soaMinimum, ZoneSignParameters zsp) throws
            IOException {
        createNSEC(domain, name, soaMinimum, zsp);

        // Add & sign prev NSEC
        createPrevNSEC(domain, name, name, soaMinimum, zsp);
    }

    /**
     * If RRSIGs already exist for this rrset, delete them.
     * Sign rrset defined by (record.name, record.type) if it exists.
     */
    public void signRRSet(Name domain, Name name, int type, ZoneSignParameters zsp) throws IOException,
            GeneralSecurityException {
        // Build rrset to be signed only with auth=1 records or NS
        List<Row> rows = storage.getRRSet(domain, name, type);
        signRRSet(domain, name, type, zsp, rows);
    }

    private void signRRSet(Name domain, Name name, int type, ZoneSignParameters zsp, List<Row> rows) throws IOException,
            GeneralSecurityException {
        RRset rrset = new RRset();
        rows.stream().filter(Signer::shouldSign).forEach(r -> rrset.addRR(r.record));

        // Delete old RRSIGs for rrset
        removeRRSIGForNameAndType(domain, name, type);

        if (rrset.size() > 0) {
            JCEDnsSecSigner signer = new JCEDnsSecSigner(false);
            List<RRSIGRecord> rrsigs = signer.signRRset(rrset, zsp.zskpairs, zsp.begin, zsp.end);
            // Add RRSIG for rrset
            rrsigs.forEach(r -> storage.addRow(domain, new Row(r)));
        }
    }

    public long getSoaMinimum(Name domain) {
        List<Row> soaRows = storage.getRRSet(domain, domain, Type.SOA);
        return soaRows.isEmpty() ? 0 : ((SOARecord) (soaRows.get(0).record)).getMinimum();
    }

    // Given a row, use the ordername to delete the NSEC3 and RRSIG for the nsec3 hashed record
    private void deleteNSEC3(Name domain, Name name, long soaMinimum, ZoneSignParameters zsp) throws IOException {
        String ordername = computeNSECOrdername(domain, name, zsp);
        Name hashName = new Name(ordername, domain);
        // Delete nsec3 record and its rrsig
        storage.deleteRRSet(domain, hashName, Type.NSEC3);
        storage.deleteRRSet(domain, hashName, Type.RRSIG);

        Name next = storage.getNextName(domain, ordername);
        createPrevNSEC(domain, name, next, soaMinimum, zsp);
    }

    private void makeEmptyNonTerminal(Name domain, Name name, long soaMinimum, ZoneSignParameters zsp) throws
            IOException {
        Row ent = new Row(new NULLRecord(name, DClass.IN, 0, new byte[0]),
                computeNSECOrdername(domain, name, zsp), true);
        storage.addRow(domain, ent);
        refreshNSECForRecord(domain, name, soaMinimum, zsp);
    }

    private void addNSECInMemory(Name domain, Record nsec, ZoneSignParameters zsp, ZoneRecords zr) {
        List<RRSIGRecord> rrsigs;
        try {
            rrsigs = signRecord(domain, nsec, zsp);
        } catch (IOException e) {
            throw new RuntimeException(e);
        } catch (GeneralSecurityException e) {
            throw new RuntimeException(e);
        }

        // Remove RRSIG for NSEC
        ArrayList<Row> toDelete = new ArrayList<>();
        for (Row row : zr.getRRSet(nsec.getName())) {
            if (row.record.getName().equals(nsec.getName()) && row.record.getType() == Type.RRSIG) {
                RRSIGRecord r = (RRSIGRecord) row.record;
                if (r.getTypeCovered() == zsp.nsecType())
                    toDelete.add(row);
            }
        }
        for (Row row : toDelete)
            zr.deleteRow(row);

        // Add signatures for NSEC
        rrsigs.forEach(r -> zr.addRow(new Row(r)));
    }

    // No trips to the database: update zr
    private void makeEmptyNonTerminalInMemory(Name domain, Name name, long soaMinimum, ZoneSignParameters zsp,
                                              ZoneRecords zr) throws IOException {
        Row ent = new Row(new NULLRecord(name, DClass.IN, 0, new byte[0]),
                computeNSECOrdername(domain, name, zsp), true);
        zr.addRow(ent);

        Function<Name, ArrayList<Integer>> getTypesForNSEC = forName -> getTypesForName(domain, zr.getRRSet(forName),
                zsp);

        // Add or rewrite NSEC and its RRSIGS for this name
        createNSECAlgorithm(domain, name, soaMinimum, zsp,
                ordername -> zr.getNextName(ordername),
                () -> getTypesForNSEC.apply(name),
                nsec -> addNSECInMemory(domain, nsec, zsp, zr));

        // Add & sign prev NSEC
        createPrevNSECAlgorithm(domain, name, name, soaMinimum, zsp,
                ordername -> zr.getPrevName(ordername),
                prevName -> getTypesForNSEC.apply(prevName),
                nsec -> addNSECInMemory(domain, nsec, zsp, zr));
    }

    public void incrementalAdd(Name domain, Record record, ZoneSignParameters zsp) throws IOException,
            GeneralSecurityException {
        // Adding a new key triggers re-sign
        if (record.getType() == Type.DNSKEY && record.getName().equals(domain)) {
            storage.addRow(domain, new Row(record, null, false));
            resignZone(domain, zsp);
            return;
        }
        long soaMinimum = getSoaMinimum(domain);
        boolean isDelegated = isDelegated(domain, record.getName());
        String ordername = null;
        if (!isDelegated || delegatedNeedsOrdername(record.getType()))
            ordername = computeNSECOrdername(domain, record.getName(), zsp);
        boolean nonApexNS = record.getType() == Type.NS && !record.getName().equals(domain);
        boolean auth = !((isDelegated && record.getType() != Type.DS) || nonApexNS);

        boolean createsDelegation = false;
        if (nonApexNS)
            createsDelegation = !storage.rrsetExists(domain, record.getName(), Type.NS);
        // Check if in the record's subzone existed an ordername
        // (used later for creating non-empty terminals)
        boolean subzoneHadOrdername = true;
        if (zsp.isNSEC3())
            subzoneHadOrdername = storage.subzoneHasOrdernames(domain, record.getName()) > 0;
        // Try to delete empty non-terminal if it existed
        storage.deleteRRSet(domain, record.getName(), Type.NULL);
        // Add rr to storage
        if (!createsDelegation)
            storage.addRow(domain, new Row(record, ordername, auth));

        // Delay signing of new delegation NS until we clean up the RRSIGS for new delegation records
        if (!createsDelegation && auth)
            signRRSet(domain, record.getName(), record.getType(), zsp);

        // Create a new delegation
        if (createsDelegation) {
            List<Row> subzoneRows = storage.getSubzoneRecords(domain, record.getName());
            // Remove auth & ordername for subzone
            storage.removeAuthAndOrdernameForSubzone(domain, record.getName());
            // Add record after we wiped ordername for the subzone
            storage.addRow(domain, new Row(record, ordername, auth));
            if (zsp.isNSEC3()) {
                // Delete NSEC3 records for the new delegation
                for (Row row : subzoneRows)
                    if (row.record.getType() != Type.NS && row.ordername != null)
                        deleteNSEC3(domain, row.record.getName(), soaMinimum, zsp);
            }
            // Delete all signatures except for those of DS records
            subzoneRows.stream().filter(row -> (row.record.getType() == Type.RRSIG && row.record.getRRsetType() != Type.value("DS")) || row.record.getType() == Type.NSEC)
                    .forEach(row -> storage.deleteRowById(domain, row.id));
            for (Row row : subzoneRows)
                if (row.record.getType() == Type.NS)
                    refreshNSECForRecord(domain, row.record.getName(), soaMinimum, zsp);
                else if (row.record.getType() == Type.NULL)
                    // Delete non-empty terminals for delegation
                    storage.deleteRowById(domain, row.id);
            refreshNSECForRecord(domain, record.getName(), soaMinimum, zsp);
        } else if (auth)
            refreshNSECForRecord(domain, record.getName(), soaMinimum, zsp);


        // Create empty non-terminals
        if (zsp.isNSEC3() && ordername != null && !subzoneHadOrdername) {
            Name name = new Name(record.getName(), 1);
            while (!domain.equals(name)) {
                if (storage.rrsetExists(domain, name))
                    break;
                makeEmptyNonTerminal(domain, name, soaMinimum, zsp);
                name = new Name(name, 1);
            }
        }
    }

    public void incrementalDelete(Name domain, Record record, ZoneSignParameters zsp) throws IOException,
            GeneralSecurityException {
        long soaMinimum = getSoaMinimum(domain);
        List<Row> rows = storage.getRRSet(domain, record.getName(), record.getType());
        boolean hasOrdername = false;
        boolean wasAuth = false;
        for (Iterator<Row> iterator = rows.iterator(); iterator.hasNext(); ) {
            Row row = iterator.next();
            if (row.record.rdataToString().equals(record.rdataToString())) {
                hasOrdername = row.ordername != null;
                wasAuth = row.auth;
                iterator.remove();
                break;
            }
        }
        // Delete rr from storage
        int deleted = storage.deleteRecord(domain, record);
        if (deleted == 0)
            // Tried to delete a rr that doesn't exist
            return;
        if (record.getType() == Type.DNSKEY && record.getName().equals(domain)) {
            resignZone(domain, zsp);
            return;
        }

        // Re-sign rrset or delete RRSIG only when it was auth
        if (wasAuth)
            signRRSet(domain, record.getName(), record.getType(), zsp, rows);

        HashSet<Name> ents = new HashSet<>();
        // Set auth & ordername for subzone rrs if the last NS delegation was deleted
        if (record.getType() == Type.NS && !record.getName().equals(domain) &&
                !storage.rrsetExists(domain, record.getName(), Type.NS)) {
            List<Row> subzoneRows = storage.getSubzoneRecords(domain, record.getName(), ExcludeType.EMPTY_NON_TERMINAL);
            // Figure out owner names that are not delegated anymore
            Set<Name> notDelegated = subzoneRows.stream().map(row -> row.record.getName()).distinct().filter(
                    name -> !isDelegated(domain, name)).collect(Collectors.toSet());
            // Update auth & ordername for them
            notDelegated.forEach(name ->
                    storage.updateAuthAndOrdernameForName(domain, name, computeNSECOrdername(domain, name, zsp), true));
            // Sign each of them
            for (Row row : subzoneRows)
                if (notDelegated.contains(row.record.getName()) && !isGenerated(row.record.getType())) {
                    signRRSet(domain, row.record.getName(), row.record.getType(), zsp);
                    refreshNSECForRecord(domain, row.record.getName(), soaMinimum, zsp);
                    // Update auth & ordername in memory as this is needed by computeEmptyNonTerminals()
                    row.ordername = computeNSECOrdername(domain, row.record.getName(), zsp);
                    row.auth = true;
                }

            Name stopAt = new Name(record.getName(), 1);
            ents.addAll(computeEmptyNonTerminals(stopAt, subzoneRows, zsp));
        }

        // Re-create NSEC or delete; if delete, re-create prev NSEC
        rows = storage.getRRSet(domain, record.getName(), 0);
        if (rows.stream().filter(Signer::shouldSign).count() > 0) {
            createNSEC(domain, record.getName(), soaMinimum, zsp, rows);
        } else {
            if (!zsp.isNSEC3())
                storage.deleteRRSet(domain, record.getName(), Type.NSEC);
            storage.deleteRRSet(domain, record.getName(), Type.RRSIG);
            if (zsp.isNSEC3())
                deleteNSEC3(domain, record.getName(), soaMinimum, zsp);
            else {
                // create prev NSEC
                Name next = storage.getNextName(domain, computeNSECOrdername(domain, record.getName(), zsp));
                if (next != null)
                    createPrevNSEC(domain, record.getName(), next, soaMinimum, zsp);
            }

            // Check if this should be a ENT now
            if (zsp.isNSEC3() && storage.subzoneHasOrdernames(domain, record.getName()) > 0)
                ents.add(record.getName());
        }

        // Check if deletion of this should trigger deletion of empty non-terminals
        if (zsp.isNSEC3() && hasOrdername) {
            Name name = new Name(record.getName(), 1);
            while (!domain.equals(name) && storage.rrsetExists(domain, name, Type.NULL) &&
                    storage.subzoneHasOrdernames(domain, name) < 2) {
                storage.deleteRRSet(domain, name, Type.NULL);
                deleteNSEC3(domain, name, soaMinimum, zsp);
                name = new Name(name, 1);
            }
        }

        // Create ENTs
        for (Name ent : ents)
            makeEmptyNonTerminal(domain, ent, soaMinimum, zsp);
    }

    public void resignZone(Name domain, ZoneSignParameters zsp) throws IOException, GeneralSecurityException {
        String lastSignParameters = storage.getSignParameters(domain);
        if (lastSignParameters != null && zsp.toString().compareTo(lastSignParameters) == 0)
            return;
        List<Row> rows = storage.getSubzoneRecords(domain, domain, ExcludeType.GENERATED);
        storage.deleteRecords(domain);
        List<Record> records = rows.stream().map(row -> row.record).collect(Collectors.toList());
        for (DnsKeyPair kp : zsp.zskpairs)
            records.add(kp.getDNSKEYRecord());
        for (DnsKeyPair kp : zsp.kskpairs)
            records.add(kp.getDNSKEYRecord());
        signZone(domain, records, zsp);
        storage.setSignParameters(domain, zsp.toString());
        storage.setNSECParameters(domain, zsp.nsecParameters);
    }
}

class ZoneRecords {
    Map<Name, List<Row>> rows;
    TreeMap<String, Name> ordernames;

    ZoneRecords(List<Row> rowList) {
        rows = rowList.stream().collect(Collectors.groupingBy(row -> row.record.getName()));
        ordernames = new TreeMap<>();
        for (List<Row> rl : rows.values())
            for (Row row : rl)
                if (row.ordername != null)
                    ordernames.put(row.ordername, row.record.getName());
    }

    Name getNextName(String ordername) {
        Map.Entry<String, Name> next = ordernames.higherEntry(ordername);
        if (next == null) {
            next = ordernames.firstEntry();
            if (next == null)
                return null;
        }
        return next.getValue();
    }

    Name getPrevName(String ordername) {
        Map.Entry<String, Name> next = ordernames.lowerEntry(ordername);
        if (next == null) {
            next = ordernames.lastEntry();
            if (next == null)
                return null;
        }
        return next.getValue();
    }

    void addRow(Row row) {
        Name name = row.record.getName();
        if (!rows.containsKey(name))
            rows.put(name, new ArrayList<>());
        rows.get(name).add(row);
        if (row.ordername != null)
            ordernames.put(row.ordername, row.record.getName());
    }

    void deleteRow(Row row) {
        List<Row> l = rows.get(row.record.getName());
        if (l == null)
            return;
        l.remove(row);
        if (row.ordername == null)
            return;
        for (Row r : l)
            if (r.ordername != null)
                return;
        ordernames.remove(row.ordername);
    }

    List<Row> getRRSet(Name name) {
        if (!rows.containsKey(name))
            return new ArrayList<>();
        return rows.get(name);
    }

    List<Row> getRows() {
        return rows.values().stream().flatMap(List::stream).collect(Collectors.toList());
    }
}
