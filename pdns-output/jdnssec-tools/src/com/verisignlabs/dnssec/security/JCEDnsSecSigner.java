// $Id$
//
// Copyright (C) 2001-2003, 2009 VeriSign, Inc.
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

import java.io.IOException;
import java.security.GeneralSecurityException;
import java.security.KeyPair;
import java.security.NoSuchAlgorithmException;
import java.security.Signature;
import java.security.interfaces.DSAPublicKey;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Date;
import java.util.Iterator;
import java.util.List;
import java.util.ListIterator;
import java.util.logging.Logger;

import org.xbill.DNS.*;
import org.xbill.DNS.Record;
import org.xbill.DNS.utils.hexdump;

/**
 * This class contains routines for signing DNS zones.
 * 
 * In particular, it contains both an ability to sign an individual RRset and
 * the ability to sign an entire zone. It primarily glues together the more
 * basic primitives found in {@link SignUtils}.
 * 
 * @author David Blacka (original)
 * @author $Author$
 * @version $Revision$
 */

public class JCEDnsSecSigner implements DnsSecSigner
{
  private DnsKeyConverter mKeyConverter;
  private boolean         mVerboseSigning = false;

  private Logger          log = Logger.getLogger(this.getClass().toString());

  public JCEDnsSecSigner()
  {
    this.mKeyConverter = null;
    this.mVerboseSigning = false;
  }

  public JCEDnsSecSigner(boolean verboseSigning)
  {
    this.mKeyConverter = null;
    this.mVerboseSigning = verboseSigning;
  }

  /**
   * Cryptographically generate a new DNSSEC key.
   * 
   * @param owner
   *          the KEY RR's owner name.
   * @param ttl
   *          the KEY RR's TTL.
   * @param dclass
   *          the KEY RR's DNS class.
   * @param algorithm
   *          the DNSSEC algorithm (RSAMD5, RSASHA1, or DSA).
   * @param flags
   *          any flags for the KEY RR.
   * @param keysize
   *          the size of the key to generate.
   * @param useLargeExponent
   *          if generating an RSA key, use the large exponent.
   * @return a DnsKeyPair with the public and private keys populated.
   */
  public DnsKeyPair generateKey(Name owner, long ttl, int dclass, int algorithm,
                                int flags, int keysize, boolean useLargeExponent)
      throws NoSuchAlgorithmException
  {
    DnsKeyAlgorithm algorithms = DnsKeyAlgorithm.getInstance();

    if (ttl < 0) ttl = 86400; // set to a reasonable default.

    KeyPair pair = algorithms.generateKeyPair(algorithm, keysize, useLargeExponent);

    if (mKeyConverter == null)
    {
      mKeyConverter = new DnsKeyConverter();
    }

    DNSKEYRecord keyrec = mKeyConverter.generateDNSKEYRecord(owner, dclass, ttl, flags,
                                                             algorithm, pair.getPublic());

    DnsKeyPair dnspair = new DnsKeyPair();
    dnspair.setDNSKEYRecord(keyrec);
    dnspair.setPublic(pair.getPublic()); // keep from conv. the keyrec back.
    dnspair.setPrivate(pair.getPrivate());

    return dnspair;
  }

  /**
   * Sign an RRset.
   * 
   * @param rrset
   *          the RRset to sign -- any existing signatures are ignored.
   * @param keypars
   *          a list of DnsKeyPair objects containing private keys.
   * @param start
   *          the inception time for the resulting RRSIG records.
   * @param expire
   *          the expiration time for the resulting RRSIG records.
   * @return a list of RRSIGRecord objects.
   */
  public List<RRSIGRecord> signRRset(RRset rrset, List<DnsKeyPair> keypairs, Date start,
                                     Date expire) throws IOException,
      GeneralSecurityException
  {
    if (rrset == null || keypairs == null) return null;

    // default start to now, expire to start + 1 second.
    if (start == null) start = new Date();
    if (expire == null) expire = new Date(start.getTime() + 1000L);
    if (keypairs.size() == 0) return null;

    if (mVerboseSigning)
    {
      log.info("Signing RRset:");
      log.info(ZoneUtils.rrsetToString(rrset, false));
    }

    // first, pre-calculate the RRset bytes.
    byte[] rrset_data = SignUtils.generateCanonicalRRsetData(rrset, 0, 0);

    ArrayList<RRSIGRecord> sigs = new ArrayList<RRSIGRecord>(keypairs.size());

    // for each keypair, sign the RRset.
    for (DnsKeyPair pair : keypairs)
    {
      DNSKEYRecord keyrec = pair.getDNSKEYRecord();
      if (keyrec == null) continue;

      RRSIGRecord presig = SignUtils.generatePreRRSIG(rrset, keyrec, start, expire,
                                                      rrset.getTTL());
      byte[] sign_data = SignUtils.generateSigData(rrset_data, presig);

      if (mVerboseSigning)
      {
        log.info("Canonical pre-signature data to sign with key "
            + keyrec.getName().toString() + "/" + keyrec.getAlgorithm() + "/"
            + keyrec.getFootprint() + ":");
        log.info(hexdump.dump(null, sign_data));
      }

      Signature signer = pair.getSigner();

      if (signer == null)
      {
        // debug
        log.fine("missing private key that goes with:\n" + pair.getDNSKEYRecord());
        throw new GeneralSecurityException("cannot sign without a valid Signer "
            + "(probably missing private key)");
      }

      // sign the data.
      signer.update(sign_data);
      byte[] sig = signer.sign();

      if (mVerboseSigning)
      {
        log.info("Raw Signature:");
        log.info(hexdump.dump(null, sig));
      }

      DnsKeyAlgorithm algs = DnsKeyAlgorithm.getInstance();
      // Convert to RFC 2536 format, if necessary.
      if (algs.baseType(pair.getDNSKEYAlgorithm()) == DnsKeyAlgorithm.DSA)
      {
        DSAPublicKey pk = (DSAPublicKey) pair.getPublic();
        sig = SignUtils.convertDSASignature(pk.getParams(), sig);
      }
      // Convert to RFC 6605, etc format
      if (pair.getDNSKEYAlgorithm() == DNSSEC.Algorithm.ECDSAP256SHA256 ||
          pair.getDNSKEYAlgorithm() == DNSSEC.Algorithm.ECDSAP384SHA384)
      {
        sig = SignUtils.convertECDSASignature(pair.getDNSKEYAlgorithm(), sig);
      }
      RRSIGRecord sigrec = SignUtils.generateRRSIG(sig, presig);
      if (mVerboseSigning)
      {
        log.info("RRSIG:\n" + sigrec);
      }
      sigs.add(sigrec);
    }

    return sigs;
  }

  /**
   * Create a completely self-signed DNSKEY RRset.
   * 
   * @param keypairs
   *          the public & private keypairs to use in the keyset.
   * @param start
   *          the RRSIG inception time.
   * @param expire
   *          the RRSIG expiration time.
   * @return a signed RRset.
   */
  public RRset makeKeySet(List<DnsKeyPair> keypairs, Date start, Date expire)
      throws IOException, GeneralSecurityException
  {
    // Generate a KEY RR set to sign.

    RRset keyset = new RRset();

    for (DnsKeyPair pair : keypairs)
    {
      keyset.addRR(pair.getDNSKEYRecord());
    }

    List<RRSIGRecord> records = signRRset(keyset, keypairs, start, expire);

    for (RRSIGRecord r : records)
    {
      keyset.addRR(r);
    }

    return keyset;
  }

  /**
   * Conditionally sign an RRset and add it to the toList.
   * 
   * @param toList
   *          the list to which we are adding the processed RRsets.
   * @param zonename
   *          the zone apex name.
   * @param rrset
   *          the RRset under consideration.
   * @param kskpairs
   *          the List of KSKs..
   * @param zskpairs
   *          the List of zone keys.
   * @param start
   *          the RRSIG inception time.
   * @param expire
   *          the RRSIG expiration time.
   * @param fullySignKeyset
   *          if true, sign the zone apex keyset with both KSKs and ZSKs.
   * @param last_cut
   *          the name of the last delegation point encountered.
   * 
   * @return the name of the new last_cut.
   */
  @SuppressWarnings("unchecked")
  private Name addRRset(List<Record> toList, Name zonename, RRset rrset,
                        List<DnsKeyPair> kskpairs, List<DnsKeyPair> zskpairs, Date start,
                        Date expire, boolean fullySignKeyset, Name last_cut,
                        Name last_dname) throws IOException, GeneralSecurityException
  {
    // add the records themselves
    for (Iterator<Record> i = rrset.rrs(); i.hasNext();)
    {
      toList.add(i.next());
    }

    int type = SignUtils.recordSecType(zonename, rrset.getName(), rrset.getType(),
                                       last_cut, last_dname);

    // we don't sign non-normal sets (delegations, glue, invalid).
    if (type == SignUtils.RR_DELEGATION)
    {
      return rrset.getName();
    }
    if (type == SignUtils.RR_GLUE || type == SignUtils.RR_INVALID)
    {
      return last_cut;
    }

    // check for the zone apex keyset.
    if (rrset.getName().equals(zonename) && rrset.getType() == Type.DNSKEY)
    {
      // if we have ksks, sign the keyset with them, otherwise we will just sign
      // them with the zsks.
      if (kskpairs != null && kskpairs.size() > 0)
      {
        List<RRSIGRecord> sigs = signRRset(rrset, kskpairs, start, expire);
        toList.addAll(sigs);

        // If we aren't going to sign with all the keys, bail out now.
        if (!fullySignKeyset) return last_cut;
      }
    }

    // otherwise, we are OK to sign this set.
    List<RRSIGRecord> sigs = signRRset(rrset, zskpairs, start, expire);
    toList.addAll(sigs);

    return last_cut;
  }

  // Various NSEC/NSEC3 generation modes
  private static final int NSEC_MODE         = 0;
  private static final int NSEC3_MODE        = 1;
  private static final int NSEC3_OPTOUT_MODE = 2;
  private static final int NSEC_EXP_OPT_IN   = 3;

  /**
   * Master zone signing method. This method handles all of the different zone
   * signing variants (NSEC with or without Opt-In, NSEC3 with or without
   * Opt-Out, etc.) External users of this class are expected to use the
   * appropriate public signZone* methods instead of this.
   * 
   * @param zonename
   *          The name of the zone
   * @param records
   *          The records comprising the zone. They do not have to be in any
   *          particular order, as this method will order them as necessary.
   * @param kskpairs
   *          The key pairs designated as "key signing keys"
   * @param zskpairs
   *          The key pairs designated as "zone signing keys"
   * @param start
   *          The RRSIG inception time
   * @param expire
   *          The RRSIG expiration time
   * @param fullySignKeyset
   *          If true, all keys (ksk or zsk) will sign the DNSKEY RRset. If
   *          false, only the ksks will sign it.
   * @param ds_digest_alg
   *          The hash algorithm to use for generating DS records
   *          (DSRecord.SHA1_DIGEST_ID, e.g.)
   * @param mode
   *          The NSEC/NSEC3 generation mode: NSEC_MODE, NSEC3_MODE,
   *          NSEC3_OPTOUT_MODE, etc.
   * @param includedNames
   *          When using an Opt-In/Opt-Out mode, the names listed here will be
   *          included in the NSEC/NSEC3 chain regardless
   * @param salt
   *          When using an NSEC3 mode, use this salt.
   * @param iterations
   *          When using an NSEC3 mode, use this number of iterations
   * @param beConservative
   *          If true, then only turn on the Opt-In flag when there are insecure
   *          delegations in the span. Currently this only works for
   *          NSEC_EXP_OPT_IN mode.
   * @param nsec3paramttl
   *          The TTL to use for the generated NSEC3PARAM record. Negative
   *          values will use the SOA TTL.
   * @return an ordered list of {@link org.xbill.DNS.Record} objects,
   *         representing the signed zone.
   * 
   * @throws IOException
   * @throws GeneralSecurityException
   */
  private List<Record> signZone(Name zonename, List<Record> records,
                                List<DnsKeyPair> kskpairs, List<DnsKeyPair> zskpairs,
                                Date start, Date expire, boolean fullySignKeyset,
                                int ds_digest_alg, int mode, List<Name> includedNames,
                                byte[] salt, int iterations, long nsec3paramttl,
                                boolean beConservative) throws IOException,
      GeneralSecurityException
  {
    // Remove any existing generated DNSSEC records (NSEC, NSEC3, NSEC3PARAM,
    // RRSIG)
    SignUtils.removeGeneratedRecords(zonename, records);

    RecordComparator rc = new RecordComparator();
    // Sort the zone
    Collections.sort(records, rc);

    // Remove duplicate records
    SignUtils.removeDuplicateRecords(records);

    // Generate DS records. This replaces any non-zone-apex DNSKEY RRs with DS
    // RRs.
    SignUtils.generateDSRecords(zonename, records, ds_digest_alg);

    // Generate the NSEC or NSEC3 records based on 'mode'
    switch (mode)
    {
      case NSEC_MODE:
        SignUtils.generateNSECRecords(zonename, records);
        break;
      case NSEC3_MODE:
        SignUtils.generateNSEC3Records(zonename, records, salt, iterations, nsec3paramttl);
        break;
      case NSEC3_OPTOUT_MODE:
        SignUtils.generateOptOutNSEC3Records(zonename, records, includedNames, salt,
                                             iterations, nsec3paramttl);
        break;
      case NSEC_EXP_OPT_IN:
        SignUtils.generateOptInNSECRecords(zonename, records, includedNames,
                                           beConservative);
        break;
    }

    // Re-sort so we can assemble into rrsets.
    Collections.sort(records, rc);

    // Assemble into RRsets and sign.
    RRset rrset = new RRset();
    ArrayList<Record> signed_records = new ArrayList<Record>();
    Name last_cut = null;
    Name last_dname = null;

    for (ListIterator<Record> i = records.listIterator(); i.hasNext();)
    {
      Record r = i.next();

      // First record
      if (rrset.size() == 0)
      {
        rrset.addRR(r);
        continue;
      }

      // Current record is part of the current RRset.
      if (rrset.getName().equals(r.getName()) && rrset.getDClass() == r.getDClass()
          && rrset.getType() == r.getType())
      {
        rrset.addRR(r);
        continue;
      }

      // Otherwise, we have completed the RRset
      // Sign the records

      // add the RRset to the list of signed_records, regardless of
      // whether or not we actually end up signing the set.
      last_cut = addRRset(signed_records, zonename, rrset, kskpairs, zskpairs, start,
                          expire, fullySignKeyset, last_cut, last_dname);
      if (rrset.getType() == Type.DNAME) last_dname = rrset.getName();

      rrset.clear();
      rrset.addRR(r);
    }

    // add the last RR set
    addRRset(signed_records, zonename, rrset, kskpairs, zskpairs, start, expire,
             fullySignKeyset, last_cut, last_dname);

    return signed_records;
  }

  /**
   * Given a zone, sign it using standard NSEC records.
   * 
   * @param zonename
   *          The name of the zone.
   * @param records
   *          The records comprising the zone. They do not have to be in any
   *          particular order, as this method will order them as necessary.
   * @param kskpairs
   *          The key pairs that are designated as "key signing keys".
   * @param zskpairs
   *          This key pairs that are designated as "zone signing keys".
   * @param start
   *          The RRSIG inception time.
   * @param expire
   *          The RRSIG expiration time.
   * @param fullySignKeyset
   *          Sign the zone apex keyset with all available keys (instead of just
   *          the key signing keys).
   * @param ds_digest_alg
   *          The digest algorithm to use when generating DS records.
   * 
   * @return an ordered list of {@link org.xbill.DNS.Record} objects,
   *         representing the signed zone.
   */
  public List<Record> signZone(Name zonename, List<Record> records,
                               List<DnsKeyPair> kskpairs, List<DnsKeyPair> zskpairs,
                               Date start, Date expire, boolean fullySignKeyset,
                               int ds_digest_alg) throws IOException,
      GeneralSecurityException
  {
    return signZone(zonename, records, kskpairs, zskpairs, start, expire,
                    fullySignKeyset, ds_digest_alg, NSEC_MODE, null, null, 0, 0, false);
  }

  /**
   * Given a zone, sign it using NSEC3 records.
   * 
   * @param signer
   *          A signer (utility) object used to actually sign stuff.
   * @param zonename
   *          The name of the zone being signed.
   * @param records
   *          The records comprising the zone. They do not have to be in any
   *          particular order, as this method will order them as necessary.
   * @param kskpairs
   *          The key pairs that are designated as "key signing keys".
   * @param zskpairs
   *          This key pairs that are designated as "zone signing keys".
   * @param start
   *          The RRSIG inception time.
   * @param expire
   *          The RRSIG expiration time.
   * @param fullySignKeyset
   *          If true then the DNSKEY RRset will be signed by all available
   *          keys, if false, only the key signing keys.
   * @param useOptOut
   *          If true, insecure delegations will be omitted from the NSEC3
   *          chain, and all NSEC3 records will have the Opt-Out flag set.
   * @param includedNames
   *          A list of names to include in the NSEC3 chain regardless.
   * @param salt
   *          The salt to use for the NSEC3 hashing. null means no salt.
   * @param iterations
   *          The number of iterations to use for the NSEC3 hashing.
   * @param ds_digest_alg
   *          The digest algorithm to use when generating DS records.
   * @param nsec3paramttl
   *          The TTL to use for the generated NSEC3PARAM record. Negative
   *          values will use the SOA TTL.
   * @return an ordered list of {@link org.xbill.DNS.Record} objects,
   *         representing the signed zone.
   * 
   * @throws IOException
   * @throws GeneralSecurityException
   */
  public List<Record> signZoneNSEC3(Name zonename, List<Record> records,
                                    List<DnsKeyPair> kskpairs, List<DnsKeyPair> zskpairs,
                                    Date start, Date expire, boolean fullySignKeyset,
                                    boolean useOptOut, List<Name> includedNames,
                                    byte[] salt, int iterations, int ds_digest_alg,
                                    long nsec3paramttl) throws IOException,
      GeneralSecurityException
  {
    if (useOptOut)
    {
      return signZone(zonename, records, kskpairs, zskpairs, start, expire,
                      fullySignKeyset, ds_digest_alg, NSEC3_OPTOUT_MODE, includedNames,
                      salt, iterations, nsec3paramttl, false);
    }
    else
    {
      return signZone(zonename, records, kskpairs, zskpairs, start, expire,
                      fullySignKeyset, ds_digest_alg, NSEC3_MODE, null, salt, iterations,
                      nsec3paramttl, false);
    }
  }

  /**
   * Given a zone, sign it using experimental Opt-In NSEC records (see RFC
   * 4956).
   * 
   * @param zonename
   *          the name of the zone.
   * @param records
   *          the records comprising the zone. They do not have to be in any
   *          particular order, as this method will order them as necessary.
   * @param kskpairs
   *          the key pairs that are designated as "key signing keys".
   * @param zskpairs
   *          this key pairs that are designated as "zone signing keys".
   * @param start
   *          the RRSIG inception time.
   * @param expire
   *          the RRSIG expiration time.
   * @param useConservativeOptIn
   *          if true, Opt-In NSEC records will only be generated if there are
   *          insecure, unsigned delegations in the span.
   * @param fullySignKeyset
   *          sign the zone apex keyset with all available keys.
   * @param ds_digest_alg
   *          The digest algorithm to use when generating DS records.
   * @param NSECIncludeNames
   *          names that are to be included in the NSEC chain regardless. This
   *          may be null.
   * @return an ordered list of {@link org.xbill.DNS.Record} objects,
   *         representing the signed zone.
   */
  public List<Record> signZoneOptIn(Name zonename, List<Record> records,
                                    List<DnsKeyPair> kskpairs, List<DnsKeyPair> zskpairs,
                                    Date start, Date expire,
                                    boolean useConservativeOptIn,
                                    boolean fullySignKeyset, List<Name> NSECIncludeNames,
                                    int ds_digest_alg) throws IOException,
      GeneralSecurityException
  {

    return signZone(zonename, records, kskpairs, zskpairs, start, expire,
                    fullySignKeyset, ds_digest_alg, NSEC_EXP_OPT_IN, NSECIncludeNames,
                    null, 0, 0, useConservativeOptIn);
  }
}
