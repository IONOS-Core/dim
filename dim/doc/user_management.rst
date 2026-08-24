LDAP Integration
================

``manage_dim sync_ldap`` is a script handling the LDAP integration. It must be run regularly to
synchronize de DIM database with LDAP.

A user-group may be associated with an LDAP department at creation::

    ndcli create user-group ldap-named <g>

or later::

    ndcli modify user-group <g> set department <department-id>

If a user-group is associated with a department, its name and user list will automatically be
updated by sync_ldap to match the data in LDAP. Any users belonging to the department will be
created if they don't exist yet.

If the department is deleted from LDAP, the associated user-group will lose this link and will
become a regular user-group.

If a user name is found in LDAP, its ldap_cn and ldap_uid will also be updated by sync_ldap.


Pseudo / Service Accounts
-------------------------

DIM supports synchronizing technical or pseudo-users (such as service accounts or API users) from a separate LDAP directory branch.

To enable this, configure the ``LDAP_PSEUDO_USER_BASE`` variable in your ``dim.cfg``.

If configured, ``sync_ldap`` will query both ``LDAP_USER_BASE`` (for standard accounts, setting ``is_pseudo = False``) and ``LDAP_PSEUDO_USER_BASE`` (for service accounts, setting ``is_pseudo = True``).

If ``LDAP_PSEUDO_USER_BASE`` is left empty or matches the standard base, all users will be treated as standard users.

If your LDAP server requires authentication for queries (or blocks anonymous queries on certain subtrees), you can configure ``LDAP_BIND_DN`` and ``LDAP_BIND_PASSWORD`` in your ``dim.cfg``. If set, the synchronization script will use these credentials to authenticate globally before performing any searches (for standard users, pseudo-users, and departments).


