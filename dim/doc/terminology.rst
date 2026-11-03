.. _terminology:

Terminology: DIM vs. RFC and RIR usage
======================================

Network engineers read DIM output with the vocabulary of the RFCs and of the
Regional Internet Registries (RIRs) in mind. Several words mean something
different in DIM. This page maps every DIM term to the closest standard term,
quotes the source and states where DIM deviates.

Three kinds of source are used and are always labelled as such:

- **RFC** - IETF documents. Quotes are verbatim, with section numbers.
- **RIPE (not RFC)** - policy documents of the RIPE NCC, the RIR for Europe.
  They define how address space is distributed (allocate, assign,
  sub-allocate) and are not IETF standards.
- **DIM (not RFC)** - concepts that exist only in DIM. There is no standard
  definition to compare against.


Quick reference
---------------

.. list-table::
   :header-rows: 1
   :widths: 14 22 24 40

   * - DIM term
     - Closest standard term
     - Source
     - Where DIM differs
   * - ipblock
     - prefix, address block
     - RFC 4632 3.1, RFC 8415 4.1
     - Same idea. Every object in the DIM IP tree is an ipblock with a status.
   * - Container
     - aggregate; allocation, LIR-PARTITIONED PA
     - RFC 4632 4.1; RIPE-826 7.0 (not RFC)
     - Documentation and structure only. A Container says nothing about
       whether the block is announced or aggregated in routing.
   * - Subnet
     - subnet, subnet prefix
     - RFC 950 App. III, RFC 4291 2
     - Only matches the RFC when the pool is a *static* pool (prefix on a
       router interface). In a *dynamic* pool a Subnet is a range of
       BGP-announceable service addresses, not a link. DIM cannot tell the two
       apart, see below.
   * - Pool
     - none
     - DIM (not RFC)
     - A named set of Subnets used for automated provisioning.
   * - Delegation
     - delegated prefix (DHCPv6 prefix delegation)
     - RFC 8415 1 (closest match only)
     - A block cut out of a Subnet of a pool and handed to one consumer.
       Not the same as DNS delegation (RFC 1034) or RIPE sub-allocation.
   * - Static
     - assigned (host) address
     - DIM (not RFC)
     - One used address (/32 or /128). DIM "Static" is not RIPE "ASSIGNED".
   * - Reserved
     - all-zeros / all-ones host values
     - RFC 950 2.1
     - DIM also reserves the .0 and .255 addresses inside larger IPv4
       subnets and the subnet-router address in IPv6 by default.
   * - Available
     - free address space
     - DIM (not RFC)
     - Unused space shown by ``list containers`` and ``list ips``.
   * - layer3domain
     - VRF, routing table
     - RFC 4364 3
     - One DIM layer3domain documents one routing table (global table, VRF or
       local table). Pool names are unique across all layer3domains.
   * - allocate (``get ip``, ``allocate`` right)
     - assign
     - RIPE-738 2.5, 2.6 (not RFC)
     - DIM says "allocate" for handing one address to one host. In RIPE
       terms that is an *assignment*; an *allocation* goes to a registry.


Prefix and ipblock
------------------

**RFC.** RFC 4632, Section 3:

    "The solution that the community created was to deprecate the Class
    A/B/C network address assignment system in favor of using "classless",
    hierarchical blocks of IP addresses (referred to as prefixes)."

RFC 4632, Section 3.1:

    "prefixes may be defined to describe any power of two-sized block of
    between one and 2^32 end system addresses."

RFC 8415, Section 4.1, defines

- prefix: "The initial bits of an address, or a set of IP addresses that
  share the same initial bits."
- prefix length: "The number of bits in a prefix."

RFC 7608 (Abstract) adds for IPv6 that the prefix length "may be any number
from zero to 128".

**DIM.** An ipblock is exactly such a prefix (``10.0.0.0/8``,
``2001:db8::/32``), stored with its layer3domain and a status (Container,
Subnet, Delegation, Static, Reserved, Available). ipblocks form a tree: every
block has the smallest enclosing block as its parent. Within one layer3domain
an address, prefix length and IP version can exist only once (see "Same size"
below).


Container and aggregate
-----------------------

**RFC.** RFC 4632, Section 4.1:

    "The only commonly understood method for reducing routing state on a
    packet-switched network is through aggregation of information."

    "the service provider advertises a single aggregate route for the larger
    block. This advertisement provides reachability and routeability for all
    the customers numbered in the block."

**RIPE (not RFC).** RIPE-826 (IPv4 policy), Section 3.0:

    "Aggregation: Distributing IPv4 addresses in an hierarchical manner
    permits the aggregation of routing information."

RIPE-738 (IPv6 policy), Section 2.5:

    "To "allocate" means to distribute address space to IRs for the purpose
    of subsequent distribution by them."

RIPE-826, Section 7.0, status ``LIR-PARTITIONED PA``:

    "This allows an LIR to document distribution and delegate management of
    allocated space within their organisation. Address space with a status
    of LIR-PARTITIONED is not considered used."

**DIM.** A Container structures address space. It is the closest thing to an
RIR allocation or an LIR-PARTITIONED block: it documents who owns the space
(attributes such as ``RIR``, ``autonomoussystem``), how it is divided and which
reverse zone profile applies to subnets created below it. Containers can be
nested.

**Deviation.** A Container is **not** a statement about routing. DIM does not
know whether a Container is announced as an aggregate, announced as
more-specifics or not announced at all. Do not derive prefix lists or
aggregate routes from Containers.


Subnet
------

**RFC.** RFC 950, Appendix III (Glossary):

    "Subnet: One or more physical networks forming a subset of an Internet
    network. A subnet is explicitly identified in the Internet address."

RFC 4291, Section 2:

    "When it is used with the term "prefix" (e.g., "subnet prefix"), it
    refers to all of the address from the left up to and including this
    field."

RFC 4291, Section 2.1:

    "Currently, IPv6 continues the IPv4 model in that a subnet prefix is
    associated with one link. Multiple subnet prefixes may be assigned to
    the same link."

**DIM.** A Subnet is an ipblock that is a member of a pool. A Subnet holds
Delegations, Static, Reserved and Available addresses. When a Subnet is
created, DIM creates the matching reverse zones. A Subnet can carry a
``gateway``.

**Deviation.** In the RFC a subnet is always a link. In DIM it is a link only
when the pool is a *static* pool. See "Pools and Subnets: the IPv4 crutch"
below.


Pool
----

**DIM (not RFC).** There is no RFC or RIPE term for a pool. A pool is a named
set of Subnets in one layer3domain and one IP version, with an optional VLAN.
Pool names are unique across all layer3domains.

Pools exist to decouple automated provisioning from real prefixes. A
provisioning process only knows the pool name and runs ``get ip`` or
``get delegation`` against it. Network administrators add Subnets to and
remove Subnets from the pool as it grows or shrinks; the process does not
change.


Delegation
----------

**DIM (not RFC).** A Delegation is a block cut out of a Subnet of a pool and
handed to one consumer, for example:

- a /56 per customer from a /44 Subnet (IPv6),
- a block of consecutive IPv4 addresses for one service that announces them
  with BGP.

It is created with ``ndcli modify pool <pool> get delegation <prefixlen>`` or
marked by hand with ``mark delegation``. The pool attribute
``assignmentsize`` sets the size ``list pool`` uses to count free and total
blocks. A Delegation can hold Static addresses.

**Closest RFC term.** RFC 8415 (DHCPv6), Section 1:

    "DHCPv6 also provides a mechanism for automated delegation of IPv6
    prefixes using DHCPv6, as originally specified in [RFC3633]. Through
    this mechanism, a delegating router can delegate prefixes to requesting
    routers."

A DIM Delegation is the IPAM record of the same idea: a prefix taken from a
larger pool and given to one consumer. DIM does not speak DHCPv6, and a DIM
Delegation does not need DHCPv6.

**Not to be confused with**

- **DNS delegation** (RFC 1034): handing authority for a part of the name
  space to other name servers with NS records. Unrelated to ipblocks.
- **RIPE sub-allocation** (RIPE-826, Section 5.3): address space an LIR
  passes to a downstream network operator "that will make assignments from
  it". A DIM Delegation is used by its consumer, not further distributed.

**What network engineers need to know.** A Delegation is always inside a
Subnet, and that Subnet is always in a pool. Delegations are never configured
on an interface by DIM and never have a gateway. If a Delegation shows up in
a prefix list or on a router, it is because a consumer announces it, usually
with BGP.


Pools and Subnets: the IPv4 crutch
----------------------------------

**DIM (not RFC).** DIM has exactly one kind of pool. In practice pools are
used for two different things:

.. list-table::
   :header-rows: 1
   :widths: 20 40 40

   * -
     - static pool
     - dynamic pool
   * - What the Subnet is
     - a prefix configured on a router interface (IRB, VLAN interface,
       routed link)
     - a range of service addresses that may be announced with BGP
   * - RFC meaning of "subnet"
     - yes, a link
     - no, not a link
   * - gateway
     - set, documents the router address for sysadmins and DHCP
     - not set
   * - network / broadcast reserved
     - yes (IPv4), unless ``--no-default-reserve``
     - should not be; every address is usable
   * - should match
     - the interface configuration
     - the prefix lists of the BGP sessions

For IPv6 the difference matters less: a link is a /64 and service addresses
come from separate prefixes anyway. For IPv4 it is a crutch: DIM cannot record
which of the two a pool is. The pool name, the ``gateway`` and the reserved
addresses are the only hints. Consequences:

- A Subnet in DIM does **not** prove that the prefix is configured on an
  interface.
- A dynamic Subnet has no gateway and usually no Reserved addresses.
- When you build prefix lists for BGP, take the Subnets of the dynamic pools,
  not the Containers.


Static, Reserved and Available
------------------------------

**DIM (not RFC).** ``Static`` is a single used address (/32 or /128),
created by ``get ip``, ``mark ip`` or ``create rr ... a/aaaa``. A Static
address created by ``create rr`` may also exist outside of any pool, directly
below a Container.
``Available`` is free space. ``Reserved`` is an address DIM must not hand
out.

**RFC.** RFC 950, Section 2.1:

    "This means the values of all zeros and all ones in the subnet field
    should not be assigned to actual (physical) subnets."

RFC 4291, Section 2.6.1 (Subnet-Router anycast address):

    "This anycast address is syntactically the same as a unicast address for
    an interface on the link with the interface identifier set to zero."

    "Packets sent to the Subnet-Router anycast address will be delivered to
    one router on the subnet."


**DIM default when a Subnet is added to a pool**

- IPv4: the network and broadcast address are Reserved, and so is every
  ``.0`` and ``.255`` address inside the Subnet ("because some buggy routers
  have problems with them"). ``--no-reserve-class-c-boundaries`` limits this
  to the first and last address, ``--no-default-reserve`` turns it off.
- IPv6: the Subnet-Router anycast address (interface ID all zeros) is
  Reserved.

**Deviations**

- RFC 3021 allows /31 on point-to-point links, where both addresses are
  usable. DIM still reserves both addresses of a /31 by default.
- RFC 6164, Section 6: "Routers MUST disable Subnet-Router anycast for the
  prefix when /127 prefixes are used." DIM still reserves the Subnet-Router
  anycast address of a /127, which is one of its two addresses.
- Add /31 and /127 Subnets with ``--no-default-reserve``.
- The ``gateway`` of a Subnet is an attribute, not an address DIM keeps
  free. ``get ip`` and ``create rr ... from POOL`` hand it out like any other
  free address, because DIM does not know the gateway convention of a network
  (first address, last address, .10, ...).
- Network engineers register the DNS names of their interface and VRRP
  addresses with ``create rr``. That makes these addresses Static, and
  automatic allocation skips them - for single addresses (``get ip``) and for
  Delegations (``get delegation``) alike.


layer3domain and VRF
--------------------

**RFC.** RFC 4364, Section 3:

    "Each PE router maintains a number of separate forwarding tables. One of
    the forwarding tables is the "default forwarding table". The others are
    "VPN Routing and Forwarding tables", or "VRFs"."

**DIM.** A layer3domain documents one routing table: a global table of an AS,
a VRF or a local table. Overlapping address space is only allowed between
layer3domains, never inside one. The layer3domain ``default`` is the 8560
global table.

**Deviation.** DIM does not model route leaking, import/export or NAT between
layer3domains. When the same prefix exists in two layer3domains, every command
that refers to an address must say which layer3domain it means
(``layer3domain <name>`` or ``layer3domain=`` in ``~/.ndclirc``).


allocate and assign
-------------------

**RIPE (not RFC).** RIPE-738, Section 2.5 and 2.6:

    "To "allocate" means to distribute address space to IRs for the purpose
    of subsequent distribution by them."

    "To "assign" means to delegate address space to an ISP or End User for
    specific use within the Internet infrastructure they operate."

**DIM.** DIM uses "allocate" for handing out an address or a Delegation to a
consumer (``get ip``, the ``allocate`` right on a pool). In RIPE terms that is
an *assignment*. When you talk to the RIPE NCC or the LIR team, translate.


Same size
---------

**DIM.** Inside one layer3domain an address, prefix length and IP version can
exist only once, in any status. A /24 Container and a /24 Subnet with the same
address are therefore impossible in the released version; the workaround is
two /25 Subnets in a /24 Container.

A change that allows exactly this pair - a Subnet with the size of its
Container, the Container being its parent - is implemented and in review
(ITOUDP-5900). Everything else stays forbidden at the same size: two
Containers, two Subnets, a Delegation of the size of its Subnet. Containers and
Subnets with a single address (/32, /128) are rejected, and so is a Subnet
without a Container around it.


Sources
-------

RFC (IETF):

- RFC 950 - Internet Standard Subnetting Procedure
- RFC 1034 - Domain Names - Concepts and Facilities
- RFC 3021 - Using 31-Bit Prefixes on IPv4 Point-to-Point Links
- RFC 4291 - IP Version 6 Addressing Architecture
- RFC 4364 - BGP/MPLS IP Virtual Private Networks (VPNs)
- RFC 4632 - Classless Inter-domain Routing (CIDR)
- RFC 6164 - Using 127-Bit IPv6 Prefixes on Inter-Router Links
- RFC 7608 - IPv6 Prefix Length Recommendation for Forwarding
- RFC 8415 - Dynamic Host Configuration Protocol for IPv6 (DHCPv6)

RIPE NCC (not RFC):

- RIPE-826 - IPv4 Address Allocation and Assignment Policies for the RIPE NCC
  Service Region (20 Jun 2024), https://www.ripe.net/publications/docs/ipv4-policies/
- RIPE-738 - IPv6 Address Allocation and Assignment Policy (16 Mar 2020),
  https://www.ripe.net/publications/docs/ipv6-policy/
