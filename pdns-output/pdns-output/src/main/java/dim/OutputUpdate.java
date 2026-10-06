package dim;

import com.verisignlabs.dnssec.security.DnsKeyPair;
import org.jooq.DSLContext;
import org.jooq.Result;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.xbill.DNS.*;
import org.xbill.DNS.Record;
import org.xbill.DNS.utils.base16;

import java.util.ArrayList;
import java.util.Base64;
import java.util.Date;
import java.util.List;
import java.util.stream.Collectors;

import static dim.Util.name;

class Key {
    public final DnsKeyPair dnsKeyPair;
    public final String label;

    public Key(Name domain, String label, String type, int algorithm, String pubkey, String privkey, long ttl) {
        final int PROTOCOL = 3;
        DNSKEYRecord record = new DNSKEYRecord(domain, DClass.IN, ttl, type.equals("ksk") ? 257 : 256, PROTOCOL,
                algorithm, Base64.getDecoder().decode(pubkey));
        dnsKeyPair = new DnsKeyPair(record, privkey);
        this.label = label;
    }
}

class OutputUpdate {
    private static final Logger log = LoggerFactory.getLogger(OutputUpdate.class);
    private static final String CREATE_RR = "create_rr";
    private static final String DELETE_RR = "delete_rr";
    private static final String UPDATE_SOA = "update_soa";
    private static final String CREATE_ZONE = "create_zone";
    private static final String REFRESH_ZONE = "refresh_zone";
    private static final String DELETE_ZONE = "delete_zone";
    private static final String DNSSEC_UPDATE = "dnssec_update";
    // TODO delete these
    private static final String ENABLE_KEY = "enable_key";
    private static final String DISABLE_KEY = "disable_key";
    private static final String NSEC3PARAM = "nsec3param";

    final String transaction;
    final long output_id;
    private final long id;
    private final String action;
    private final String zone_name;
    private final long serial;
    private final String name;
    private final int ttl;
    private final String type;
    private final String content;

    OutputUpdate(long id, String transaction, String action, long output_id, String zone_name, long serial,
                 String name, int ttl, String type, String content) {
        this.id = id;
        this.transaction = transaction;
        this.action = action;
        this.output_id = output_id;
        this.zone_name = zone_name;
        this.serial = serial;
        this.name = name;
        this.ttl = ttl;
        this.type = type;
        this.content = content;
    }

    @Override
    public String toString() {
        return String.format("output=%d, tid=%s zone=%s, serial=%d: %s %s %d %s %s",
                output_id, transaction, zone_name, serial, action, name, ttl, type, content);
    }

    void applyUpdate(DSLContext dim, DSLContext pdns, long maxQuerySize) {
        try {
            log.info("Applying update {}", this);
            PdnsDatabase db = new PdnsDatabase(pdns, maxQuerySize);
            Name domain = name(zone_name);
            Signer signer = new Signer(db);
            switch (action) {
                case CREATE_ZONE:
                case REFRESH_ZONE: {
                    boolean created = db.createDomain(domain);
                    if (!created) {
                        if (action.equals(REFRESH_ZONE))
                            log.warn("Zone {} already exists, probably create_zone applied twice", zone_name);
                        else
                            throw new RuntimeException(String.format("Zone %s already exists in the PowerDNS " +
                                    "database", zone_name));
                    }
                    db.deleteRecords(domain);
                    Record soa = Record.fromString(domain, Type.SOA, DClass.IN, ttl, content, name(zone_name));
                    db.addRow(domain, new Row(soa));
                    break;
                }
                case DELETE_ZONE:
                    try {
                        db.deleteDomain(domain);
                    } catch (NonexistentDomainException e) {
                        // What is dead may never die
                    }
                    break;
                case CREATE_RR: {
                    Record r = Record.fromString(name(name), Type.value(type), DClass.IN, ttl, content, domain);
                    db.updateSerial(name(zone_name), serial);
                    ZoneSignParameters zsp = null;
                    if (db.isSigned(domain))
                        zsp = getZSP(dim, signer);
                    if (zsp != null) {
                        signer.incrementalAdd(domain, r, zsp);
                        signer.signRRSet(domain, domain, Type.SOA, zsp);
                    } else {
                        db.addRow(domain, new Row(r));
                    }
                    break;
                }
                case DELETE_RR: {
                    db.updateSerial(domain, serial);
                    ZoneSignParameters zsp = null;
                    if (db.isSigned(domain))
                        zsp = getZSP(dim, signer);
                    Record r = Record.fromString(name(name), Type.value(type), DClass.IN, ttl, content, domain);
                    if (zsp != null) {
                        signer.incrementalDelete(domain, r, zsp);
                        signer.signRRSet(domain, domain, Type.SOA, zsp);
                    } else
                        db.deleteRecord(domain, r);
                    break;
                }
                case UPDATE_SOA:
                    // TODO merge with updateserial
                    db.updateSOA(domain, ttl, content);
                    break;
                case ENABLE_KEY:
                case DISABLE_KEY:
                case NSEC3PARAM:
                case DNSSEC_UPDATE: {
                    List<Key> keys = getKeys(dim, signer);
                    if (keys.isEmpty() || keys.stream().allMatch(x -> isKSK(x.dnsKeyPair))) {
                        db.deleteDNSSECRecords(domain);
                        db.setSignStatus(domain, false);
                    } else {
                        ZoneSignParameters zsp = getZSP(dim, signer);
                        if (zsp != null)
                            signer.resignZone(domain, zsp);
                    }
                    break;
                }
            }
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }

    private List<Key> getKeys(DSLContext dim, Signer signer) {
        Name domain = name(zone_name);
        long soaMinimum = signer.getSoaMinimum(domain);
        Result<org.jooq.Record> results = dim.fetch("SELECT label, type, algorithm, pubkey, privkey FROM zonekey" +
                " JOIN zone WHERE zonekey.zone_id=zone.id AND zone.name=?", zone_name);
        ArrayList<Key> keys = new ArrayList<>();
        for (org.jooq.Record r : results) {
            String privateKeyString = r.get("privkey", String.class);
            String label = r.get("label", String.class);
            if (privateKeyString == null)
                throw new RuntimeException("Private key missing for label " + label);
            keys.add(new Key(domain, label, r.get("type", String.class), r.get("algorithm", Integer.class),
                    r.get("pubkey", String.class), privateKeyString, soaMinimum));
        }
        return keys;
    }

    private ZoneSignParameters getSigningParameters(DSLContext dim) {
        org.jooq.Record result = dim.fetchOne("SELECT nsec3_algorithm, nsec3_iterations, nsec3_salt," +
                " valid_begin, valid_end FROM zone WHERE zone.name=?", zone_name);
        if (result == null)
            return null;
        Integer algorithm = result.get(0, Integer.class);
        Date begin = result.get(3, Date.class);
        Date end = result.get(4, Date.class);
        NSECParameters nsecParameters;
        if (algorithm == null || algorithm == 0)
            nsecParameters = NSECParameters.NSEC();
        else {
            String salt = result.get(2, String.class);
            byte[] saltBytes = null;
            if (salt != null && !salt.equals("-"))
                saltBytes = base16.fromString(salt);
            nsecParameters = NSECParameters.NSEC3(result.get(1, Integer.class), saltBytes);
        }
        return new ZoneSignParameters(null, null, begin, end, nsecParameters);
    }

    private static boolean isKSK(DnsKeyPair dnsKeyPair) {
        return (dnsKeyPair.getDNSKEYRecord().getFlags() & DNSKEYRecord.Flags.SEP_KEY) != 0;
    }

    private ZoneSignParameters getZSP(DSLContext dim, Signer signer) {
        List<Key> keys = getKeys(dim, signer);
        if (keys.isEmpty())
            return null;
        List<DnsKeyPair> ksk = keys.stream().filter(k -> isKSK(k.dnsKeyPair)).map(k -> k.dnsKeyPair)
                .collect(Collectors.toList());
        List<DnsKeyPair> zsk = keys.stream().filter(k -> !isKSK(k.dnsKeyPair)).map(k -> k.dnsKeyPair)
                .collect(Collectors.toList());
        if (zsk.isEmpty())
            return null;
        ZoneSignParameters zsp = getSigningParameters(dim);
        if (zsp == null)
            return null;
        return zsp.withKeys(ksk, zsk);
    }
}
