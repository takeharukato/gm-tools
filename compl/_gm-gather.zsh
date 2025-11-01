#compdef gm-gather.py gm-gather

# zsh completion for gm-gather.py / gm-gather

local -a _bools
_bools=(
  '(-h --help)'{-h,--help}'[Show help and exit]'
  '(-i --ignore-case)'{-i,--ignore-case}'[Compile regex with re.IGNORECASE]'
  '(-S --strict-host-key-checking)'{-S,--strict-host-key-checking}'[Enable strict host key checking]'
  '(--pack)--pack[Pack on remote host using tar/gzip]'
  '(--one-archive)--one-archive[Combine all roots into a single archive (may collide)]'
  '(-n --dry-run)'{-n,--dry-run}'[List matches only; do not download]'
  '(-v --verbose)'{-v,--verbose}'[Verbose logs]'
)

# _values
local -a _ints_parallel _ints_port _secs_timeout
_ints_parallel=(1 2 3 4 8 16 32 64 128 256)
_ints_port=(22 2222 22222)
_secs_timeout=(5 10 15 20 30 45 60 90 120)

_arguments -s -S \
  "${_bools[@]}" \
  '(-u --user)'{-u+,--user=}'[Target account (for ~ resolution)]:user:_users' \
  '(-H --hosts)'{-H+,--hosts=}'[Hosts file]:file:_files' \
  '(-s --ssh-user)'{-s+,--ssh-user=}'[SSH login user]:user:_users' \
  '(-a --pattern-abs)'{-a+,--pattern-abs=}'[Regex for ABSOLUTE remote paths]:regex:' \
  '(-r --pattern-rel)'{-r+,--pattern-rel=}'[Regex for paths RELATIVE to each --roots]:regex:' \
  '(-j --parallel)'{-j+,--parallel=}'[Concurrent hosts]:_values -s , "parallel" "${_ints_parallel[@]}"' \
  '(-R --roots)'{-R+,--roots=}'[Search roots (absolute paths); may repeat]:directory:_directories' \
  '(-P --port)'{-P+,--port=}'[SSH port]:_values -s , "port" "${_ints_port[@]}"' \
  '(-K --key)'{-K+,--key=}'[SSH private key file]:file:_files' \
  '(-W --password)'{-W+,--password=}'[SSH password (not recommended)]:password:' \
  '(-T --timeout)'{-T+,--timeout=}'[SSH/command timeout seconds]:_values -s , "timeout" "${_secs_timeout[@]}"' \
  '1:dest directory:_directories' \
  '*:: :->rest' && return 0

return 0
