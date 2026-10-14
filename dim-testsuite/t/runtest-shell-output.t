# The test runner compares the output of shell lines (everything that does not
# start with "ndcli") with the expected lines, exactly or as a regex when the
# expectation ends with " re".
$ echo hello
hello
$ printf 'one\ntwo\n'
one
two
$ date +%Y
^20[0-9][0-9]$ re
