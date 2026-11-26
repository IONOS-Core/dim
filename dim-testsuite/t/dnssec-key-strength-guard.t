$ ndcli create zone weak-ksk-eval.com -q

$ ndcli modify zone weak-ksk-eval.com dnssec enable 8 ksk 512 zsk 512
ERROR - RSA modulus length must be >= 1024
