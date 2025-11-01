# bash completion for gm-scatter.py / gm-scatter
# shellcheck shell=bash

_gm_scatter()
{
    local cur prev words cword
    # bash-completion 2.x API
    _init_completion -n : || return

    # ---- flags (no arg) ----
    local -a opts_flags=(
        --help -h
        --ignore-case -i
        --strict-host-key-checking -S
        --pack
        --preserve-perms
        --preserve-owner
        --preserve-acls
        --preserve-xattrs
        --follow-symlinks
        --include-empty-dirs
        --dry-run -n
        --verbose -v
    )

    # ---- options (expect an arg) ----
    local -a opts_args=(
        --user -u
        --hosts -H
        --ssh-user -s
        --pattern-abs -a
        --pattern-rel -r
        --parallel -j
        --root -R
        --port -P
        --key -K
        --password -W
        --timeout -T
        --selinux
    )

    # support --long=VAL form
    local optname=${cur%%=*}
    local after_eq=
    if [[ $cur == *=* ]]; then
        after_eq=${cur#*=}
    fi

    # complete argument after an option that expects one
    case "$prev" in
        --user|-u|--ssh-user|-s)
            COMPREPLY=( $(compgen -A user -- "$cur") )
            return
            ;;
        --hosts|-H)
            COMPREPLY=( $(compgen -f -- "$cur") )
            return
            ;;
        --pattern-abs|-a|--pattern-rel|-r)
            COMPREPLY=()    # free-form regex
            return
            ;;
        --parallel|-j|--port|-P)
            COMPREPLY=( $(compgen -W "1 2 3 4 8 16 32 64 128 256" -- "$cur") )
            return
            ;;
        --timeout|-T)
            COMPREPLY=( $(compgen -W "5 10 15 20 30 45 60 90 120" -- "$cur") )
            return
            ;;
        --root|-R)
            compopt -o filenames
            COMPREPLY=( $(compgen -d -- "$cur") )
            return
            ;;
        --key|-K)
            compopt -o filenames
            COMPREPLY=( $(compgen -f -- "$cur") )
            return
            ;;
        --password|-W)
            COMPREPLY=()    # free-form
            return
            ;;
        --selinux)
            COMPREPLY=( $(compgen -W "auto policy archive ignore" -- "$cur") )
            return
            ;;
    esac

    # completing right-hand side for --long=VAL
    if [[ -n $after_eq ]]; then
        case "$optname" in
            --user|--ssh-user)
                COMPREPLY=( $(compgen -A user -- "$after_eq") )
                ;;
            --hosts)
                COMPREPLY=( $(compgen -f -- "$after_eq") )
                ;;
            --pattern-abs|--pattern-rel)
                COMPREPLY=() ;;  # free-form
            --parallel|--port)
                COMPREPLY=( $(compgen -W "1 2 3 4 8 16 32 64 128 256" -- "$after_eq") )
                ;;
            --timeout)
                COMPREPLY=( $(compgen -W "5 10 15 20 30 45 60 90 120" -- "$after_eq") )
                ;;
            --root)
                compopt -o filenames
                COMPREPLY=( $(compgen -d -- "$after_eq") )
                ;;
            --key)
                compopt -o filenames
                COMPREPLY=( $(compgen -f -- "$after_eq") )
                ;;
            --password)
                COMPREPLY=() ;;
            --selinux)
                COMPREPLY=( $(compgen -W "auto policy archive ignore" -- "$after_eq") )
                ;;
            *)
                COMPREPLY=() ;;
        esac
        if ((${#COMPREPLY[@]})); then
            local i
            for i in "${!COMPREPLY[@]}"; do
                COMPREPLY[$i]="$optname=${COMPREPLY[$i]}"
            done
        fi
        return
    fi

    # show options while typing a dash-prefixed token
    if [[ $cur == -* ]]; then
        COMPREPLY=( $(compgen -W "${opts_flags[*]} ${opts_args[*]}" -- "$cur") )
        return
    fi

    # positional arguments:
    # gm-scatter は 0..N の src と最後に dest（必須）だが、補完段階では判別困難。
    # ここでは src/dest の双方に有用なファイル名補完を提供する。
    compopt -o filenames
    COMPREPLY=( $(compgen -f -- "$cur") )
}

complete -o bashdefault -o default -F _gm_scatter gm-scatter.py
complete -o bashdefault -o default -F _gm_scatter gm-scatter
