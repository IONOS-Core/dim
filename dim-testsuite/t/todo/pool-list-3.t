# https://github.com/ionos-core/dim/issues/86
#
# calcuate correct number of free ips/delegations
# when assignmentsize is in effect
# make sure it detects actual free delegations,
# taking static (blocking) ips into account

$ ndcli create container 2001:db8::/32 comment:RFC3849
INFO - Creating container 2001:db8::/32 in layer3domain default

$ ndcli create pool p assignmentsize:56

$ ndcli modify pool p add subnet 2001:db8:40:c00::/54
INFO - Created subnet 2001:db8:40:c00::/54 in layer3domain default
WARNING - Creating zone c.0.0.4.0.0.8.b.d.0.1.0.0.2.ip6.arpa without profile
WARNING - Primary NS for this Domain is now localhost.
WARNING - Creating zone d.0.0.4.0.0.8.b.d.0.1.0.0.2.ip6.arpa without profile
WARNING - Primary NS for this Domain is now localhost.
WARNING - Creating zone e.0.0.4.0.0.8.b.d.0.1.0.0.2.ip6.arpa without profile
WARNING - Primary NS for this Domain is now localhost.
WARNING - Creating zone f.0.0.4.0.0.8.b.d.0.1.0.0.2.ip6.arpa without profile
WARNING - Primary NS for this Domain is now localhost.

$ ndcli list pool p
INFO - Total free IPs: 4
prio subnet               gateway free(/56) total(/56)
   1 2001:db8:40:c00::/54                 4          4

$ ndcli modify pool p mark ip 2001:db8:40:c00::1
created:2021-07-05 17:12:34.969235
ip:2001:db8:40:c00::1
layer3domain:default
modified:2021-07-05 17:12:34.969287
modified_by:mieslingert
pool:p
prefixlength:54
reverse_zone:c.0.0.4.0.0.8.b.d.0.1.0.0.2.ip6.arpa
status:Static
subnet:2001:db8:40:c00::/54

$ ndcli list pool p
INFO - Total free IPs: 3
prio subnet               gateway free(/56) total(/56)
   1 2001:db8:40:c00::/54                 3          4

$ ndcli modify pool p get  delegation 56
created:2021-07-05 17:13:38.127194
ip:2001:db8:40:d00::/56
layer3domain:default
modified:2021-07-05 17:13:38.127224
modified_by:mieslingert
pool:p
prefixlength:54
reverse_zone:d.0.0.4.0.0.8.b.d.0.1.0.0.2.ip6.arpa
status:Delegation
subnet:2001:db8:40:c00::/54

$ ndcli list pool p
INFO - Total free IPs: 2
prio subnet               gateway free(/56) total(/56)
   1 2001:db8:40:c00::/54                 2          4

$ ndcli modify pool p mark delegation 2001:0db8:0040:0e00::/64
created:2021-07-05 17:15:00.945784
ip:2001:db8:40:e00::/64
layer3domain:default
modified:2021-07-05 17:15:00.945836
modified_by:mieslingert
pool:p
prefixlength:54
reverse_zone:e.0.0.4.0.0.8.b.d.0.1.0.0.2.ip6.arpa
status:Delegation
subnet:2001:db8:40:c00::/54

$ ndcli modify pool p mark delegation 2001:0db8:0040:0e01::/64
created:2021-07-05 17:15:18.152282
ip:2001:db8:40:e01::/64
layer3domain:default
modified:2021-07-05 17:15:18.152343
modified_by:mieslingert
pool:p
prefixlength:54
reverse_zone:e.0.0.4.0.0.8.b.d.0.1.0.0.2.ip6.arpa
status:Delegation
subnet:2001:db8:40:c00::/54

$ ndcli list pool p
INFO - Total free IPs: 1
prio subnet               gateway free(/56) total(/56)
   1 2001:db8:40:c00::/54                 1          4

$ ndcli modify pool p remove subnet 2001\:db8\:40\:c00\:\:/54 -c -f
INFO - Deleting zone c.0.0.4.0.0.8.b.d.0.1.0.0.2.ip6.arpa
INFO - Deleting zone d.0.0.4.0.0.8.b.d.0.1.0.0.2.ip6.arpa
INFO - Deleting zone e.0.0.4.0.0.8.b.d.0.1.0.0.2.ip6.arpa
INFO - Deleting zone f.0.0.4.0.0.8.b.d.0.1.0.0.2.ip6.arpa

$ ndcli delete container 2001:db8::/32
INFO - Deleting container 2001:db8::/32 from layer3domain default
