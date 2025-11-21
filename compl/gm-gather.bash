# bash completion for gm-gather.py / gm-gather

# shellcheck shell=bash

_gm_gather()
{
    local cur prev words cword
    # bash-completion 2.x API
    _init_completion -n : || return

    # 現行 CLI のフラグ系オプション
    local -a opts_flags=(
        --help -h
        --strict-host-key-checking -S
        --pack
        --follow-symlinks
        --dry-run -n
        --verbose -v
        --sudo-collect --no-sudo-collect -x
    )

    # 引数を伴うオプション
    local -a opts_args=(
        --hosts -H
        --user -u
        --ssh-user -s
        --port -P
        --key -K
        --password -W
        --timeout -T
        --parallel -j
    )

    # --long=VAL 形式への対応 :  "--key=/p/t/h" のようなケース
    local optname=${cur%%=*}
    local after_eq=
    if [[ $cur == *=* ]]; then
        after_eq=${cur#*=}
    fi

    # 直前のトークンが「引数を要するオプション」なら, その引数を補完
    case "$prev" in
        --user|-u)
            # ローカルユーザー名候補
            COMPREPLY=( $(compgen -A user -- "$cur") )
            return
            ;;
        --ssh-user|-s)
            COMPREPLY=( $(compgen -A user -- "$cur") )
            return
            ;;
        --hosts|-H)
            # ホストリストファイル
            compopt -o filenames
            COMPREPLY=( $(compgen -f -- "$cur") )
            return
            ;;
        --parallel|-j)
            COMPREPLY=( $(compgen -W "1 2 3 4 8 16 32 64 128 256" -- "$cur") )
            return
            ;;
        --port|-P)
            COMPREPLY=( $(compgen -W "22" -- "$cur") )
            return
            ;;
        --timeout|-T)
            COMPREPLY=( $(compgen -W "30 45 60 90 120" -- "$cur") )
            return
            ;;
        --key|-K)
            compopt -o filenames
            if [[ $cur == /* || $cur == ~* ]]; then
                COMPREPLY=( $(compgen -f -- "$cur") )
            else
                COMPREPLY=( $(compgen -f -- "$HOME/.ssh/$cur") )
            fi
            return
            ;;
        --password|-W)
            # パスワードは自由入力 : 補完なし
            COMPREPLY=()
            return
            ;;
    esac

    # --long= の右辺を補完中
    if [[ -n $after_eq ]]; then
        case "$optname" in
            --user|--ssh-user)
                COMPREPLY=( $(compgen -A user -- "$after_eq") )
                ;;
            --hosts)
                COMPREPLY=( $(compgen -f -- "$after_eq") )
                ;;
            --parallel)
                COMPREPLY=( $(compgen -W "1 2 3 4 8 16 32 64 128 256" -- "$after_eq") )
                ;;
            --port)
                COMPREPLY=( $(compgen -W "22" -- "$after_eq") )
                ;;
            --timeout)
                COMPREPLY=( $(compgen -W "30 45 60 90 120" -- "$after_eq") )
                ;;
            --key)
                compopt -o filenames
                if [[ $after_eq == /* || $after_eq == ~* ]]; then
                    COMPREPLY=( $(compgen -f -- "$after_eq") )
                else
                    COMPREPLY=( $(compgen -f -- "$HOME/.ssh/$after_eq") )
                fi
                ;;
            --password)
                COMPREPLY=() ;;  # 自由入力
            *)
                COMPREPLY=() ;;
        esac
        # --long=VAL の形式なので現在単語全体を置換
        if ((${#COMPREPLY[@]})); then
            local i
            for i in "${!COMPREPLY[@]}"; do
                COMPREPLY[$i]="$optname=${COMPREPLY[$i]}"
            done
        fi
        return
    fi

    # まだオプションを入力中ならロング/ショートを提示
    if [[ $cur == -* ]]; then
        COMPREPLY=( $(compgen -W "${opts_flags[*]} ${opts_args[*]}" -- "$cur") )
        return
    fi

    # 位置引数は自由入力とする
    COMPREPLY=()
}

# command name aliases
complete -o bashdefault -o default -F _gm_gather gm-gather.py
complete -o bashdefault -o default -F _gm_gather gm-gather
