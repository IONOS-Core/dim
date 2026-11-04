package dim;

/**
 * Exception thrown when a DNSSEC signature or its validity parameters are rejected
 * before publishing to PowerDNS.
 *
 * <p>Thrown by {@link DnssecGatekeeper} or {@link OutputUpdate} when keys, timestamps,
 * or signatures are missing, expired, future-dated, or malformed.</p>
 *
 * <p>This fail-safe exception prevents corrupted, expired, or future-dated RRSIG records
 * from overwriting healthy authoritative records in the nameserver database (RFC 6781).</p>
 */
public class SignatureRefusedException extends Exception {

    private static final long serialVersionUID = 1L;

    /**
     * Constructs a new SignatureRefusedException with the specified detail message.
     *
     * @param message human-readable description of the validation failure
     */
    public SignatureRefusedException(String message) {
        super(message);
    }

    /**
     * Constructs a new SignatureRefusedException with the specified detail message and root cause.
     *
     * @param message human-readable description of the validation failure
     * @param cause underlying exception that triggered the validation failure
     */
    public SignatureRefusedException(String message, Throwable cause) {
        super(message, cause);
    }
}
