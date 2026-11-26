$ ndcli create container 10.0.0.0/8 -q
$ ndcli create pool adjacent-pool -q
$ ndcli modify pool adjacent-pool add subnet 10.10.1.0/24 -q
$ ndcli modify pool adjacent-pool add subnet 10.10.2.0/24 -q

$ ndcli create zone adjacent-eval.com -q
$ ndcli create rr h1.adjacent-eval.com. A 10.10.1.10 -q
$ ndcli create rr h2.adjacent-eval.com. A 10.10.2.10 -q
$ ndcli list zone 1.10.10.in-addr.arpa
record zone                 ttl   type value
     @ 1.10.10.in-addr.arpa 86400 SOA  localhost. hostmaster.1.10.10.in-addr.arpa. 2026112001 14400 3600 605000 86400
    10 1.10.10.in-addr.arpa       PTR  h1.adjacent-eval.com.
$ ndcli list zone 2.10.10.in-addr.arpa
record zone                 ttl   type value
     @ 2.10.10.in-addr.arpa 86400 SOA  localhost. hostmaster.2.10.10.in-addr.arpa. 2026112001 14400 3600 605000 86400
    10 2.10.10.in-addr.arpa       PTR  h2.adjacent-eval.com.
