package dim;

import org.junit.Test;
import org.xbill.DNS.Name;
import org.xbill.DNS.TextParseException;

import java.time.Instant;
import java.util.Date;

import static org.junit.Assert.*;

/**
 * Unit tests for {@link DnssecGatekeeper}.
 *
 * <p>Verifies that valid signature intervals pass smoothly, and that future-dated,
 * expired, inverted, or null timestamps are rejected with {@link SignatureRefusedException}.</p>
 */
public class DnssecGatekeeperTest {

    private final Name testDomain;

    public DnssecGatekeeperTest() throws TextParseException {
        this.testDomain = new Name("test.de.");
    }

    /**
     * Helper to construct ZoneSignParameters with specified begin and end timestamps.
     */
    private ZoneSignParameters makeZsp(Instant begin, Instant end) {
        Date beginDate = (begin != null) ? Date.from(begin) : null;
        Date endDate = (end != null) ? Date.from(end) : null;
        return new ZoneSignParameters(null, null, beginDate, endDate, null);
    }

    @Test
    public void testValidParametersPass() throws Exception {
        // ARRANGE: Healthy parameters (inception 1h in past, expiration 14d in future)
        Instant begin = Instant.now().minusSeconds(3600);
        Instant end = Instant.now().plusSeconds(86400 * 14);
        ZoneSignParameters zsp = makeZsp(begin, end);

        // ACT & ASSERT: Must pass cleanly without throwing any exception
        DnssecGatekeeper.validateParameters(testDomain, zsp);
    }

    @Test(expected = SignatureRefusedException.class)
    public void testFutureInceptionThrows() throws Exception {
        // ARRANGE: Poison pill (inception 1 hour in the FUTURE)
        Instant begin = Instant.now().plusSeconds(3600);
        Instant end = Instant.now().plusSeconds(86400 * 14);
        ZoneSignParameters zsp = makeZsp(begin, end);

        // ACT & ASSERT: Gatekeeper MUST refuse the future inception
        DnssecGatekeeper.validateParameters(testDomain, zsp);
    }

    @Test(expected = SignatureRefusedException.class)
    public void testExpiredSignatureThrows() throws Exception {
        // ARRANGE: Expired parameters (both inception and expiration in the past)
        Instant begin = Instant.now().minusSeconds(86400 * 14);
        Instant end = Instant.now().minusSeconds(3600);
        ZoneSignParameters zsp = makeZsp(begin, end);

        // ACT & ASSERT: Gatekeeper MUST refuse expired signatures
        DnssecGatekeeper.validateParameters(testDomain, zsp);
    }

    @Test(expected = SignatureRefusedException.class)
    public void testInvertedIntervalThrows() throws Exception {
        // ARRANGE: Malformed parameters where end is before begin
        Instant begin = Instant.now().minusSeconds(3600);
        Instant end = Instant.now().minusSeconds(7200);
        ZoneSignParameters zsp = makeZsp(begin, end);

        // ACT & ASSERT: Gatekeeper MUST refuse inverted intervals
        DnssecGatekeeper.validateParameters(testDomain, zsp);
    }

    @Test(expected = SignatureRefusedException.class)
    public void testNullParametersThrows() throws Exception {
        // ACT & ASSERT: Null zsp must be rejected defensively
        DnssecGatekeeper.validateParameters(testDomain, null);
    }

    @Test(expected = SignatureRefusedException.class)
    public void testNullBeginTimestampThrows() throws Exception {
        // ARRANGE: Missing begin timestamp
        ZoneSignParameters zsp = makeZsp(null, Instant.now().plusSeconds(86400));

        // ACT & ASSERT: Must refuse missing begin date
        DnssecGatekeeper.validateParameters(testDomain, zsp);
    }

    @Test(expected = SignatureRefusedException.class)
    public void testNullEndTimestampThrows() throws Exception {
        // ARRANGE: Missing end timestamp
        ZoneSignParameters zsp = makeZsp(Instant.now().minusSeconds(3600), null);

        // ACT & ASSERT: Must refuse missing end date
        DnssecGatekeeper.validateParameters(testDomain, zsp);
    }

    @Test
    public void testErrorMessageContainsHumanReadableUtcIsoTimestamps() {
        // ARRANGE: Inception in the future
        Instant begin = Instant.now().plusSeconds(3600);
        Instant end = Instant.now().plusSeconds(86400 * 14);
        ZoneSignParameters zsp = makeZsp(begin, end);

        try {
            // ACT
            DnssecGatekeeper.validateParameters(testDomain, zsp);
            fail("Expected SignatureRefusedException was not thrown");
        } catch (SignatureRefusedException e) {
            // ASSERT: Verify actionable error details for on-call sysadmins
            String msg = e.getMessage();
            assertTrue("Message should contain zone name", msg.contains("test.de."));
            assertTrue("Message should explain inception is in future", msg.contains("in the future"));
            assertTrue("Message should contain ISO-8601 UTC 'Z' indicator", msg.contains("Z"));
        }
    }
}
