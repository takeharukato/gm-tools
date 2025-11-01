#compdef gm-scatter gm-scatter.py

# zsh completion for gm-scatter.py / gm-scatter
# requires: autoload -U compinit && compinit

local -a _gm_selinux
_gm_selinux=(auto policy archive ignore)

local -a _gm_int_candidates
_gm_int_candidates=(1 2 3 4 8 16 32 64 128 256)

_arguments -s -S \
  '(-h --help)'{-h,--help}'[show help and exit]' \
  '(-i --ignore-case)'{-i,--ignore-case}'[compile regexes with IGNORECASE]' \
  '(-S --strict-host-key-checking)'{-S,--strict-host-key-checking}'[enable strict host key checking]' \
  '--pack[create local tar.gz -> upload -> remote extract]' \
  '--preserve-perms[preserve permissions on extract (tar -p)]' \
  '--preserve-owner[preserve owner/group on extract (if supported)]' \
  '--preserve-acls[preserve ACLs on extract (if supported)]' \
  '--preserve-xattrs[preserve xattrs on extract (if supported)]' \
  '--follow-symlinks[follow symlinks when scanning locals]' \
  '--include-empty-dirs[SFTP mode: also create empty directories under src]' \
  '(-n --dry-run)'{-n,--dry-run}'[show plan only]' \
  '(-v --verbose)'{-v,--verbose}'[verbose logs]' \
  '(-u --user)'{-u,--user}'[target account on remote]:user:_users' \
  '(-s --ssh-user)'{-s,--ssh-user}'[SSH login user]:user:_users' \
  '(-H --hosts)'{-H,--hosts}'[hosts file]:file:_files' \
  '(-P --port)'{-P,--port}'[SSH port]:port:({_gm_int_candidates})' \
  '(-K --key)'{-K,--key}'[SSH private key file]:file:_files' \
  '(-W --password)'{-W,--password}'[SSH password (not recommended)]:password:_guard "^.*$" "password"' \
  '(-T --timeout)'{-T,--timeout}'[SSH/command timeout (sec)]:seconds:{_values -s " " "5" "10" "15" "20" "30" "45" "60" "90" "120"}' \
  '(-j --parallel)'{-j,--parallel}'[parallel hosts]:count:({_gm_int_candidates})' \
  '(-a --pattern-abs)'{-a,--pattern-abs}'[ABSOLUTE local path regex (repeatable)]:regex:_guard "^.*$" "regex"' \
  '(-r --pattern-rel)'{-r,--pattern-rel}'[RELATIVE path regex to each --root (repeatable)]:regex:_guard "^.*$" "regex"' \
  '(-R --root)'{-R,--root}'[local search root (repeatable)]:directory:_directories' \
  '--selinux[SELinux handling mode]:mode:({_gm_selinux})' \
  '*:path:_files'
