#compdef gm-gather.py gm-gather

# zsh completion for gm-gather.py / gm-gather

_arguments -s -S \
  '(-h --help)'{-h,--help}'[show help and exit]' \
  '(-S --strict-host-key-checking)'{-S,--strict-host-key-checking}'[enable strict host key checking]' \
  '--pack[create a tar.gz on the remote host and download once]' \
  '--follow-symlinks[dereference symlinks when used with --pack]' \
  '(-n --dry-run)'{-n,--dry-run}'[print the plan without transferring]' \
  '(-v --verbose)'{-v,--verbose}'[enable verbose logging]' \
  '(-x --sudo-collect)'{-x,--sudo-collect}'[force sudo for remote packing operations]' \
  '--no-sudo-collect[disable sudo for remote packing operations]' \
  '(-H --hosts)'{-H+,--hosts=}'[hosts file]:file:_files' \
  '(-u --user)'{-u+,--user=}'[target user on remote hosts]:user:_users' \
  '(-s --ssh-user)'{-s+,--ssh-user=}'[SSH login user]:user:_users' \
  '(-P --port)'{-P+,--port=}'[SSH port]:port:(22)' \
  '(-K --key)'{-K+,--key=}'[SSH private key file]:file:_files -W "$HOME/.ssh"' \
  '(-W --password)'{-W+,--password=}'[SSH password (not recommended)]:password:_guard "^.*$" password' \
  '(-T --timeout)'{-T+,--timeout=}'[SSH/command timeout seconds]:seconds:(30 45 60 90 120)' \
  '(-j --parallel)'{-j+,--parallel=}'[parallel host count]:count:(1 2 3 4 8 16 32 64 128 256)' \
  '*:argument:_files'
