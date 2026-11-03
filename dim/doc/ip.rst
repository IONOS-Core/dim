.. _introduction:

IP
===

Ipblock
-------

Dim stores information about the IP address space in a tree structure. An
individual node from this tree is called Ipblock (or simply block) and it
represents a CIDR-like block of IP addresses (ex: ``10.0.0.0/8``). The children
Ipblocks represent a subset of the parent address space.

How these terms relate to the RFC and RIPE vocabulary network engineers use
is described in :ref:`terminology`.

Each block can have a status value associated. The most interesting status
values are:

- Container: a generic status for blocks larger than subnets
- Subnet: a subnet (can only have Delegation, Static, Reserved or Available children)
- Delegation: the block is used for a specific purpose (ex: a server)
- Static: a single allocated IP address
- Available: a single free IP address
- Reserved: a reserved single IP address (for example the IPv4 network and broadcast addresses in a Subnet)

Example IPv6 tree:

.. digraph:: ipblock_tree_example

   graph [rankdir=TB];
   node [fontsize=10 shape=box];
   C1 [label="2001:8d8::/32\nContainer\nUI RIPE Space"];
   C2 [label="2001:8d8:fe::/47\nContainer\nRIPE Global Anycast"];
   S [label="2001:8d8:fe:53::/64\nSubnet\nGlobal DNS Anycast"];
   D1 [label="2001:8d8:fe:53:0:d9a0:5000::/104\nDelegation\nnspa-de"];
   Static1 [label="2001:8d8:fe:53:0:d9a0:50c7:100/128\nStatic\a.de"];
   D2 [label="2001:8d8:fe:53:0:d9a0:5200::/104\nDelegation\nnspa-com"];
   Static2 [label="2001:8d8:fe:53:0:d9a0:52c7:100/128\nStatic\a.com"];

   C1 -> C2;
   C2 -> S;
   S -> D1;
   D1 -> Static1;
   S -> D2;
   D2 -> Static2;


Pool
------

A pool is a collection of Subnet blocks. All subnets must be in the same VLAN.


Custom Attribute
----------------

It is possible to attach custom attributes to pool and Ipblock objects. Both
the attribute name and value are free-form strings. The only restriction is that
the attribute name must not be reserved (for example, ``name`` and ``vlan`` are
reserved names for pools).
