# test tree view for list containers (-T/--tree, -p/--pattern, -c/--case-sensitive, --no-color)
$ ndcli create container 10.0.0.0/8
INFO - Creating container 10.0.0.0/8 in layer3domain default

# basic tree with long flag; single container uses └── connector
$ ndcli list containers layer3domain default --tree
layer3domain: default
└── 10.0.0.0/8 (Container)
    └── 10.0.0.0/8 (Available)

$ ndcli create layer3domain one type vrf rd 0:1
$ ndcli create container 10.0.0.0/8 layer3domain one
INFO - Creating container 10.0.0.0/8 in layer3domain one
$ ndcli create container 11.0.0.0/8 layer3domain one
INFO - Creating container 11.0.0.0/8 in layer3domain one

# short flag -T; multiple containers: non-last uses ├── and │ prefix, last uses └──
$ ndcli list containers layer3domain one -T
layer3domain: one
├── 10.0.0.0/8 (Container)
│   └── 10.0.0.0/8 (Available)
└── 11.0.0.0/8 (Container)
    └── 11.0.0.0/8 (Available)

# pattern filter implies tree mode; only the matching subtree is shown
$ ndcli list containers layer3domain one -p 11
layer3domain: one
└── 11.0.0.0/8 (Container)
    └── 11.0.0.0/8 (Available)

# pattern is case-insensitive by default; both containers carry "(Container)" in their text
$ ndcli list containers layer3domain one -p container
layer3domain: one
├── 10.0.0.0/8 (Container)
│   └── 10.0.0.0/8 (Available)
└── 11.0.0.0/8 (Container)
    └── 11.0.0.0/8 (Available)

# case-sensitive flag: lowercase "container" does not match "(Container)"
$ ndcli list containers layer3domain one -p container -c
WARNING - No matches for pattern 'container' in layer3domain one

# --no-color is accepted without error; no visual difference for non-tty output
$ ndcli list containers layer3domain default -T --no-color
layer3domain: default
└── 10.0.0.0/8 (Container)
    └── 10.0.0.0/8 (Available)
