# bash completion for gm-gather.py / gm-gather

# shellcheck shell=bash

_gm_gather()
{
    local cur prev words cword
    # bash-completion 2.x API
    _init_completion -n : || return

    # すべてのロング/ショートオプション ( 引数を取る/取らないを区別 )
    local -a opts_flags=(
        --help -h
        --ignore-case -i
        --strict-host-key-checking -S
        --pack
        --one-archive
        --dry-run -n
        --verbose -v
    )

    local -a opts_args=(
        # expects ARG
        --user -u
        --hosts -H
        --ssh-user -s
        --pattern-abs -a
        --pattern-rel -r
        --parallel -j
        --roots -R
        --port -P
        --key -K
        --password -W
        --timeout -T
    )

    # --long=VAL 形式への対応： "--key=/p/t/h" のようなケース
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
            COMPREPLY=( $(compgen -f -- "$cur") )
            return
            ;;
        --pattern-abs|-a|--pattern-rel|-r)
            # 正規表現は自由入力：補完なし
            COMPREPLY=()
            return
            ;;
        --parallel|-j|--port|-P)
            # 整数
            COMPREPLY=( $(compgen -W "1 2 3 4 8 16 32 64 128 256" -- "$cur") )
            return
            ;;
        --timeout|-T)
            # 秒 ( float想定だが提示候補は整数 )
            COMPREPLY=( $(compgen -W "5 10 15 20 30 45 60 90 120" -- "$cur") )
            return
            ;;
        --roots|-R)
            # ディレクトリ複数 ( スペース区切り ) を想定
            compopt -o filenames
            COMPREPLY=( $(compgen -d -- "$cur") )
            return
            ;;
        --key|-K)
            # 秘密鍵ファイル
            compopt -o filenames
            COMPREPLY=( $(compgen -f -- "$cur") )
            return
            ;;
        --password|-W)
            # パスワードは自由入力：補完なし
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
            --pattern-abs|--pattern-rel)
                COMPREPLY=() ;;  # 自由入力
            --parallel|--port)
                COMPREPLY=( $(compgen -W "1 2 3 4 8 16 32 64 128 256" -- "$after_eq") )
                ;;
            --timeout)
                COMPREPLY=( $(compgen -W "5 10 15 20 30 45 60 90 120" -- "$after_eq") )
                ;;
            --roots)
                compopt -o filenames
                COMPREPLY=( $(compgen -d -- "$after_eq") )
                ;;
            --key)
                compopt -o filenames
                COMPREPLY=( $(compgen -f -- "$after_eq") )
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

    # 位置引数 ( dest ) : ディレクトリを補完
    compopt -o dirnames
    COMPREPLY=( $(compgen -d -- "$cur") )
}

# command name aliases
complete -o bashdefault -o default -F _gm_gather gm-gather.py
complete -o bashdefault -o default -F _gm_gather gm-gather
