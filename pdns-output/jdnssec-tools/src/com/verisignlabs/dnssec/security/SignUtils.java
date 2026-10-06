// $Id$
//
// Copyright (C) 2001-2003 VeriSign, Inc.
//
// This library is free software; you can redistribute it and/or
// modify it under the terms of the GNU Lesser General Public
// License as published by the Free Software Foundation; either
// version 2.1 of the License, or (at your option) any later version.
//
// This library is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
// Lesser General Public License for more details.
//
// You should have received a copy of the GNU Lesser General Public
// License along with this library; if not, write to the Free Software
// Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA 02111-1307
// USA

package com.verisignlabs.dnssec.security;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SignatureException;
import java.security.interfaces.DSAParams;
import java.util.*;
import java.util.logging.Logger;

import org.xbill.DNS.*;
import org.xbill.DNS.Record;
import org.xbill.DNS.utils.base64;

/**
 * This class contains a bunch of utility methods that are generally useful in
 * signing zones.
 * 
 * @author David Blacka (original)
 * @author $Author$
 * @version $Revision$
 */

public class SignUtils
{
  private static final int ASN1_INT      = 0x02;
  private static final int ASN1_SEQ      = 0x30;

  public static final int  RR_NORMAL     = 0;
  public static final int  RR_DELEGATION = 1;
  public static final int  RR_GLUE       = 2;
  public static final int  RR_INVALID    = 3;
  public static final int  RR_DNAME      = 4;

  private static Logger    log;

  static
  {
    log = Logger.getLogger(SignUtils.class.toString());
  }

  public static void setLog(Logger v)
  {
    log = v;
  }

  /**
   * Generate from some basic information a prototype RRSIG RR containing
   * everything but the actual signature itself.
   * 
   * @param rrset
   *          the RRset being signed.
   * @param key
   *          the public DNSKEY RR counterpart to the key being used to sign the
   *          RRset
   * @param start
   *          the RRSIG inception time.
   * @param expire
   *          the RRSIG expiration time.
   * @param sig_ttl
   *          the TTL of the resulting RRSIG record.
   * 
   * @return a prototype signature based on the RRset and key information.
   */
  public static RRSIGRecord generatePreRRSIG(RRset rrset, DNSKEYRecord key, Date start,
                                             Date expire, long sig_ttl)
  {
    return new RRSIGRecord(rrset.getName(), rrset.getDClass(), sig_ttl, rrset.getType(),
                           key.getAlgorithm(), (int) rrset.getTTL(), expire, start,
                           key.getFootprint(), key.getName(), null);
  }

  /**
   * Generate from some basic information a prototype RRSIG RR containing
   * everything but the actual signature itself.
   * 
   * @param rec
   *          the DNS record being signed (forming an entire RRset).
   * @param key
   *          the public DNSKEY RR counterpart to the key signing the record.
   * @param start
   *          the RRSIG inception time.
   * @param expire
   *          the RRSIG expiration time.
   * @param sig_ttl
   *          the TTL of the result RRSIG record.
   * 
   * @return a prototype signature based on the Record and key information.
   */
  public static RRSIGRecord generatePreRRSIG(Record rec, DNSKEYRecord key, Date start,
                                             Date expire, long sig_ttl)
  {
    return new RRSIGRecord(rec.getName(), rec.getDClass(), sig_ttl, rec.getType(),
                           key.getAlgorithm(), rec.getTTL(), expire, start,
                           key.getFootprint(), key.getName(), null);
  }

  /**
   * Generate the binary image of the prototype RRSIG RR.
   * 
   * @param presig
   *          the RRSIG RR prototype.
   * @return the RDATA portion of the prototype RRSIG record. This forms the
   *         first part of the data to be signed.
   */
  private static byte[] generatePreSigRdata(RRSIGRecord presig)
  {
    // Generate the binary image;
    DNSOutput image = new DNSOutput();

    // precalc some things
    int start_time = (int) (presig.getTimeSigned().getTime() / 1000);
    int expire_time = (int) (presig.getExpire().getTime() / 1000);
    Name signer = presig.getSigner();

    // first write out the partial SIG record (this is the SIG RDATA
    // minus the actual signature.
    image.writeU16(presig.getTypeCovered());
    image.writeU8(presig.getAlgorithm());
    image.writeU8(presig.getLabels());
    image.writeU32((int) presig.getOrigTTL());
    image.writeU32(expire_time);
    image.writeU32(start_time);
    image.writeU16(presig.getFootprint());
    image.writeByteArray(signer.toWireCanonical());

    return image.toByteArray();
  }

  /**
   * Calculate the canonical wire line format of the RRset.
   * 
   * @param rrset
   *          the RRset to convert.
   * @param ttl
   *          the TTL to use when canonicalizing -- this is generally the
   *          TTL of the signature if there is a pre-existing signature. If
   *          not it is just the ttl of the rrset itself.
   * @param labels
   *          the labels field of the signature, or 0.
   * @return the canonical wire line format of the rrset. This is the second
   *         part of data to be signed.
   */
  @SuppressWarnings("unchecked")
  public static byte[] generateCanonicalRRsetData(RRset rrset, long ttl, int labels)
  {
    DNSOutput image = new DNSOutput();

    if (ttl == 0) ttl = rrset.getTTL();
    Name n = rrset.getName();
    if (labels == 0)
    {
      labels = n.labels();
    }
    else
    {
      // correct for Name()'s conception of label count.
      labels++;
    }
    boolean wildcardName = false;
    if (n.labels() != labels)
    {
      n = n.wild(n.labels() - labels);
      wildcardName = true;
      log.fine("Detected wildcard expansion: " + rrset.getName() + " changed to " + n);
    }

    // now convert the wire format records in the RRset into a
    // list of byte arrays.
    ArrayList<byte[]> canonical_rrs = new ArrayList<byte[]>();
    for (Iterator<Record> i = rrset.rrs(); i.hasNext();)
    {
      Record r = i.next();
      if (r.getTTL() != ttl || wildcardName)
      {
        // If necessary, we need to create a new record with a new ttl
        // or ownername.
        // In the TTL case, this avoids changing the ttl in the
        // response.
        r = Record.newRecord(n, r.getType(), r.getDClass(), ttl, r.rdataToWireCanonical());
      }
      byte[] wire_fmt = r.toWireCanonical();
      canonical_rrs.add(wire_fmt);
    }

    // put the records into the correct ordering.
    // Calculate the offset where the RDATA begins (we have to skip
    // past the length byte)

    int offset = rrset.getName().toWireCanonical().length + 10;
    ByteArrayComparator bac = new ByteArrayComparator(offset, false);

    Collections.sort(canonical_rrs, bac);

    for (byte[] wire_fmt_rec : canonical_rrs)
    {
      image.writeByteArray(wire_fmt_rec);
    }

    return image.toByteArray();
  }

  /**
   * Given an RRset and the prototype signature, generate the canonical data
   * that is to be signed.
   * 
   * @param rrset
   *          the RRset to be signed.
   * @param presig
   *          a prototype SIG RR created using the same RRset.
   * @return a block of data ready to be signed.
   */
  public static byte[] generateSigData(RRset rrset, RRSIGRecord presig)
      throws IOException
  {
    byte[] rrset_data = generateCanonicalRRsetData(rrset, presig.getOrigTTL(),
                                                   presig.getLabels());

    return generateSigData(rrset_data, presig);
  }

  /**
   * Given an RRset and the prototype signature, generate the canonical data
   * that is to be signed.
   * 
   * @param rrset_data
   *          the RRset converted into canonical wire line format (as per the
   *          canonicalization rules in RFC 2535).
   * @param presig
   *          the prototype signature based on the same RRset represented in
   *          <code>rrset_data</code>.
   * @return a block of data ready to be signed.
   */
  public static byte[] generateSigData(byte[] rrset_data, RRSIGRecord presig)
      throws IOException
  {
    byte[] sig_rdata = generatePreSigRdata(presig);

    ByteArrayOutputStream image = new ByteArrayOutputStream(sig_rdata.length
        + rrset_data.length);

    image.write(sig_rdata);
    image.write(rrset_data);

    return image.toByteArray();
  }

  /**
   * Given the actual signature and the prototype signature, combine them and
   * return the fully formed RRSIGRecord.
   * 
   * @param signature
   *          the cryptographic signature, in DNSSEC format.
   * @param presig
   *          the prototype RRSIG RR to add the signature to.
   * @return the fully formed RRSIG RR.
   */
  public static RRSIGRecord generateRRSIG(byte[] signature, RRSIGRecord presig)
  {
    return new RRSIGRecord(presig.getName(), presig.getDClass(), presig.getTTL(),
                           presig.getTypeCovered(), presig.getAlgorithm(),
                           presig.getOrigTTL(), presig.getExpire(),
                           presig.getTimeSigned(), presig.getFootprint(),
                           presig.getSigner(), signature);
  }

  /**
   * Converts from a RFC 2536 formatted DSA signature to a JCE (ASN.1) formatted
   * signature.
   * 
   * <p>
   * ASN.1 format = ASN1_SEQ . seq_length . ASN1_INT . Rlength . R . ANS1_INT .
   * Slength . S
   * </p>
   * 
   * The integers R and S may have a leading null byte to force the integer
   * positive.
   * 
   * @param signature
   *          the RFC 2536 formatted DSA signature.
   * @return The ASN.1 formatted DSA signature.
   * @throws SignatureException
   *           if there was something wrong with the RFC 2536 formatted
   *           signature.
   */
  public static byte[] convertDSASignature(byte[] signature) throws SignatureException
  {
    if (signature.length != 41)
      throw new SignatureException("RFC 2536 signature not expected length.");

    byte r_pad = 0;
    byte s_pad = 0;

    // handle initial null byte padding.
    if (signature[1] < 0) r_pad++;
    if (signature[21] < 0) s_pad++;

    // ASN.1 length = R length + S length + (2 + 2 + 2), where each 2
    // is for a ASN.1 type-length byte pair of which there are three
    // (SEQ, INT, INT).
    byte sig_length = (byte) (40 + r_pad + s_pad + 6);

    byte sig[] = new byte[sig_length];
    byte pos = 0;

    sig[pos++] = ASN1_SEQ;
    sig[pos++] = (byte) (sig_length - 2); // all but the SEQ type+length.
    sig[pos++] = ASN1_INT;
    sig[pos++] = (byte) (20 + r_pad);

    // copy the value of R, leaving a null byte if necessary
    if (r_pad == 1) sig[pos++] = 0;

    System.arraycopy(signature, 1, sig, pos, 20);
    pos += 20;

    sig[pos++] = ASN1_INT;
    sig[pos++] = (byte) (20 + s_pad);

    // copy the value of S, leaving a null byte if necessary
    if (s_pad == 1) sig[pos++] = 0;

    System.arraycopy(signature, 21, sig, pos, 20);

    return sig;
  }

  /**
   * Converts from a JCE (ASN.1) formatted DSA signature to a RFC 2536 compliant
   * signature.
   * 
   * <p>
   * rfc2536 format = T . R . S
   * </p>
   * 
   * where T is a number between 0 and 8, which is based on the DSA key length,
   * and R & S are formatted to be exactly 20 bytes each (no leading null
   * bytes).
   * 
   * @param params
   *          the DSA parameters associated with the DSA key used to generate
   *          the signature.
   * @param signature
   *          the ASN.1 formatted DSA signature.
   * @return a RFC 2536 formatted DSA signature.
   * @throws SignatureException
   *           if something is wrong with the ASN.1 format.
   */
  public static byte[] convertDSASignature(DSAParams params, byte[] signature)
      throws SignatureException
  {
    if (signature[0] != ASN1_SEQ || signature[2] != ASN1_INT)
    {
      throw new SignatureException("Invalid ASN.1 signature format: expected SEQ, INT");
    }

    byte r_pad = (byte) (signature[3] - 20);

    if (signature[24 + r_pad] != ASN1_INT)
    {
      throw new SignatureException(
                                   "Invalid ASN.1 signature format: expected SEQ, INT, INT");
    }

    log.finer("(start) ASN.1 DSA Sig:\n" + base64.toString(signature));

    byte s_pad = (byte) (signature[25 + r_pad] - 20);

    byte[] sig = new byte[41]; // all rfc2536 signatures are 41 bytes.

    // Calculate T:
    sig[0] = (byte) ((params.getP().bitLength() - 512) / 64);

    // copy R value
    if (r_pad >= 0)
    {
      System.arraycopy(signature, 4 + r_pad, sig, 1, 20);
    }
    else
    {
      // R is shorter than 20 bytes, so right justify the number
      // (r_pad is negative here, remember?).
      Arrays.fill(sig, 1, 1 - r_pad, (byte) 0);
      System.arraycopy(signature, 4, sig, 1 - r_pad, 20 + r_pad);
    }

    // copy S value
    if (s_pad >= 0)
    {
      System.arraycopy(signature, 26 + r_pad + s_pad, sig, 21, 20);
    }
    else
    {
      // S is shorter than 20 bytes, so right justify the number
      // (s_pad is negative here).
      Arrays.fill(sig, 21, 21 - s_pad, (byte) 0);
      System.arraycopy(signature, 26 + r_pad, sig, 21 - s_pad, 20 + s_pad);
    }

    if (r_pad < 0 || s_pad < 0)
    {
      log.finer("(finish ***) RFC 2536 DSA Sig:\n" + base64.toString(sig));

    }
    else
    {
      log.finer("(finish) RFC 2536 DSA Sig:\n" + base64.toString(sig));
    }

    return sig;
  }

  // Given one of the ECDSA algorithms determine the "length", which is the
  // length, in bytes, of both 'r' and 's' in the ECDSA signature.
  private static int ecdsaLength(int algorithm) throws SignatureException
  {
    switch (algorithm)
    {
      case DNSSEC.Algorithm.ECDSAP256SHA256: return 32;
      case DNSSEC.Algorithm.ECDSAP384SHA384: return 48;
      default:
        throw new SignatureException("Algorithm " + algorithm + 
                                     " is not a supported ECDSA signature algorithm.");
    }
  }

  /**
   * Convert a JCE standard ECDSA signature (which is a ASN.1 encoding) into a
   * standard DNS signature.
   * 
   * The format of the ASN.1 signature is
   * 
   * ASN1_SEQ . seq_length . ASN1_INT . r_length . R . ANS1_INT . s_length . S
   * 
   * where R and S may have a leading zero byte if without it the values would
   * be negative.
   *
   * The format of the DNSSEC signature is just R . S where R and S are both
   * exactly "length" bytes.
   * 
   * @param signature
   *          The output of a ECDSA signature object.
   * @return signature data formatted for use in DNSSEC.
   * @throws SignatureException if the ASN.1 encoding appears to be corrupt.
   */
  public static byte[] convertECDSASignature(int algorithm, byte[] signature) 
      throws SignatureException
  {
    int exp_length = ecdsaLength(algorithm);
    byte[] sig = new byte[exp_length * 2];

    if (signature[0] != ASN1_SEQ || signature[2] != ASN1_INT)
    {
      throw new SignatureException("Invalid ASN.1 signature format: expected SEQ, INT");
    }
    int r_len = signature[3];
    int r_pos = 4;
 
    if (signature[r_pos + r_len] != ASN1_INT)
    {
      throw new SignatureException("Invalid ASN.1 signature format: expected SEQ, INT, INT");
    }
    int s_pos = r_pos + r_len + 2;
    int s_len = signature[r_pos + r_len + 1];

    // Adjust for leading zeros on both R and S
    if (signature[r_pos] == 0) {
      r_pos++;
      r_len--;
    }
    if (signature[s_pos] == 0) {
      s_pos++;
      s_len--;
    }

    System.arraycopy(signature, r_pos, sig, 0 + (exp_length - r_len), r_len);
    System.arraycopy(signature, s_pos, sig, exp_length + (exp_length - s_len), s_len);

    return sig;
  }

  /**
   * Convert a DNS standard ECDSA signature (defined in RFC 6605) into a
   * JCE standard ECDSA signature, which is encoded in ASN.1.
   * 
   * The format of the ASN.1 signature is
   * 
   * ASN1_SEQ . seq_length . ASN1_INT . r_length . R . ANS1_INT . s_length . S
   * 
   * where R and S may have a leading zero byte if without it the values would
   * be negative.
   *
   * The format of the DNSSEC signature is just R . S where R and S are both
   * exactly "length" bytes.
   * 
   * @param signature
   *          The binary signature data from an RRSIG record.
   * @return signature data that may be used in a JCE Signature object for
   *         verification purposes.
   */
  public static byte[] convertECDSASignature(byte[] signature)
  {
    byte r_src_pos, r_src_len, r_pad, s_src_pos, s_src_len, s_pad, len;

    r_src_len = s_src_len = (byte) (signature.length / 2);
    r_src_pos = 0; r_pad = 0;
    s_src_pos = (byte) (r_src_pos + r_src_len); s_pad = 0;
    len = (byte) (6 + r_src_len + s_src_len);

    if (signature[r_src_pos] < 0) {
        r_pad = 1; len++;
    }
    if (signature[s_src_pos] < 0) {
      s_pad = 1; len++;
    }
    byte[] sig = new byte[len];
    byte pos = 0;

    sig[pos++] = ASN1_SEQ;
    sig[pos++] = (byte) (len - 2);
    sig[pos++] = ASN1_INT;
    sig[pos++] = (byte) (r_src_len + r_pad);
    pos += r_pad;
    System.arraycopy(signature, r_src_pos, sig, pos, r_src_len);
    pos += r_src_len;

    sig[pos++] = ASN1_INT;
    sig[pos++] = (byte) (s_src_len + s_pad);
    pos += s_pad;
    System.arraycopy(signature, s_src_pos, sig, pos, s_src_len);

    return sig;
  }

  /**
   * This is a convenience routine to help us classify records/RRsets.
   * 
   * It characterizes a record/RRset as one of the following classes:<br/>
   * <dl>
   * 
   * <dt>NORMAL</dt>
   * <dd>This record/set is properly within the zone an subject to all NXT and
   * SIG processing.</dd>
   * 
   * <dt>DELEGATION</dt>
   * <dd>This is a zone delegation point (or cut). It is used in NXT processing
   * but is not signed.</dd>
   * 
   * <dt>GLUE</dt>
   * <dd>This is a glue record and therefore not properly within the zone. It is
   * not included in NXT or SIG processing. Normally glue records are A records,
   * but this routine calls anything that is below a zone delegation glue.</dd>
   * 
   * <dt>INVALID</dt>
   * <dd>This record doesn't even belong in the zone.</dd>
   * 
   * </dl>
   * <br/>
   * 
   * This method must be called successively on records in the canonical name
   * ordering, and the caller must maintain the last_cut parameter.
   * 
   * @param zonename
   *          the name of the zone that is being processed.
   * @param name
   *          the name of the record/set under consideration.
   * @param type
   *          the type of the record/set under consideration.
   * @param last_cut
   *          the name of the last DELEGATION record/set that was encountered
   *          while iterating over the zone in canonical order.
   */
  public static int recordSecType(Name zonename, Name name, int type, Name last_cut,
                                  Name last_dname)
  {
    // records not even in the zone itself are invalid.
    if (!name.subdomain(zonename)) return RR_INVALID;

    // all records a the zone apex are normal, by definition.
    if (name.equals(zonename)) return RR_NORMAL;

    if (last_cut != null && name.subdomain(last_cut))
    {
      // if we are at the same level as a delegation point, but not one of a set of types allowed at 
      // a delegation point (NS, DS, NSEC), this is glue.
      if (name.equals(last_cut))
      {
        if (type != Type.NS && type != Type.DS && type != Type.NXT && type != Type.NSEC)
        {
          return RR_GLUE;
        }
      }
      // if we are below the delegation point, this is glue.
      else
      {
        return RR_GLUE;
      }

    }

    // if we are below a DNAME, then the RR is invalid.    
    if (last_dname != null && name.subdomain(last_dname)
        && name.labels() > last_dname.labels())
    {
      return RR_INVALID;
    }

    // since we are not at zone level, any NS records are delegations
    if (type == Type.NS) return RR_DELEGATION;

    // and everything else is normal
    return RR_NORMAL;
  }

  /**
   * Given a canonical ordered list of records from a single zone, order the raw
   * records into a list of RRsets.
   * 
   * @param records
   *          a list of {@link org.xbill.DNS.Record} objects, in DNSSEC
   *          canonical order.
   * @return a List of {@link org.xbill.DNS.RRset} objects.
   */
  public static List<RRset> assembleIntoRRsets(List<Record> records)
  {
    RRset rrset = new RRset();
    ArrayList<RRset> rrsets = new ArrayList<RRset>();

    for (Record r : records)
    {
      // First record
      if (rrset.size() == 0)
      {
        rrset.addRR(r);
        continue;
      }

      // Current record is part of the current RRset.
      if (rrset.getName().equals(r.getName())
          && rrset.getDClass() == r.getDClass()
          && ((r.getType() == Type.RRSIG && rrset.getType() == ((RRSIGRecord) r).getTypeCovered()) || rrset.getType() == r.getType()))
      {
        rrset.addRR(r);
        continue;
      }

      // otherwise, we have completed the RRset
      rrsets.add(rrset);

      // set up for the next set.
      rrset = new RRset();
      rrset.addRR(r);
    }

    // add the last rrset.
    rrsets.add(rrset);

    return rrsets;
  }

  /**
   * A little private class to hold information about a given node.
   */
  private static class NodeInfo
  {
    public Name         name;
    public int          type;
    public long         ttl;
    public int          dclass;
    public Set<Integer> typemap;
    public boolean      isSecureNode; // opt-in support.
    public boolean      hasOptInSpan; // opt-in support.
    public int          nsecIndex;

    public NodeInfo(Record r, int nodeType)
    {
      this.name = r.getName();
      this.type = nodeType;
      this.ttl = r.getTTL();
      this.dclass = r.getDClass();
      this.typemap = new HashSet<Integer>();
      this.isSecureNode = false;
      this.hasOptInSpan = false;
      addType(r.getType());
    }

    public void addType(int type)
    {
      this.typemap.add(new Integer(type));

      // Opt-In support.
      if (type != Type.NS && type != Type.NSEC && type != Type.RRSIG
          && type != Type.NSEC3)
      {
        isSecureNode = true;
      }
    }

    public boolean hasType(int type)
    {
      return this.typemap.contains(type);
    }

    public String toString()
    {
      StringBuffer sb = new StringBuffer(name.toString());
      if (isSecureNode) sb.append("(S)");
      if (hasOptInSpan) sb.append("(O)");
      return sb.toString();
    }

    public int[] getTypes()
    {
      Object[] a = typemap.toArray();
      int[] res = new int[a.length];

      for (int i = 0; i < a.length; i++)
      {
        res[i] = ((Integer) a[i]).intValue();
      }
      return res;
    }
  }

  /**
   * Given a canonical (by name) ordered list of records in a zone, generate the
   * NSEC records in place.
   * 
   * Note that the list that the records are stored in must support the
   * listIterator.add() operation.
   * 
   * @param zonename
   *          the name of the zone (used to distinguish between zone apex NS
   *          RRsets and delegations).
   * @param records
   *          a list of {@link org.xbill.DNS.Record} objects in DNSSEC canonical
   *          order.
   */
  public static void generateNSECRecords(Name zonename, List<Record> records)
  {
    // This works by iterating over a known sorted list of records.

    NodeInfo last_node = null;
    NodeInfo current_node = null;

    Name last_cut = null;
    Name last_dname = null;
    int backup;
    long nsec_ttl = 0;

    // First find the SOA record -- it should be near the beginning -- and get
    // the soa minimum
    for (Record r : records)
    {
      if (r.getType() == Type.SOA)
      {
        SOARecord soa = (SOARecord) r;
        nsec_ttl = soa.getMinimum();
        break;
      }
    }

    if (nsec_ttl == 0)
    {
      throw new IllegalArgumentException("Zone did not contain a SOA record");
    }

    for (ListIterator<Record> i = records.listIterator(); i.hasNext();)
    {
      Record r = i.next();
      Name r_name = r.getName();
      int r_type = r.getType();
      int r_sectype = recordSecType(zonename, r_name, r_type, last_cut, last_dname);

      // skip irrelevant records
      if (r_sectype == RR_INVALID || r_sectype == RR_GLUE) continue;

      // note our last delegation point so we can recognize glue.
      if (r_sectype == RR_DELEGATION) last_cut = r_name;

      // if this is a DNAME, note it so we can recognize junk
      if (r_type == Type.DNAME) last_dname = r_name;

      // first node -- initialize
      if (current_node == null)
      {
        current_node = new NodeInfo(r, r_sectype);
        current_node.addType(Type.RRSIG);
        current_node.addType(Type.NSEC);
        continue;
      }

      // record name hasn't changed, so we are still on the same node.
      if (r_name.equals(current_node.name))
      {
        current_node.addType(r_type);
        continue;
      }

      if (last_node != null)
      {
        NSECRecord nsec = new NSECRecord(last_node.name, last_node.dclass, nsec_ttl,
                                         current_node.name, last_node.getTypes());
        // Note: we have to add this through the iterator, otherwise
        // the next access via the iterator will generate a
        // ConcurrencyModificationException.
        backup = i.nextIndex() - last_node.nsecIndex;
        for (int j = 0; j < backup; j++)
          i.previous();
        i.add(nsec);
        for (int j = 0; j < backup; j++)
          i.next();

        log.finer("Generated: " + nsec);
      }

      last_node = current_node;

      current_node.nsecIndex = i.previousIndex();
      current_node = new NodeInfo(r, r_sectype);
      current_node.addType(Type.RRSIG);
      current_node.addType(Type.NSEC);
    }

    // Generate next to last NSEC
    if (last_node != null)
    {
      NSECRecord nsec = new NSECRecord(last_node.name, last_node.dclass, nsec_ttl,
                                       current_node.name, last_node.getTypes());
      records.add(last_node.nsecIndex - 1, nsec);
      log.finer("Generated: " + nsec);
    }

    // Generate last NSEC
    NSECRecord nsec = new NSECRecord(current_node.name, current_node.dclass, nsec_ttl,
                                     zonename, current_node.getTypes());
    records.add(nsec);

    log.finer("Generated: " + nsec);
  }

  /**
   * Given a canonical (by name) ordered list of records in a zone, generate the
   * NSEC3 records in place.
   * 
   * Note that the list that the records are stored in must support the
   * listIterator.add() operation.
   * 
   * @param zonename
   *          the name of the zone (used to distinguish between zone apex NS
   *          RRsets and delegations).
   * @param records
   *          a list of {@link org.xbill.DNS.Record} objects in DNSSEC canonical
   *          order.
   * @param salt
   *          The NSEC3 salt to use (may be null or empty for no salt).
   * @param iterations
   *          The number of hash iterations to use.
   * @param nsec3param_ttl
   *          The TTL to use for the generated NSEC3PARAM records (NSEC3 records
   *          will use the SOA minimum)
   * @throws NoSuchAlgorithmException
   */
  public static void generateNSEC3Records(Name zonename, List<Record> records,
                                          byte[] salt, int iterations, long nsec3param_ttl)
      throws NoSuchAlgorithmException
  {
    List<ProtoNSEC3> proto_nsec3s = new ArrayList<ProtoNSEC3>();
    NodeInfo current_node = null;
    NodeInfo last_node = null;
    // For detecting glue.
    Name last_cut = null;
    // For detecting junk below a DNAME
    Name last_dname = null;

    long nsec3_ttl = 0;

    for (Record r : records)
    {
      Name r_name = r.getName();
      int r_type = r.getType();

      // Classify this record so we know if we can skip it.
      int r_sectype = recordSecType(zonename, r_name, r_type, last_cut, last_dname);

      // skip irrelevant records
      if (r_sectype == RR_INVALID || r_sectype == RR_GLUE) continue;

      // note our last delegation point so we can recognize glue.
      if (r_sectype == RR_DELEGATION) last_cut = r_name;

      // note our last DNAME point, so we can recognize junk.
      if (r_type == Type.DNAME) last_dname = r_name;

      if (r_type == Type.SOA)
      {
        SOARecord soa = (SOARecord) r;
        nsec3_ttl = soa.getMinimum();
        if (nsec3param_ttl < 0)
        {
          nsec3param_ttl = soa.getTTL();
        }
      }

      // For the first iteration, we create our current node.
      if (current_node == null)
      {
        current_node = new NodeInfo(r, r_sectype);
        continue;
      }

      // If we are at the same name, we are on the same node.
      if (r_name.equals(current_node.name))
      {
        current_node.addType(r_type);
        continue;
      }

      // At this point, r represents the start of a new node.
      // So we move current_node to last_node and generate a new current node.
      // But first, we need to do something with the last node.
      generateNSEC3ForNode(last_node, zonename, salt, iterations, false, proto_nsec3s);

      last_node = current_node;
      current_node = new NodeInfo(r, r_sectype);
    }

    // process last two nodes.
    generateNSEC3ForNode(last_node, zonename, salt, iterations, false, proto_nsec3s);
    generateNSEC3ForNode(current_node, zonename, salt, iterations, false, proto_nsec3s);

    List<NSEC3Record> nsec3s = finishNSEC3s(proto_nsec3s, nsec3_ttl);

    records.addAll(nsec3s);

    NSEC3PARAMRecord nsec3param = new NSEC3PARAMRecord(zonename, DClass.IN,
                                                       nsec3param_ttl,
                                                       NSEC3Record.SHA1_DIGEST_ID,
                                                       (byte) 0, iterations, salt);
    records.add(nsec3param);

  }

  /**
   * Given a canonical (by name) ordered list of records in a zone, generate the
   * NSEC3 records in place using Opt-Out NSEC3 records. This means that
   * non-apex NS RRs (and glue below those delegations) will, by default, not be
   * included in the NSEC3 chain.
   * 
   * Note that the list that the records are stored in must support the
   * listIterator.add() operation.
   * 
   * @param zonename
   *          the name of the zone (used to distinguish between zone apex NS
   *          RRsets and delegations).
   * @param records
   *          a list of {@link org.xbill.DNS.Record} objects in DNSSEC canonical
   *          order.
   * @param includedNames
   *          A list of {@link org.xbill.DNS.Name} objects. These names will be
   *          included in the NSEC3 chain (if they exist in the zone)
   *          regardless.
   * @param salt
   *          The NSEC3 salt to use (may be null or empty for no salt).
   * @param iterations
   *          The number of hash iterations to use.
   * @param nsec3param_ttl
   *          The TTL to use for the generated NSEC3PARAM records (NSEC3 records
   *          will use the SOA minimum)
   * @throws NoSuchAlgorithmException
   */
  public static void generateOptOutNSEC3Records(Name zonename, List<Record> records,
                                                List<Name> includedNames, byte[] salt,
                                                int iterations, long nsec3param_ttl)
      throws NoSuchAlgorithmException
  {
    List<ProtoNSEC3> proto_nsec3s = new ArrayList<ProtoNSEC3>();
    NodeInfo current_node = null;
    NodeInfo last_node = null;
    // For detecting glue.
    Name last_cut = null;
    // For detecting out-of-zone records below a DNAME
    Name last_dname = null;

    long nsec3_ttl = 0;

    HashSet<Name> includeSet = null;
    if (includedNames != null)
    {
      includeSet = new HashSet<Name>(includedNames);
    }

    for (Record r : records)
    {
      Name r_name = r.getName();
      int r_type = r.getType();

      // Classify this record so we know if we can skip it.
      int r_sectype = recordSecType(zonename, r_name, r_type, last_cut, last_dname);

      // skip irrelevant records
      if (r_sectype == RR_INVALID || r_sectype == RR_GLUE) continue;

      // note our last delegation point so we can recognize glue.
      if (r_sectype == RR_DELEGATION) last_cut = r_name;

      if (r_type == Type.DNAME) last_dname = r_name;

      if (r_type == Type.SOA)
      {
        SOARecord soa = (SOARecord) r;
        nsec3_ttl = soa.getMinimum();
        if (nsec3param_ttl < 0)
        {
          nsec3param_ttl = soa.getTTL();
        }
      }

      // For the first iteration, we create our current node.
      if (current_node == null)
      {
        current_node = new NodeInfo(r, r_sectype);
        continue;
      }

      // If we are at the same name, we are on the same node.
      if (r_name.equals(current_node.name))
      {
        current_node.addType(r_type);
        continue;
      }

      if (includeSet != null && includeSet.contains(current_node.name))
      {
        current_node.isSecureNode = true;
      }

      // At this point, r represents the start of a new node.
      // So we move current_node to last_node and generate a new current node.
      // But first, we need to do something with the last node.
      generateNSEC3ForNode(last_node, zonename, salt, iterations, true, proto_nsec3s);

      if (current_node.isSecureNode)
      {
        last_node = current_node;
      }
      else
      {
        last_node.hasOptInSpan = true;
      }

      current_node = new NodeInfo(r, r_sectype);
    }

    // process last two nodes.
    generateNSEC3ForNode(last_node, zonename, salt, iterations, true, proto_nsec3s);
    generateNSEC3ForNode(current_node, zonename, salt, iterations, true, proto_nsec3s);

    List<NSEC3Record> nsec3s = finishNSEC3s(proto_nsec3s, nsec3_ttl);
    records.addAll(nsec3s);

    NSEC3PARAMRecord nsec3param = new NSEC3PARAMRecord(zonename, DClass.IN,
                                                       nsec3param_ttl,
                                                       NSEC3Record.SHA1_DIGEST_ID,
                                                       (byte) 0, iterations, salt);
    records.add(nsec3param);
  }

  /**
   * For a given node (representing all of the RRsets at a given name), generate
   * all of the necessary NSEC3 records for it. That is, generate the NSEC3 for
   * the node itself, and for any potential empty non-terminals.
   * 
   * @param node
   *          The node in question.
   * @param zonename
   *          The zonename.
   * @param salt
   *          The salt to use for the NSEC3 RRs
   * @param iterations
   *          The iterations to use for the NSEC3 RRs.
   * @param optIn
   *          If true, the NSEC3 will have the Opt-Out flag set.
   * @param nsec3s
   *          The current list of NSEC3s -- this will be updated.
   * @throws NoSuchAlgorithmException
   */
  private static void generateNSEC3ForNode(NodeInfo node, Name zonename, byte[] salt,
                                           int iterations, boolean optIn, List<ProtoNSEC3> nsec3s)
      throws NoSuchAlgorithmException
  {
    if (node == null) return;
    if (optIn && !node.isSecureNode) return;

    // Add our default types.
    if (node.type == RR_NORMAL || (node.type == RR_DELEGATION && node.hasType(Type.DS)))
    {
      node.addType(Type.RRSIG);
    }
    if (node.name.equals(zonename)) node.addType(Type.NSEC3PARAM);

    // Check for ENTs -- note this will generate duplicate ENTs because it
    // doesn't use any context.
    int ldiff = node.name.labels() - zonename.labels();
    for (int i = 1; i < ldiff; i++)
    {
      Name n = new Name(node.name, i);
      log.fine("Generating ENT NSEC3 for " + n);
      ProtoNSEC3 nsec3 = generateNSEC3(n, zonename, node.ttl, salt, iterations, optIn,
                                       null);
      nsec3s.add(nsec3);
    }

    ProtoNSEC3 nsec3 = generateNSEC3(node.name, zonename, node.ttl, salt, iterations,
                                     optIn, node.getTypes());
    nsec3s.add(nsec3);
  }

  /**
   * Create a "prototype" NSEC3 record. Basically, a mutable NSEC3 record.
   * 
   * @param name
   *          The original ownername to use.
   * @param zonename
   *          The zonename to use.
   * @param ttl
   *          The TTL to use.
   * @param salt
   *          The salt to use.
   * @param iterations
   *          The number of hash iterations to use.
   * @param optIn
   *          The value of the Opt-Out flag.
   * @param types
   *          The typecodes present at this name.
   * @return A mutable NSEC3 record.
   * 
   * @throws NoSuchAlgorithmException
   */
  private static ProtoNSEC3 generateNSEC3(Name name, Name zonename, long ttl,
                                          byte[] salt, int iterations, boolean optIn,
                                          int[] types) throws NoSuchAlgorithmException
  {
    byte[] hash = nsec3hash(name, NSEC3Record.SHA1_DIGEST_ID, iterations, salt);
    byte flags = (byte) (optIn ? 0x01 : 0x00);
    
    ProtoNSEC3 r = new ProtoNSEC3(hash, name, zonename, DClass.IN, ttl,
                                  NSEC3Record.SHA1_DIGEST_ID, flags, iterations, salt,
                                  null, types);

    log.finer("Generated: " + r);
    return r;
  }

  /**
   * Given a list of {@link ProtoNSEC3} object (mutable NSEC3 RRs), convert the
   * list into the set of actual {@link org.xbill.DNS.NSEC3Record} objects. This
   * will remove duplicates and finalize the records.
   * 
   * @param nsec3s
   *          The list of ProtoNSEC3 objects
   * @param ttl
   *          The TTL to assign to the finished NSEC3 records. In general, this
   *          should match the SOA minimum value for the zone.
   * @return The list of {@link org.xbill.DNS.NSEC3Record} objects.
   */
  private static List<NSEC3Record> finishNSEC3s(List<ProtoNSEC3> nsec3s, long ttl)
  {
    if (nsec3s == null) return null;
    Collections.sort(nsec3s, new ProtoNSEC3.Comparator());

    ProtoNSEC3 prev_nsec3 = null;
    ProtoNSEC3 cur_nsec3 = null;
    byte[] first_nsec3_hash = null;

    for (ListIterator<ProtoNSEC3> i = nsec3s.listIterator(); i.hasNext();)
    {
      cur_nsec3 = i.next();

      // check to see if cur is a duplicate (by name)
      if (prev_nsec3 != null
          && Arrays.equals(prev_nsec3.getOwner(), cur_nsec3.getOwner()))
      {
        log.fine("found duplicate NSEC3 (by name) -- merging type maps: "
            + prev_nsec3.getTypemap() + " and " + cur_nsec3.getTypemap());
        i.remove();
        prev_nsec3.mergeTypes(cur_nsec3.getTypemap());
        log.fine("merged type map: " + prev_nsec3.getTypemap());
        continue;
      }

      byte[] next = cur_nsec3.getOwner();

      if (prev_nsec3 == null)
      {
        prev_nsec3 = cur_nsec3;
        first_nsec3_hash = next;
        continue;
      }

      prev_nsec3.setNext(next);
      prev_nsec3 = cur_nsec3;
    }

    // Handle last NSEC3.
    if (prev_nsec3.getNext() == null)
    {
      // if prev_nsec3's next field hasn't been set, then it is the last
      // record (i.e., all remaining records were duplicates.)
      prev_nsec3.setNext(first_nsec3_hash);
    }
    else
    {
      // otherwise, cur_nsec3 is the last record.
      cur_nsec3.setNext(first_nsec3_hash);
    }

    // Convert our ProtoNSEC3s to actual (immutable) NSEC3Record objects.
    List<NSEC3Record> res = new ArrayList<NSEC3Record>(nsec3s.size());
    for (ProtoNSEC3 p : nsec3s)
    {
      p.setTTL(ttl);
      res.add(p.getNSEC3Record());
    }

    return res;
  }

  /**
   * Given a canonical (by name) ordered list of records in a zone, generate the
   * NSEC records in place.
   * 
   * Note that the list that the records are stored in must support the
   * <code>listIterator.add</code> operation.
   * 
   * @param zonename
   *          the name of the zone apex, used to distinguish between
   *          authoritative and delegation NS RRsets.
   * @param records
   *          a list of {@link org.xbill.DNS.Record}s in DNSSEC canonical order.
   * @param includeNames
   *          a list of names that should be in the NXT chain regardless. This
   *          may be null.
   * @param beConservative
   *          if true, then Opt-In NXTs will only be generated where there is
   *          actually a span of insecure delegations.
   */
  public static void generateOptInNSECRecords(Name zonename, List<Record> records,
                                              List<Name> includeNames,
                                              boolean beConservative)
  {
    // This works by iterating over a known sorted list of records.

    NodeInfo last_node = null;
    NodeInfo current_node = null;

    Name last_cut = null;
    Name last_dname = null;

    int backup;
    HashSet<Name> includeSet = null;

    if (includeNames != null)
    {
      includeSet = new HashSet<Name>(includeNames);
    }

    for (ListIterator<Record> i = records.listIterator(); i.hasNext();)
    {
      Record r = i.next();
      Name r_name = r.getName();
      int r_type = r.getType();
      int r_sectype = recordSecType(zonename, r_name, r_type, last_cut, last_dname);

      // skip irrelevant records
      if (r_sectype == RR_INVALID || r_sectype == RR_GLUE) continue;

      // note our last delegation point so we can recognize glue.
      if (r_sectype == RR_DELEGATION) last_cut = r_name;

      if (r_type == Type.DNAME) last_dname = r_name;

      // first node -- initialize
      if (current_node == null)
      {
        current_node = new NodeInfo(r, r_sectype);
        current_node.addType(Type.RRSIG);
        continue;
      }

      // record name hasn't changed, so we are still on the same node.
      if (r_name.equals(current_node.name))
      {
        current_node.addType(r_type);
        continue;
      }

      // If the name is in the set of included names, mark it as
      // secure.
      if (includeSet != null && includeSet.contains(current_node.name))
      {
        current_node.isSecureNode = true;
      }

      if (last_node != null && current_node.isSecureNode)
      {
        // generate a NSEC record.
        if (beConservative && !last_node.hasOptInSpan)
        {
          last_node.addType(Type.NSEC);
        }
        NSECRecord nsec = new NSECRecord(last_node.name, last_node.dclass, last_node.ttl,
                                         current_node.name, last_node.getTypes());
        // Note: we have to add this through the iterator, otherwise
        // the next access via the iterator will generate a
        // ConcurrencyModificationException.
        backup = i.nextIndex() - last_node.nsecIndex;
        for (int j = 0; j < backup; j++)
          i.previous();
        i.add(nsec);
        for (int j = 0; j < backup; j++)
          i.next();

        log.finer("Generated: " + nsec);
      }

      if (current_node.isSecureNode)
      {
        last_node = current_node;
      }
      else if (last_node != null)
      {
        // last_node does not change -- last_node is essentially the
        // last *secure* node, and current_node is not secure.
        // However, we need to note the passing of the insecure node.
        last_node.hasOptInSpan = true;
      }

      current_node.nsecIndex = i.previousIndex();
      current_node = new NodeInfo(r, r_sectype);
      current_node.addType(Type.RRSIG);
    }

    // Generate next to last NSEC
    if (last_node != null && current_node.isSecureNode)
    {
      // generate a NSEC record.
      if (beConservative && !last_node.hasOptInSpan)
      {
        last_node.addType(Type.NSEC);
      }
      NSECRecord nsec = new NSECRecord(last_node.name, last_node.dclass, last_node.ttl,
                                       current_node.name, last_node.getTypes());
      records.add(last_node.nsecIndex - 1, nsec);
      log.finer("Generated: " + nsec);
    }

    // Generate last NSEC
    NSECRecord nsec;
    if (current_node.isSecureNode)
    {
      if (beConservative)
      {
        current_node.addType(Type.NSEC);
      }
      nsec = new NSECRecord(current_node.name, current_node.dclass, current_node.ttl,
                            zonename, current_node.getTypes());
      // we can just tack this on the end as we are working on the
      // last node.
      records.add(nsec);
    }
    else
    {
      nsec = new NSECRecord(last_node.name, last_node.dclass, last_node.ttl, zonename,
                            last_node.getTypes());
      // We need to tack this on after the last secure node, not the
      // end of the whole list.
      records.add(last_node.nsecIndex, nsec);
    }

    log.finer("Generated: " + nsec);
  }

  /**
   * Given a zone with DNSKEY records at delegation points, convert those KEY
   * records into their corresponding DS records in place.
   * 
   * @param zonename
   *          the name of the zone, used to reliably distinguish the zone apex
   *          from other records.
   * @param records
   *          a list of {@link org.xbill.DNS.Record} objects.
   * @param digest_alg
   *          The digest algorithm to use.
   */
  public static void generateDSRecords(Name zonename, List<Record> records, int digest_alg)
  {

    for (ListIterator<Record> i = records.listIterator(); i.hasNext();)
    {
      Record r = i.next();
      if (r == null) continue; // this should never be true.

      Name r_name = r.getName();
      if (r_name == null) continue; // this should never be true.

      // Convert non-zone level KEY records into DS records.
      if (r.getType() == Type.DNSKEY && !r_name.equals(zonename))
      {
        DSRecord ds = calculateDSRecord((DNSKEYRecord) r, digest_alg, r.getTTL());

        i.set(ds);
      }
    }
  }

  /**
   * Given a zone, remove all records that are generated.
   * 
   * @param zonename
   *          the name of the zone.
   * @param records
   *          a list of {@link org.xbill.DNS.Record} objects.
   */
  public static void removeGeneratedRecords(Name zonename, List<Record> records)
  {
    for (Iterator<Record> i = records.iterator(); i.hasNext();)
    {
      Record r = (Record) i.next();

      if (r.getType() == Type.RRSIG || r.getType() == Type.NSEC
          || r.getType() == Type.NSEC3 || r.getType() == Type.NSEC3PARAM)
      {
        i.remove();
      }
    }
  }

  /**
   * Remove duplicate records from a list of records. This routine presumes the
   * list of records is in a canonical sorted order, at least on name and RR
   * type.
   * 
   * @param records
   *          a list of {@link org.xbill.DNS.Record} object, in sorted order.
   */
  public static void removeDuplicateRecords(List<Record> records)
  {
    Record lastrec = null;
    for (Iterator<Record> i = records.iterator(); i.hasNext();)
    {
      Record r = i.next();
      if (lastrec == null)
      {
        lastrec = r;
        continue;
      }
      if (lastrec.equals(r))
      {
        i.remove();
        continue;
      }
      lastrec = r;
    }
  }

  /**
   * Given a DNSKEY record, generate the DS record from it.
   * 
   * @param keyrec
   *          the KEY record in question.
   * @param digest_alg
   *          The digest algorithm (SHA-1, SHA-256, etc.).
   * @param ttl
   *          the desired TTL for the generated DS record. If zero, or negative,
   *          the original KEY RR's TTL will be used.
   * @return the corresponding {@link org.xbill.DNS.DSRecord}
   */
  public static DSRecord calculateDSRecord(DNSKEYRecord keyrec, int digest_alg, long ttl)
  {
    if (keyrec == null) return null;

    if (ttl <= 0) ttl = keyrec.getTTL();

    DNSOutput os = new DNSOutput();

    os.writeByteArray(keyrec.getName().toWireCanonical());
    os.writeByteArray(keyrec.rdataToWireCanonical());

    try
    {
      byte[] digest;
      MessageDigest md;

      switch (digest_alg)
      {
        case DSRecord.SHA1_DIGEST_ID:
          md = MessageDigest.getInstance("SHA");
          digest = md.digest(os.toByteArray());
          break;
        case DSRecord.SHA256_DIGEST_ID:
          md = MessageDigest.getInstance("SHA-256");
          digest = md.digest(os.toByteArray());
          break;
        default:
          throw new IllegalArgumentException("Unknown digest id: " + digest_alg);
      }

      return new DSRecord(keyrec.getName(), keyrec.getDClass(), ttl,
                          keyrec.getFootprint(), keyrec.getAlgorithm(), digest_alg,
                          digest);

    }
    catch (NoSuchAlgorithmException e)
    {
      log.severe(e.toString());
      return null;
    }
  }

  /**
   * Calculate an NSEC3 hash based on a DNS name and NSEC3 hash parameters.
   * 
   * @param n
   *          The name to hash.
   * @param hash_algorithm
   *          The hash algorithm to use.
   * @param iterations
   *          The number of iterations to do.
   * @param salt
   *          The salt to use.
   * @return The calculated hash as a byte array.
   * @throws NoSuchAlgorithmException
   *           If the hash algorithm is unrecognized.
   */
  public static byte[] nsec3hash(Name n, int hash_algorithm, int iterations, byte[] salt)
      throws NoSuchAlgorithmException
  {
    MessageDigest md;

    switch (hash_algorithm)
    {
      case NSEC3Record.SHA1_DIGEST_ID:
        md = MessageDigest.getInstance("SHA1");
        break;
      default:
        throw new NoSuchAlgorithmException("Unknown NSEC3 algorithm identifier: "
            + hash_algorithm);
    }

    // Construct our wire form.
    byte[] wire_name = n.toWireCanonical();
    byte[] res = wire_name; // for the first iteration.
    for (int i = 0; i <= iterations; i++)
    {
      // Concatenate the salt, if it exists.
      if (salt != null)
      {
        byte[] concat = new byte[res.length + salt.length];
        System.arraycopy(res, 0, concat, 0, res.length);
        System.arraycopy(salt, 0, concat, res.length, salt.length);
        res = concat;
      }
      res = md.digest(res);
    }

    return res;
  }

}
