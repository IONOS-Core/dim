# Source default system bashrc if present
if [ -f /etc/bash.bashrc ]; then
    . /etc/bash.bashrc
fi

export PATH="/usr/local/bin:/src/dim-testsuite/virtual_env/bin:$PATH"
export NDCLI_SERVER="http://localhost:5000"
export NDCLI_USERNAME="admin"

# Shell aliases
alias ls="ls --color=auto"
alias ll="ls -lh --color=auto"
alias la="ls -lha --color=auto"

# Testbed shell prompt
PS1="\[\033[1;36m\](dim-testbed)\[\033[0m\] \u@\h:\w\$ "

# Pre-authenticate ndcli for convenience
ndcli login -p p >/dev/null 2>&1 || true

# Tab completion for ndcli
complete -C /src/dim-testsuite/virtual_env/bin/ndcli ndcli 2>/dev/null || true
