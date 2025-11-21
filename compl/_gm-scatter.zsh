#compdef gm-scatter gm-scatter.py

# zsh completion for gm-scatter.py / gm-scatter
# requires: autoload -U compinit && compinit

_arguments -s -S \
  '(-h --help)'{-h,--help}'[show help and exit]' \
  '(-S --strict-host-key-checking)'{-S,--strict-host-key-checking}'[enable strict host key checking]' \
  '--pack[create a local tar.gz and extract remotely]' \
  '--follow-symlinks[while packing, follow symlinks]' \
  '(-n --dry-run)'{-n,--dry-run}'[show plan only]' \
  '(-v --verbose)'{-v,--verbose}'[verbose logs]' \
  '(-x --sudo-extract)'{-x,--sudo-extract}'[force sudo for remote extract operations]' \
  '--no-sudo-extract[disable sudo for remote extract operations]' \
  '(-H --hosts)'{-H+,--hosts=}'[hosts file]:file:_files' \
  '(-u --user)'{-u+,--user=}'[target user on remote hosts]:user:_users' \
  '(-s --ssh-user)'{-s+,--ssh-user=}'[SSH login user]:user:_users' \
  '(-P --port)'{-P+,--port=}'[SSH port]:port:(22)' \
  '(-K --key)'{-K+,--key=}'[SSH private key file]:file:_files -W "$HOME/.ssh"' \
  '(-W --password)'{-W+,--password=}'[SSH password (not recommended)]:password:_guard "^.*$" password' \
  '(-T --timeout)'{-T+,--timeout=}'[SSH/command timeout seconds]:seconds:(30 45 60 90 120)' \
  '(-j --parallel)'{-j+,--parallel=}'[parallel host count]:count:(1 2 3 4 8 16 32 64 128 256)' \
  '--selinux[SELinux handling mode]:mode:(auto policy ignore)' \
  '*:path:_files'
