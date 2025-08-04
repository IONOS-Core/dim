.. _api:

API
===

Conventions
-----------

Most of the functions have a last parameter called *options*. If present, it
must be an object with name:value pairs.

Global Options
~~~~~~~~~~~~~~

In addition to the options documented separately for each function, all
functions also accept the following options:

- *dryrun*: roll back the changes made to the database during this function call


API Examples
~~~~~~~~~~~~

The examples in this page are formatted the same way they appear in ``ndcli
--debug`` output. That is, for the JSON-RPC request::

  {"jsonrpc":"2.0","id":null,"method":"zone_list","params":[{"pattern": "a*"}]}

the examples will list the call as::

  zone_list({'pattern': 'a*'})


.. _check-options:

Check Options
~~~~~~~~~~~~~

Some functions share a common list of options which can be used to specify
additional checks for the CIDR argument they receive:

- *pool*: check if CIDR is inside this pool
- *delegation*: check if CIDR is inside this delegation
- *host*: if true, check if CIDR is a host address (prefix 32 for IPv4 or 128
  for IPv6)
- *status*: check if CIDR is a block with this status


.. _messages:

Messages
~~~~~~~~

Some functions return a list of messages explaining the actions taken by the
server. The messages are usually returned in a field called *messages*. Each
message contains the severity and a string. For example::

  {'messages': [[30, 'Creating zone test.com without profile'],
                [30, 'Primary NS for this Domain is now localhost.']]}

The severity values have the following meaning:

- 10: DEBUG
- 20: INFO
- 25: DNS record
- 30: WARNING
- 40: ERROR

.. _soa_attributes:

SOA Attributes
~~~~~~~~~~~~~~

An object containing values for SOA fields; these values will override the
defaults or the values from *from_profile*.

Example::

       {
           'primary': 'ns.test.com.',
           'mail': 'admin.test.com.',
           'serial': '101',
           'ttl': '3600'
       }


.. _view_option:

View Option
~~~~~~~~~~~

Some functions take a *view* option. As explained in :ref:`dns_concepts`, if the
zone only has one view, this option can be omitted. If the zone has multiple
views, this option is required.


.. _profile_option:

Profile Option
~~~~~~~~~~~~~~

If *profile* option is true, the function will operate on zone profiles instead
of zones.

.. _rr_options:

RR Options
~~~~~~~~~~

When creating or specifying a RR, the following options are available:

- *name* (string): the fqdn of the RR or the relative name if *zone* was
  specified; it can be omitted if *type* is PTR and the *ip* is specified
- *type* (string): one of the supported RR types (A, AAAA, PTR, CNAME, DNAME, MX, NS,
  SRV, TXT, SPF, RP, CERT, HINFO, NAPTR)
- :ref:`layer3domain_option`. The layer3domain value is optional when specifying a RR
  if there is only one RR with that name, type and value.

In addition to *name* and *type*, the right side of a RR (the value) is
specified differently for each type:

- A/AAAA: *ip*
- PTR: *ptrdname*
- CNAME: *cname*
- DNAME: *target*
- MX: *preference*, *exchange*
- NS: *nsdname*
- SRV: *priority*, *weight*, *port*, *target*
- TXT/SPF: *strings* (can be either a list of strings or a single string
  containing quoted strings)
- RP: *mbox*, *txtdname*
- CERT: *certificate_type*, *key_tag*, *algorithm*, *certificate*
- HINFO: *cpu*, *os*
- NAPTR: *order*, *preference*, *flags*, *service*, *regexp*, *replacement*

The RR value is optional when specifying a RR if there are no other RRs with
that name and type.


.. _layer3domain_option:

layer3domain Option
~~~~~~~~~~~~~~~~~~~

Specifies the name of the layer3domain the ipblock/ippool lives in. Optional if only one layer3domain exists.


.. _allow_overlap_option:

allow_overlap Option
~~~~~~~~~~~~~~~~~~~~

When true, allows creating *cidr* even when *cidr* already exists in another layer3domain.
However, this will only be permitted if *cird* is inside one of the whitelisted subnets that allow overlapping
(configured by DIM admins).


General Functions
-----------------

.. function:: protocol_version() -> integer

   Returns the server's protocol version. The client should check this before
   attempting to call any other function.

.. function:: server_info() -> object

   Returns informations about the server:

   - version
   - host
   - os
   - python version
   - db uri
   - configuration variables prefixed with ``SERVER_INFO_``

.. function:: get_username() -> string

   Returns the currently logged-in user name.


Layer3domain Functions
----------------------
.. function:: layer3domain_create(name, type[, options])

   Create a layer3domain. *type* can only be ``vrf``

   Valid *options*:

   - *comment* (string)

   Options for type ``vrf``:

   - *rd* (string)

.. function:: layer3domain_list() -> array of objects

   Returns the list of layer3domains.

.. function:: layer3domain_delete(layer3domain)

   Delete the layer3domain *layer3domain*.

.. function:: layer3domain_set_comment(layer3domain, comment)

   Set the layer3domain comment.

.. function:: layer3domain_get_attrs(layer3domain) -> object

   Return the layer3domain attributes.

.. function:: layer3domain_set_attrs(layer3domain[, options])

   Set the layer3domain attributes.

   Options for type ``vrf``:

   - *rd* (string)

.. function:: layer3domain_rename(old_name, new_name)

   Rename the layer3domain named *old_name* to *new_name*.


Pool Functions
--------------

.. function:: ippool_create(name[, options])

   Create a pool.

   Valid *options*:

   - *vlan* (integer): VLAN id
   - *attributes* (object): custom attributes
   - :ref:`layer3domain_option`


.. function:: ippool_delete(pool[, options]) -> integer

   Delete the pool *pool*. By default, a pool is not deleted if it contains
   any subnets.

   Valid *options*:

   - *force*: delete the pool even if it has subnets
   - *delete_subnets*: also delete the subnets if *force* was specified

   Returns 1 if the pool was deleted, 0 if the pool had subnets and *force*
   was not specified.


.. function:: ippool_rename(old_name, new_name)

   Rename the pool named *old_name* to *new_name*.


.. function:: ippool_get_attrs(pool) -> object

   Returns the list of attributes for *pool*.


.. function:: ippool_set_attrs(pool, attributes)

   Sets custom attributes for *pool*. *attributes* must be an object.


.. function:: ippool_delete_attrs(pool, attributes)

   Removes custom attributes from *pool*. *attributes* must be an array of
   attribute names.


.. function:: ippool_set_vlan(pool, vlan)

   Sets the vlan for *pool* and all its subnets.


.. function:: ippool_remove_vlan(pool)

   Remove the vlan from *pool* and all its subnets.


.. function:: ippool_set_layer3domain(pool, layer3domain)

   Moves *pool* from its origin layer3domain to *layer3domain*.
   Subnets of the pool, the contained entries and delegations are also being moved to layer3domain.
   Reverse entries are stored in views named after the layer3domain.
   All reverse entries are being deleted from the old view and then created in the new view.
   If the view does not exist, it will be created.

   **Please remember to add eventually newly created views to their appropriate zone-profile.**
   Forward entries will be fixed to keep their reference to the reverse record.

   **This operation can take a long time** to succeed depending on
   the number of existing subnets, IPs, forward DNS entries and reverse DNS entries.
   During this time, the pool and its entries can't be modified.


.. function:: ippool_get_access(pool) -> array of objects

   Returns a list of access rights. Each access right has the following properties:

   - *access*: string representing an access right
   - *object*: name of the object on which the right is granted
   - *group*: group for which the access right applies


.. function:: ippool_get_subnets(pool[, options]) -> array of objects

   Returns the list of subnets from *pool* sorted by priority. Each subnet is
   represented by an object with the following members:

   - *priority*
   - *subnet*
   - *gateway*
   - *free*: the number of free IPs in this subnet
   - *static*: the number of static IPs in this subnet
   - *total*: the total number of IPs in this subnet (including reserved IPs)

   Valid *options*:

   - *full*: expand IPv6 addresses
   - *include_usage*: whether to include the *free*, *static* and *total*
     fields in the result

.. function:: ippool_get_delegations(pool[, options]) -> array of objects

   Returns the list of delegations from *pool*. Each delegation is represented
   by an object with the following members:

   - *delegation*
   - *free*: the number of free IPs in this subnet
   - *total*: the total number of IPs in this subnet (including reserved IPs)

   Valid *options*:

   - *full*: expand IPv6 addresses
   - *include_usage*: whether to include the *free* and *total* fields in the
     result


.. function:: ippool_add_subnet(pool, cidr[, options]) -> integer

   Adds the subnet *cidr* to *pool*. The subnet is created if necessary and also
   creates entries for reserved IP addresses.

   Valid *options*:

   - *attributes*: object with name:value pairs for attributes
   - *gateway*
   - *allow_move*: allow the subnet to be added to *pool* even if it is part
     of another pool
   - *dont_reserve_network_broadcast*: the network and broadcast addresses for
     subnet are not reserved.
   - :ref:`allow_overlap_option`
   - *include_messages*: also include :ref:`messages` in the return value; the number
     of subnets created will become a field named *created*

   Returns the number of new Subnet blocks created (0 or 1).


.. function:: ippool_get_ip(pool[, options]) -> object

   Allocates a single IP address from *pool* (sets its status to Static). This
   function respects subnet priorities.

   Valid *options*:

   - *attributes*: attributes to be set for the allocated IP.
   - *full*: expand IPv6 addresses

   Returns the result of :func:`ipblock_get_attrs` for the allocated IP.


.. function:: ippool_get_delegation(pool, prefix[, options]) -> array of objects

   Allocates one or more delegations from *pool* that have combined the same
   number of IP addresses as a single block with the prefix *prefix*.

   Valid *options*:

   - *maxsplit*: how much the prefix can be increased during the search for free
     blocks (the returned delegation will have a maximum prefix equal to
     *prefix* + *maxsplit*)
   - *attributes*: attributes to be set for the allocated delegations
   - *full*: expand IPv6 addresses

   Returns an array of results from :func:`ipblock_get_attrs` for each allocated
   delegation.


.. function:: ippool_unset_owner(poolname)
   Unset owner for pool.


.. function:: ippool_list(pool[, options]) -> array of objects

   Returns the list of pools matching the criteria specified in *options*. Each
   pool is an object with the following fields:

   - *name*
   - *vlan*
   - *subnets* (array of strings): list of CIDRs (one for each subnet)
   - *layer3domain*

   Valid *options*:

   - *limit*: limit the amount of results
   - *offset*: skip the first *offset* results
   - *pool*: selects only pools which match this shell-like wildcard pattern
   - *vlan*: selects only pools which are in *vlan*
   - *cidr*: selects only pools which contain blocks inside *cidr*
   - *owner*: selects only pools owner by user group *owner*
   - *favorite_only* (boolean): return only favorite pools
   - *full*: expand IPv6 addresses
   - *include_subnets*: whether to include the *subnets* field in the response
   - *can_allocate*: whether to include only pools with the allocate right for the current user
   - *fields*: if true, add a *can_allocate* field to each object returned
   - *layer3domain*: selects only pools which are in *layer3domain*

   The options *pool*, *vlan*, *cidr* and *owner* are mutually exclusive. If none is
   specified, all pools are returned.


.. function:: ippool_count(pool[, options]) -> integer

   Returns the number of pools matching the criteria specified in *options*. Valid *options*:

   - *pool*
   - *vlan*
   - *cidr*
   - *can_allocate*
   - *owner*
   - *layer3domain*

   The options have the same meaning as for :func:`ippool_list`.


.. function:: ippool_favorite(pool) -> boolean

   Returns true if the pool is favorited by the current user.


.. function:: ippool_favorite_add(pool)

   Mark pool as favorite for the current user.


.. function:: ippool_favorite_remove(pool)

   Remove pool as a favorite of the current user.


Block Functions
---------------

.. note::

   The following functions apply to Subnet, Container, Delegation blocks and
   individual IPs.

.. function:: ipblock_create(cidr[, options]) -> object

   Creates a new block.

   Valid *options*:

   - *status*
   - *attributes*: object with name:value pairs for attributes
   - *disallow_children*: if true, return an error if *cidr* has children
   - :ref:`allow_overlap_option`
   - :ref:`layer3domain_option`
   - :ref:`check-options`

   Returns the attributes.


.. function:: ipblock_remove(cidr[, options])

   Removes the block identified by *cidr*. An error is raised if *force* is
   not specified and the block has children.

   Valid *options*:

   - *force*: if true, remove the block even if it still has children
     (**Reserved** children are ignored for the purposes of this option)
   - *recursive*: if true, recursively remove its children blocks too
   - *include_messages*: also include :ref:`messages` in the return value
   - :ref:`layer3domain_option`
   - :ref:`check-options`


.. function:: ipblock_get_attrs(cidr[, options]) -> object

   Returns an object with any custom attributes and the following system
   attributes:

   - *ip*: the cannonical representation of *cidr*
   - *status*
   - *delegation*: the CIDR of the ancestor with the Delegation status (if
      available)

   If *cidr* is part of a subnet, the following are added:

   - *subnet*, *mask* (for IPv4) or *prefixlength* (for IPv6)
   - *pool*: if the subnet is part of any pool
   - *gateway*: the gateway of the subnet

   Valid *options*:

   - *full*: expand IPv6 addresses
   - :ref:`layer3domain_option`
   - :ref:`check-options`


.. function:: ipblock_set_attrs(cidr, attributes[, options])

   Sets custom attributes for the block identified by *cidr*. *attributes* must be an object.

   Valid *options*:

   - :ref:`layer3domain_option`
   - :ref:`check-options`


.. function:: ipblock_delete_attrs(cidr, attributes[, options])

   Deletes custom attributes for *pool*. *attributes* must be a list of strings.

   Valid *options*:

   - :ref:`layer3domain_option`
   - :ref:`check-options`


.. function:: ipblock_get_ip(cidr[, options])

   Allocates a single IP address from *cidr* (sets its status to Static).

   Valid *options*:

   - :ref:`check-options`
   - :ref:`layer3domain_option`
   - *attributes*: attributes to be set for the allocated IP.
   - *full*: expand IPv6 addresses

   Returns the result of :func:`ipblock_get_attrs` for the allocated IP.


.. function:: ipblock_get_delegation(cidr, prefix[, options]) -> array of objects

   Allocates one or more delegations from *cidr* that have combined the same
   number of IP addresses as a single block with the prefix *prefix*.

   Valid *options*:

   - *maxsplit*: how much the prefix can be increased during the search for free
     blocks (the returned delegation will have a maximum prefix equal to
     *prefix* + *maxsplit*)
   - *attributes*: attributes to be set for the allocated delegations
   - *full*: expand IPv6 addresses
   - :ref:`layer3domain_option`

   Returns an array of results from :func:`ipblock_get_attrs` for each allocated
   delegation.


.. function:: ipblock_move_to(cidr, block, layer3domain, to_layer3domain[, options])

   Moves a `container` from *layer3domain* to the layer3domain *to_layer3domain*.
   IP addresses that are not part of a pool are moved, too.
   The container must have a parent. All `subnets` contained in the container get reassigned to the parent container.
   If not present, new views for the affected reverse zones will be created with the name of the layer3domain to_layer3domain.
   Entries for the IP addresses will then be deleted in the old view and recreated in the new view.

   **This operation can take a long time** to succeed depending on the size of the container,
   the number of subnets and the number of IPs allocated in the container.
   In this time any write actions against the container,
   subnets or reverse zones may get blocked at the database level.


Subnet Functions
----------------

.. function:: subnet_set_priority(cidr, priority[, options])

   Sets the priority of the subnet identified by *cidr* to *priority*. This only
   works if the subnet is part of a pool.

   If another subnet from the same pool has the same priority, it is demoted
   (its priority is incremented).

   Valid *options*:

   - :ref:`check-options`
   - :ref:`layer3domain_option`


.. function:: subnet_set_gateway(cidr, gateway[, options])

   Sets the gateway of the subnet identified by *cidr* to *gateway*.

   Valid *options*:

   - :ref:`check-options`
   - :ref:`layer3domain_option`


.. function:: subnet_remove_gateway(cidr[, options])

   Removes the gateway of the subnet identified by *cidr*.

   Valid *options*:

   - :ref:`check-options`
   - :ref:`layer3domain_option`

IP Functions
------------

.. function:: ip_list([options]) -> array of objects

   Returns the list of IP addresses matching the criteria specified in
   *options*. Each IP is represented by an object.

   The members of each object describing an IP can be filtered by specifying the
   *attributes* option. For performance reasons, the set of *attributes* should
   be the minimized.

   Valid *options*:

   - :ref:`layer3domain_option`
   - *limit*: limit the amount of results; anything larger than the
     ``RPC_MAX_RESULTS`` setting on the server is ignored
   - *offset*: skip the first *offset* results
   - *type*: one of ``all``, ``free``, ``used`` (defaults to ``all``)
   - *pool*: only return results from pool with names matching the shell-like
     pattern *pool*
   - *vlan*: only return results from *vlan*
   - *cidr*: only return results from *cidr*
   - *full*: expand IPv6 addresses
   - *attributes*: list of attribute names to be included for each ip if
     available; if not present, all available attributes will be returned.

   *pool*, *cidr* and *vlan* are mutually exclusive.

.. note:: The set of attributes returned for each IP does not include the
   inherited system attributes (like *subnet*, *mask*, *prefixlength*, *pool*,
   *gateway* or *delegation*). *pool* will be returned however if it's present in *attributes*.


.. function:: ip_mark(ip[, options]) -> object

   Sets the status of *ip* to Static.

   Valid *options*:

   - :ref:`check-options`
   - :ref:`layer3domain_option`
   - *attributes*: object with name:value pairs
   - *full*: expand IPv6 addresses


.. function:: ip_free(ip[, options])

   Sets the status of *ip* to Available.

   Valid *options*:

   - *reserved*: if present and true a Reserved IP will be freed, otherwise -1 is returned
   - *include_messages*: also include :ref:`messages` in the return value; the
     numeric return value described below will become a field named *freed*
   - :ref:`check-options`
   - :ref:`layer3domain_option`

   Returns:

   - -1 if *ip* is Reserved and *reserved* is false
   - 0 if *ip* was already Available
   - 1 if *ip* was not Available


Container Functions
-------------------

.. function:: container_list([options]) -> list of objects

   Valid *options*:

   - :ref:`layer3domain_option`
   - *container*

   Returns a tree of blocks starting from *container* or from the list of root
   blocks if *container* is not specified. The leaves are either Subnet or
   Available blocks. Each block has the follwing fields, where applicable:

   - *ip*
   - *status*
   - *attributes*
   - *pool*

   Example::

        container_list()
        [
           {
              "status" : "Container",
              "ip" : "87.106.0.0/16",
              "children" : [
                 {
                    "pool" : "pool",
                    "status" : "Subnet",
                    "ip" : "87.106.0.0/17",
                    "attributes" : {}
                 },
                 {
                    "status" : "Available",
                    "ip" : "87.106.128.0/18"
                 },
                 {
                    "status" : "Available",
                    "ip" : "87.106.192.0/20"
                 },
                 {
                    "status" : "Container",
                    "ip" : "87.106.208.0/20",
                    "children" : [
                       {
                          "status" : "Available",
                          "ip" : "87.106.208.0/20"
                       }
                    ],
                    "attributes" : {}
                 },
                 {
                    "status" : "Available",
                    "ip" : "87.106.224.0/19"
                 }
              ],
              "attributes" : {}
           }
        ]


Zone/View Functions
-------------------

.. function:: zone_create(zone[, options]) -> :ref:`messages`

   Creates a zone or a zone profile.

   Valid *options*:

   - *profile* (boolean): if true, a zone profile will be created (default: false)
   - *from_profile*: the name of a zone profile from which records will be copied
     to the newly created zone
   - *soa_attributes*: :ref:`soa_attributes`
   - *empty_profile_warning*: if warnings for creating an empty profile should
     be issued (default: true)
   - *view_name*: the name of the zone view created for the new zone (default:
     ``default``)
   - *owner* (string): name of a user group
   - *inherit_zone_groups* (boolean): if true, inherit zone-group membership from parent zone
   - *inherit_rights* (boolean): if true, inherit user rights from parent zone
   - *inherit_owner* (boolean): if true, inherit owner from parent zone


.. function:: zone_delete(zone[, options])

   Deletes a zone (only if it contains a single view).

   Valid *options*:

   - *profile* (boolean): :ref:`profile_option`
   - *cleanup*: if true, also delete the resource records and free the IPs
        (default: false)

.. function:: zone_create_view(zone, view[, options]) -> :ref:`messages`

   Creates a new zone view for *zone*.

   Valid *options*:

   - *from_profile*: the name of a zone profile from which records will be copied
     to the newly created zone view
   - *soa_attributes*: :ref:`soa_attributes`

.. function:: zone_rename_view(zone, view, new_name)

   Renames the zone view named *view* (for zone *zone*) to *new_name*.

.. function:: zone_delete_view(zone, view[, options]) -> :ref:`messages`

   Deletes a zone view and all the records it contains. If the view is not
   empty, returns an error unless cleanup is true.

   Valid *options*:

   - *cleanup*: if true, also delete the resource records and free the IPs
     (default: false)

.. function:: zone_list([options]) -> list of zone objects

   Returns a list of zones or zone profiles.

   Valid *options*:

   - *pattern*: pattern to match the zone names against (default: ``*``)
   - *owner*: select only zones owned by user group *owner*
   - *limit*: limit the amount of results
   - *offset*: skip the first *offset* results
   - *profile* (boolean): :ref:`profile_option`
   - *alias*: has no effect, present for backwards compatibility
   - *can_create_rr* (boolean): if true, return all zones where the current user has the create_rr right on at least one view in the zone
   - *can_delete_rr* (boolean): if true, return all zones where the current user has the delete_rr right on at least one view in the zone
   - *exclude_reverse* (boolean): if true, exclude reverse zones
   - *fields*: if true, add the following fields to each object returned:
        *views*, *zone_groups*, *can_create_rr*, *can_delete_rr*

   If both *can_create_rr* and *can_delete_rr* are true, only one of the rights is needed for a zone to be selected.

.. function:: zone_list2([options]) -> object

   Returns a list of zones or zone profiles.

   Valid *options*:

   - *pattern*: pattern to match the zone names against (default: ``*``)
   - *owner*: select only zones owned by user group *owner*
   - *limit*: limit the amount of results
   - *offset*: skip the first *offset* results
   - *profile* (boolean): :ref:`profile_option`
   - *can_create_rr* (boolean): if true, return all zones where the current user has the create_rr right on at least one view in the zone
   - *can_delete_rr* (boolean): if true, return all zones where the current user has the delete_rr right on at least one view in the zone
   - *forward_zones* (boolean): if true, exclude reverse zones unless *ipv4_reverse_zones*, *ipv6_reverse_zones* are true
   - *ipv4_reverse_zones* (boolean): if true, include IPv4 reverse zones
   - *ipv6_reverse_zones* (boolean): if true, include IPv6 reverse zones
   - *favorite_only* (boolean): return only favorite zones

   If both *can_create_rr* and *can_delete_rr* are true, only one of the rights is needed for a zone to be selected.


   Example::

     zone_list2(pattern='myzone.net')
     {"count": 1, "data": [{"name": "myzone.net", "dnssec": false, "views": [{"can_create_rr": false, "name": "default", "can_delete_rr": false}]}]}

.. function:: zone_count([options]) -> integer

   Returns the number of zones or zone profiles.

   Valid *options*:

   - *pattern*
   - *owner*
   - *alias*: has no effect, present for backwards compatibility
   - *profile*
   - *can_create_rr*
   - *can_delete_rr*

   The options have the same meaning as for :func:`zone_list`.

.. function:: zone_list_popular() -> object

  Returns the list of popular zones.

  Example::

    zone_list_popular()
    {"count": 2, "data": [{"name": "myzone.net", "views": [{"can_create_rr": true, "can_delete_rr": true, "name": "default"}]},
    {"name": "schlund.net", "views": [{"can_create_rr": true, "can_delete_rr": true, "name": "internal"}, {"can_create_rr": true, "can_delete_rr": true, "name": "public"}]}}

.. function:: zone_dump(zone[, options]) -> string

   Returns the contents of the zone represented as a BIND zone file.

   Valid *options*:

   - *view* (string): :ref:`view_option`
   - *profile* (boolean): :ref:`profile_option`

.. function:: zone_favorite(zone[, options]) -> boolean

   Returns true if the zone is favorited by the current user.

   Valid *options*:

   - *view* (string): :ref:`view_option`

.. function:: zone_favorite_add(zone[, options])

   Mark zone as favorite for the current user.

   Valid *options*:

   - *view* (string): :ref:`view_option`

.. function:: zone_favorite_remove(zone[, options])

   Remove zone as a favorite of the current user.

   Valid *options*:

   - *view* (string): :ref:`view_option`

.. function:: zone_list_zone_groups(zone[, options]) -> list of objects

   Returns a list of pairs (zone view, zone group) for the current zone.

   Valid *options*:

   - *view* (string): restrict results to *view*

   Example::

     zone_list_zone_groups('test.com')
     [{'view': 'eu', 'zone_group': 'eu_ns'},
      {'view': 'us', 'zone_group': 'us_ns'},
      {'view': 'us', 'zone_group': 'br_ns'}]


.. function:: zone_list_views(zone[, options]) -> list of view objects

   Valid *options*:

   - *can_create_rr* (boolean): if true, return all views where the current user has the create_rr right
   - *can_delete_rr* (boolean): if true, return all views where the current user has the delete_rr right
   - *fields*: if true, add the following fields to each object returned:
        *can_create_rr*, *can_delete_rr*

   If both *can_create_rr* and *can_delete_rr* are true, only one of the rights is needed for a view to be selected.

   Example::

     zone_list_views('test.com')
     [{'name': 'eu'},
      {'name': 'us'}]

.. function:: zone_list_keys(zone) -> list of key objects

   Each key object has the following attributes:

   - *label* (string): key label
   - *type* (string): ``ksk`` or ``zsk``
   - *flags* (integer)
   - *tag* (integer)
   - *algorithm* (integer)
   - *bits* (integer): key length in bits
   - *created*: creation timestamp
   - *pubkey*: base64-encoded public key

.. function:: zone_list_delegation_signers(zone) -> list of DS objects

   Each DS object has the following attributes:

   - *tag* (integer)
   - *algorithm* (integer)
   - *digest_type* (integer)
   - *digest* (string)

.. function:: zone_get_access(zone[, options]) -> array of objects

   Returns a list of access rights. Each access right has the following properties:

   - *access*: string representing an access right
   - *object*: name of the object on which the right is granted
   - *group*: group for which the access right applies

   Valid *options*:

   - *view* (string): :ref:`view_option`

.. function:: zone_get_attrs(zone[, options]) -> object

   Returns zone attributes.

   Valid options:

   - *profile* (boolean): :ref:`profile_option`

   Example::

     zone_get_attrs('test.com')
     {'created': '2013-03-08 17:03:52',
      'created_by': 'admin',
      'modified': '2013-03-08 17:04:10',
      'modified_by': 'admin',
      'name': 'test.com',
      'views': 2,
      'zone_groups': 3}

.. function:: zone_set_attrs(zone, attributes[, options])

   Sets zone attributes. *attributes* must be an object.

   Valid options:

   - *profile* (boolean): :ref:`profile_option`

   The following zone attributes are special and cannot be modified:

   - *name*: zone name
   - *views*: the number of zone views

   The following zone attributes are used for DNSSEC and can be modified:

   - *default_algorithm*: default algorithm used for signing
   - *default_ksk_bits*: default KSK length
   - *default_zsk_bits*: default ZSK length

   The following zone attributes are read-only:

   - *nsec3_algorithm*: ``0`` for disabled or ``8`` for rsasha256
   - *nsec3_iterations*: NSEC3 iterations
   - *nsec3_salt*: ``-`` for no salt or a hexadecimal string

   Example::

     zone_set_attrs('test.com', {'country': 'de'})

.. function:: zone_set_owner(zone, owner)

.. function:: zone_delete_attrs(zone, attribute_names[, options])

   Deletes zone attributes. *attribute_names* must be an list of strings.

   Valid options:

   - *profile* (boolean): :ref:`profile_option`

.. function:: zone_view_get_attrs(zone, view) -> object

   Returns zone view attributes.

.. function:: zone_get_soa_attrs(zone[, options])

   Returns SOA attributes.

   Valid options:

   - *profile* (boolean): :ref:`profile_option`
   - *view* (string): :ref:`view_option`

.. function:: zone_set_soa_attrs(zone, attributes[, options])

   Sets SOA attributes. *attributes* must be an object.

   Valid options:

   - *profile* (boolean): :ref:`profile_option`
   - *view* (string): :ref:`view_option`

.. function:: zone_create_key(zone, key_type) -> string

   Create a DNSSEC key using zone attributes to determine algorithm and key
   length and returns the key label.

   *key_type* can be ``zsk`` or ``ksk``.

.. function:: zone_delete_key(zone, key_label)

   Deletes the specified DNSSEC key.

.. function:: zone_dnssec_enable(zone[, options]) -> list of strings

   Valid options:

   - *algorithm* (integer, required)
   - *ksk_bits* (integer, required)
   - *zsk_bits* (integer, required)
   - *nsec3_algorithm* (integer)
   - *nsec3_iterations* (integer)
   - *nsec3_salt* (hexadecimal string or ``-``)

   Returns the list of labels for the keys created.

.. function:: zone_dnssec_disable(zone)

   Deletes all keys for *zone* and the NSEC3PARAM record.


RR Functions
------------

.. function:: rr_create(options)

   Creates a RR. Valid *options*:

   - *zone* (string): optional if *name* is a fqdn
   - *views* (list of strings): list of view names where the RRs will be created
     (can be left unspecified if the zone only has one view)
   - *profile* (boolean): :ref:`profile_option`
   - *ttl* (integer)
   - *comment* (string)
   - :ref:`rr_options`
   - :ref:`allow_overlap_option`

.. function:: rr_create_from_pool(name, pool[, options])

   Same as :func:`rr_create` but allocate an IP from *pool* to create an A or
   AAAA record. Returns the IP attributes.

   Valid options:

   - *ttl* (integer)
   - *full* (boolean): expand IPv6 addresses
   - *attributes* (object): attributes for the allocated IP

.. function:: rr_delete(options)

   Deletes one or more RRs.

   Valid *options*:

   - *ids* (list of integers): rr ids can be obtained with :func:`rr_get_references`. This option cannot be used with the *zone*, *views*, *profile* or :ref:`rr_options`.
   - *zone* (string): optional if *name* is a fqdn
   - *views* (list of strings): list of view names whence the RRs will be deleted
     (can be left unspecified if the zone only has one view). The list of views only applies
     to the list of specified rrs, not to their references.
   - *profile* (boolean): :ref:`profile_option`
   - :ref:`rr_options`
   - *free_ips* (boolean): also free IPs (default: false)
   - *references* (string): strategy for dealing with references to deleted RRs:

     - ``error`` (default): if other references than A-PTR exist, return an error and don't delete anything
     - ``warn``: delete RRs and their A-PTR references. Warn about other references.
     - ``delete``: delete RRs and recursively delete any references to them
     - ``ignore``: delete just the RRs

     A-PTR references: PTR references of A/AAAA rrs and A/AAAA references of PTR rrs. These will always be deleted
     unless *references* is set to ``ignore``.

.. function:: rr_get_attrs(options) -> object

   Returns RR attributes.

   Valid *options*:

   - *view* (string): :ref:`view_option`
   - :ref:`rr_options`

.. function:: rr_set_attrs(options)

   Sets the RR ttl and/or comment.

   Valid *options*:

   - *comment* (string)
   - *ttl* (integer)
   - *view* (string): :ref:`view_option`
   - :ref:`rr_options`

.. function:: rr_set_comment(options)

   Sets the RR comment.

   Valid *options*:

   - *comment* (string)
   - *view* (string): :ref:`view_option`
   - :ref:`rr_options`

.. function:: rr_set_ttl(options)

   Sets the RR ttl.

   Valid *options*:

   - *ttl* (integer)
   - *view* (string): :ref:`view_option`
   - :ref:`rr_options`

.. function:: rr_list(options) -> list of RRs

   Returns a list of RRs matching the criteria specified in *options*.

   Valid *options*:

   - *limit*: limit the amount of results
   - *offset*: skip the first *offset* results
   - *pattern* (string): pattern to match against the RR name or IP address. A relative pattern will be converted into an absolute one.
   - *type* (string): filter by RR type
   - *zone* (string): filter by RR zone
   - *view* (string): :ref:`view_option`
   - *profile* (boolean): :ref:`profile_option`
   - :ref:`layer3domain_option`
   - *fields* (boolean): if true, add the following fields to each object returned:
        *can_create_rr*, *can_delete_rr* (derived from the user rights on the parent view), *comment*
   - *value_as_object* (boolean): if true, the *value* attribute of a rr object will be an object instead of a string

   Example::

     rr_list()
     [{'zone': 'a.de',
       'value': '100 10 "" "E2U+voice:sip" "!^[+\\\\*]*!" .',
       'record': '*.4.7.3.1.9.1.2.7.4.9.enum',
       'ttl': None,
       'type': 'NAPTR',
       'view': 'default'}]

     rr_list(value_as_object=True)
     [{'zone': 'a.de',
       'value': {'service': 'E2U+voice:sip',
                 'flags': '',
                 'preference': 10,
                 'regexp': '!^[+\\\\*]*!',
                 'order': 100,
                 'replacement': '.'},
       'record': '*.4.7.3.1.9.1.2.7.4.9.enum',
       'ttl': None,
       'type': 'NAPTR',
       'view': 'default'}]


.. function:: rr_get_zone(name)

   Returns the name of the zone where the rr with name *name* will be placed.

.. function:: rr_get_references(options)

   Returns a directed graph of rrs that reference a RR. The returned value is an object with the following structure:

   - *root*: the id of the RR
   - *records*: a list of rr objects that are nodes in the graph. Each object has the *id* property.
   - *graph*: the adjacency list of the graph

   Valid *options*:

   - *delete* (boolean): If true, returns rrs that would be orphaned if rr is deleted. If false, returns rrs that might need to be changed if RR is changed.
   - *view* (string): :ref:`view_option`
   - :ref:`rr_options`

   Example::

     rr_get_references(delete=True, name='a.de.', type='A', view='second', ip='1.1.1.1')
     {
        'graph': {
           '3': [4, 2],
           '2': [],
           '5': [],
           '4': [5]
        },
        'nodes': [
           {
              'name': '1.1.1.1.in-addr.arpa.',
              'zone': '1.1.1.in-addr.arpa',
              'value': 'a.de.',
              'type': 'PTR',
              'id': 2,
              'view': 'default'
           },
           {
              'name': 'a.de.',
              'zone': 'a.de',
              'value': '1.1.1.1',
              'type': 'A',
              'id': 3,
              'view': 'second'
           },
           {
              'name': 'mx.b.de.',
              'zone': 'b.de',
              'value': '10 a.de.',
              'type': 'MX',
              'id': 4,
              'view': 'default'
           },
           {
              'name': 'cname.c.de.',
              'zone': 'c.de',
              'value': 'mx.b.de.',
              'type': 'CNAME',
              'id': 5,
              'view': 'default'
           }
        ],
        'root': 3
     }

.. function:: rr_edit(id, options)

   Modifies the rr with id *id* using *options*. Valid options:

   - *views* (list of strings): list of view names where the RRs will be recreated
     (if a name change requires moving to a different zone)
   - *ttl* (integer)
   - *comment* (string)
   - *references* (list of ids): list of rr references that need to be updated
   - :ref:`rr_options` except *type*


Output Functions
----------------

.. function:: zone_group_create(group[, options])

   Create a zone group. Valid *options*:

   - *comment* (string)

.. function:: zone_group_delete(group)

   Delete a zone group.

.. function:: zone_group_rename(group, new_name)

   Rename zone group *group* to *new_name*.

.. function:: zone_group_add_zone(group, zone[, options])

   Adds a view to the zone group. Valid *options*:

   - *view* (string): :ref:`view_option`

.. function:: zone_group_remove_zone(group, zone)

   Removes a view from the zone group (the view name doesn't need to be
   specified because a single view from each zone can exist in a zone group).

.. function:: zone_group_set_comment(group, comment)

   Set the zone group comment.

.. function:: zone_group_get_attrs(group) -> object

   Return the zone group attributes.

.. function:: zone_group_get_views(group)

   Returns the list of views from the zone group.

   Example::

     zone_group_get_views('br_ns')
     [{'view': 'us', 'zone': 'test.com'}]

.. function:: zone_group_list()

   Returns the list of zone groups.

   Example::

     zone_group_list()
     [{'comment': None, 'name': 'eu_ns'},
      {'comment': None, 'name': 'us_ns'},
      {'comment': None, 'name': 'br_ns'}]

.. function:: zone_group_list_outputs(group)

   Returns the list of outputs for *group*.

.. function:: output_list([options])

   Returns the list of outputs. Valid *options*:

   - *include_status* (boolean): whether to include information about the output
     status (last_run, status, pending_records)

   Example::

     output_list({'include_status': True})
     [{'last_run': '2013-03-08 14:38:48',
       'name': 'eu',
       'pending_records': 0,
       'plugin': 'pdns-db',
       'status': 'OK'},
      {'last_run': '2013-03-08 14:38:48',
       'name': 'us',
       'pending_records': 0,
       'plugin': 'pdns-db',
       'status': 'OK'}]

.. function:: output_create(name, plugin[, options])

   Create an output. Valid *options*:

   - *comment* (string)

   Options for plugin ``pdns-db``:

   - *db_uri* (string)


.. function:: output_delete(output)

   Delete the output.

.. function:: output_rename(output, new_name)

   Rename *output* to *new_name*.

.. function:: output_set_comment(output, comment)

   Set the output comment.

.. function:: output_add_group(output, group)

   Add the zone group to the output.

.. function:: output_remove_group(output, group)

   Remove the zone group from the output.

.. function:: output_get_attrs(output) -> object

   Return the attributes for the output.

.. function:: output_get_groups(output)

   Return the zone groups added to output. Example::

     output_get_groups('output1')
     [{'comment': None, 'zone_group': 'zonegroup1'},
      {'comment': None, 'zone_group': 'zonegroup2'}]



User Rights Functions
---------------------

Access rights supported:

- *allocate*: needs a pool as the object *parameter*
- *create_rr*, *delete_rr*: need a tuple (zone name, zone view name) as the object *parameter*
- *zone_admin*: needs a zone name as the object *parameter*
- *network_admin*, *dns_admin*, *zone_create*, *dns_update_agent*: require no object parameter

.. function:: group_create(name[, options])

   Options:

   - *department_number*

   If *department_number* is specified, an LDAP query will be performed to determine the name of
   the group

   The ``ou`` attribute will be used by ``manage_dim sync_ldap`` as ``departmentNumber``. The LDAP base
   is configurable via LDAP_DEPARTMENT_BASE.

   The *department_number* is also used to synchronize the list of members (with LDAP base configurable via LDAP_USER_BASE

.. function:: group_delete(name)

.. function:: group_rename(name, new_name)

.. function:: group_add_user(group, user)

.. function:: group_remove_user(group, user)

.. function:: group_grant_access(group, access[, object])

.. function:: group_revoke_access(group, access[, object])

.. function:: user_list([options]) -> array of objects

.. function:: user_get_groups(user) -> array of strings

.. function:: user_get_attrs(user) -> object

   Returns an object with the following fields: *username*, *ldap_cn*, *ldap_uid* and
   *department_number*.

.. function:: group_get_users(group [, options]) -> array of strings

   Valid *options*:

   - *include_ldap*: Return an array of objects instead, each object having the following properties:
       *username*, *ldap_cn*, *ldap_uid* and *department_number*.

.. function:: group_get_access(group) -> array of arrays

   Returns the list of access rights for *group*. Each access right is represented as an array with two items:

   - *access*
   - *object* - may be null

.. function:: group_get_attrs(group) -> object

.. function:: group_set_department_number(group, department_number)

.. function:: department_number(department_name) -> number

.. function:: department_list() -> object {department_number, name}


Registrar Functions
-------------------

.. function:: registrar_account_create(name, plugin, url, user, password, subaccount)

   Create a registrar-account. The only valid *plugin* value is ``autodns3``.

.. function:: registrar_account_delete(name)

   Delete a registrar-account. Succeeds if the registrar-account has no actions in progress.

.. function:: registrar_account_get_attrs(name) -> object

.. function:: registrar_account_list([options]) -> array of registrar-account objects

   Each registrar-account object has the following attributes:

   - *name* (string)
   - *plugin* (string)
   - *username* (string)
   - *total_actions* (integer): the number of pending and ongoing actions

   Valid options:

   - *include_actions* (boolean): whether to include the *total_actions* field in the response

.. function:: registrar_account_list_zones(name[, options]) -> array of zone objects

   Return the list of zones added to the registrar-account named *name*. Each zone object has the following fields:

   - *zone* (string)
   - *last_run* (string)
   - *status* (string)
   - *error* (string)

   Valid options:

   - *include_status* (boolean): whether to include the *last_run*, *status* and *error* fields in the response

.. function:: registrar_account_add_zone(name, zone)

   Add zone to registrar-account. A zone can be added to at most one registrar-account.

.. function:: registrar_account_delete_zone(name, zone)

   Remove zone from its registrar-account. Succeeds if the zone has no registrar actions in progress.

.. function:: zone_registrar_actions(zone) -> array of action objects

   Return the list of pending and in progress registrar actions for *zone*. Each action object
   has the following fields:

   - *action* (string)
   - *data* (string)
   - *status* (string)

.. function:: registrar_account_update_zone(zone)

   Starts the pending registrar action for *zone* if there is one.

.. function:: registrar_account_update_zones(name)

   Starts all the pending registrar actions for the zones added to the registrar-account named *name*.
