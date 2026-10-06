/*
 * $Id$
 * 
 * Copyright (c) 2005 VeriSign. All rights reserved.
 * 
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 * 
 * 1. Redistributions of source code must retain the above copyright notice,
 * this list of conditions and the following disclaimer. 2. Redistributions in
 * binary form must reproduce the above copyright notice, this list of
 * conditions and the following disclaimer in the documentation and/or other
 * materials provided with the distribution. 3. The name of the author may not
 * be used to endorse or promote products derived from this software without
 * specific prior written permission.
 * 
 * THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS OR
 * IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES
 * OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN
 * NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
 * SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED
 * TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
 * PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
 * LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
 * NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
 * SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 *  
 */

package com.verisignlabs.dnssec.security;

import org.xbill.DNS.*;
import org.xbill.DNS.Record;
import org.xbill.DNS.utils.base16;
import org.xbill.DNS.utils.base32;

/**
 * This is a class representing a "prototype NSEC3" resource record. These are
 * used as an intermediate stage (in zone signing) between determining the list
 * of NSEC3 records and forming them into a viable chain.
 * 
 * @author David Blacka (original)
 * @author $Author: davidb $
 * @version $Revision: 183 $
 */
public class ProtoNSEC3
{
  private Name                originalOwner;
  private int                 hashAlg;
  private byte                flags;
  private int                 iterations;
  private byte[]              salt;
  private byte[]              next;
  private byte[]              owner;       // cached numerical owner value.
  private TypeMap             typemap;
  private Name                zone;
  private Name                name;
  private int                 dclass;
  private long                ttl;

  private static final base32 b32 = new base32(base32.Alphabet.BASE32HEX, false, false);

  /**
   * Creates an NSEC3 Record from the given data.
   */
  public ProtoNSEC3(byte[] owner, Name originalOwner, Name zone, int dclass, long ttl,
                    int hashAlg, byte flags, int iterations, byte[] salt, byte[] next,
                    TypeMap typemap)
  {
    this.zone = zone;
    this.owner = owner;
    this.dclass = dclass;
    this.ttl = ttl;
    this.hashAlg = hashAlg;
    this.flags = flags;
    this.iterations = iterations;
    this.salt = salt;
    this.next = next;
    this.typemap = typemap;
    this.originalOwner = originalOwner;
  }

  public ProtoNSEC3(byte[] owner, Name originalOwner, Name zone, int dclass, long ttl,
                    int hashAlg, byte flags, int iterations, byte[] salt, byte[] next,
                    int[] types)
  {
    this(owner, originalOwner, zone, dclass, ttl, hashAlg, flags, iterations, salt, next,
         TypeMap.fromTypes(types));
  }

  private String hashToString(byte[] hash)
  {
    if (hash == null) return null;
    return b32.toString(hash);
  }

  public Name getName()
  {
    if (name == null)
    {
      try
      {
        name = new Name(hashToString(owner), zone);
      }
      catch (TextParseException e)
      {
        // This isn't going to happen.
      }
    }

    return name;
  }

  public byte[] getNext()
  {
    return next;
  }

  public void setNext(byte[] next)
  {
    this.next = next;
  }

  public byte getFlags()
  {
    return flags;
  }

  public boolean getOptOutFlag()
  {
    return (flags & NSEC3Record.Flags.OPT_OUT) != 0;
  }

  public void setOptOutFlag(boolean optOutFlag)
  {
    if (optOutFlag)
      this.flags |= NSEC3Record.Flags.OPT_OUT;
    else
      this.flags &= ~NSEC3Record.Flags.OPT_OUT;
  }

  public long getTTL()
  {
    return ttl;
  }

  public void setTTL(long ttl)
  {
    this.ttl = ttl;
  }

  public TypeMap getTypemap()
  {
    return typemap;
  }

  public int[] getTypes()
  {
    return typemap.getTypes();
  }

  public void setTypemap(TypeMap typemap)
  {
    this.typemap = typemap;
  }

  public int getDClass()
  {
    return dclass;
  }

  public int getHashAlgorithm()
  {
    return hashAlg;
  }

  public int getIterations()
  {
    return iterations;
  }

  public byte[] getOwner()
  {
    return owner;
  }

  public byte[] getSalt()
  {
    return salt;
  }

  public Name getZone()
  {
    return zone;
  }

  public NSEC3Record getNSEC3Record()
  {
    String comment = (originalOwner == null) ? "(unknown original ownername)"
                                            : originalOwner.toString();
    return new NSEC3Record(getName(), dclass, ttl, hashAlg, flags, iterations, salt,
                           next, getTypes(), comment);
  }

  public void mergeTypes(TypeMap new_types)
  {
    int[] nt = new_types.getTypes();
    for (int i = 0; i < nt.length; i++)
    {
      if (!typemap.get(nt[i])) typemap.set(nt[i]);
    }
  }

  public int compareTo(ProtoNSEC3 o)
  {
    if (o == null) return 1;
    byte[] o_owner = o.getOwner();
    int len = owner.length < o_owner.length ? o_owner.length : owner.length;
    for (int i = 0; i < len; i++)
    {
      int d = ((owner[i] & 0xFF) - (o_owner[i] & 0xFF));
      if (d != 0) return d;
    }
    return owner.length - o_owner.length;
  }

  public String toString()
  {
    StringBuffer sb = new StringBuffer();
    sb.append(getName());
    sb.append(' ');
    sb.append(ttl);
    sb.append(' ');
    sb.append(DClass.string(dclass));
    sb.append(" NSEC3 ");
    sb.append(flags);
    sb.append(' ');
    sb.append(hashAlg);
    sb.append(' ');
    sb.append(iterations);
    sb.append(' ');
    sb.append(salt == null ? "-" : base16.toString(salt));
    sb.append(' ');
    String nextstr = (next == null) ? "(null)" : b32.toString(next);
    sb.append(nextstr);
    sb.append(' ');
    sb.append(typemap.toString());

    return sb.toString();
  }

  public static class Comparator implements java.util.Comparator<ProtoNSEC3>
  {
    public int compare(ProtoNSEC3 a, ProtoNSEC3 b)
    {
      return a.compareTo(b);
    }

  }
}
