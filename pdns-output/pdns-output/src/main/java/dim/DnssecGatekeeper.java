package dim;

import java.time.Instant;
import org.xbill.DNS.Name;

/**
 * Pre-publishing gatekeeper for DNSSEC signatures before synchronizing to PowerDNS.
 *
 * <p>Validates that signature validity intervals conform to RFC 6781 and prevents
 * future-dated, expired, or malformed RRSIG records from overwriting healthy authoritative
 * records in the PowerDNS database.</p>
 */
public class DnssecGatekeeper {

    /**
     * Validates signature parameters against current UTC system time before signing.
     *
     * <p>Enforces strict temporal constraints in Unix epoch seconds:
     * <ul>
     *   <li>Inception (valid_begin) must not be in the future (begin &lt;= now).</li>
     *   <li>Expiration (valid_end) must be strictly in the future (end &gt; now).</li>
     *   <li>Interval must be strictly ordered (begin &lt; end).</li>
     * </ul>
     * </p>
     *
     * @param domain the DNS zone name being evaluated
     * @param zsp the zone signing parameters containing begin and end timestamps
     * @throws SignatureRefusedException if parameters are null, future-dated, expired, or inverted
     */
    public static void validateParameters(Name domain, ZoneSignParameters zsp) throws SignatureRefusedException {
        // Defensive check: ensure parameters and timestamps are present
        if (zsp == null || zsp.begin == null || zsp.end == null) {
            throw new SignatureRefusedException(
                "ZoneSignParameters or timestamps (valid_begin/valid_end) are null for zone " + domain
            );
        }

        // readable output //
        Instant nowInstant = Instant.now();
        Instant beginInstant = zsp.begin.toInstant();
        Instant endInstant = zsp.end.toInstant();

        // unix timestamp //
        long now = nowInstant.getEpochSecond();
        long begin = beginInstant.getEpochSecond();
        long end = endInstant.getEpochSecond();

        // Check 1: Inception must never be in the future (INC-135591 prevention).
        // Per RFC 6781 section 4.4.1, a signature inception time in the future causes
        // immediate validation failure on validating resolvers until that inception time
        // arrives. Furthermore, validating resolvers cache SERVFAIL/BOGUS results, causing
        // prolonged outages. Because DIM backdates valid_begin by default by 1 hour (3600s),
        // any valid_begin > now indicates clock skew or misconfiguration and must be strictly
        // refused without leeway.
        if (begin > now) {
            throw new SignatureRefusedException(
                "Signature inception is in the future for zone " + domain +
                ": valid_begin=" + beginInstant + " > now=" + nowInstant + " (" + (begin - now) + "s in future)"
            );
        }

        // Check 2: Expiration must be strictly in the future
        if (end <= now) {
            throw new SignatureRefusedException(
                "Signature expiration is in the past for zone " + domain +
                ": valid_end=" + endInstant + " <= now=" + nowInstant + " (expired " + (now - end) + "s ago)"
            );
        }

        // Check 3: Validity interval must be plausible (begin < end)
        if (begin >= end) {
            throw new SignatureRefusedException(
                "Invalid validity window for zone " + domain +
                ": valid_begin=" + beginInstant + " >= valid_end=" + endInstant
            );
        }
    }
}
