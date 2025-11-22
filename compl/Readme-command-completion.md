# gm-tools Completion Script Overview

This directory contains shell completion scripts for `gm-gather` and `gm-scatter`. They support both bash and zsh, providing context-aware suggestions for each command-line argument.

Positional arguments consist of one or more sources and a required destination. Because the completer cannot tell them apart while the user is typing, both bash and zsh enable filename completion so that files and directories are suggested.

## Bash Scripts

- `gm-gather` / `gm-gather.bash`
  - Uses the bash-completion initialisation helper (`_init_completion`) to populate the current token (`$cur`) and the previous token (`$prev`) and decide what should be completed.
  - Detects options that expect a value—`--hosts/-H`, `--user/-u`, `--ssh-user/-s`, `--port/-P`, `--key/-K`, `--password/-W`, `--timeout/-T`, `--parallel/-j`—and generates tailored candidates for each.
    - `--hosts/-H`: switches to filename completion and suggests host list files.
    - `--user/-u` and `--ssh-user/-s`: lists local system user names.
    - `--port/-P`: offers common port numbers (the current script suggests `22`).
    - `--key/-K`: performs filename completion; absolute paths are used as-is, while relative paths are resolved under `$HOME/.ssh/`.
    - `--password/-W`: suppresses completion so the user can type freely.
    - `--timeout/-T`: proposes typical timeout values (`30 45 60 90 120`).
    - `--parallel/-j`: suggests plausible parallelism levels (`1 2 3 4 8 16 32 64 128 256`).
  - When the user types `--option=value`, only the part after the equals sign is recompleted, and the result is reassembled as `--option=candidate`.
  - Options without arguments—`--help/-h`, `--strict-host-key-checking/-S`, `--pack`, `--follow-symlinks`, `--dry-run/-n`, `--verbose/-v`, `--sudo-collect`, `--no-sudo-collect`, and the short toggle `-x`—are offered as soon as a dash-prefixed token is started.
  - The registration `complete -F _gm_gather` ensures this completion function is invoked for `gm-gather` and `gm-gather.py`.

- `gm-scatter` / `gm-scatter.bash`
  - Follows the same `_init_completion` workflow as the gather script.
  - Recognises value-taking options `--hosts/-H`, `--user/-u`, `--ssh-user/-s`, `--port/-P`, `--key/-K`, `--password/-W`, `--timeout/-T`, `--parallel/-j`, and `--selinux`.
    - `--hosts/-H`, `--user/-u`, `--ssh-user/-s`, `--port/-P`, `--key/-K`, `--password/-W`, `--timeout/-T`, and `--parallel/-j` reuse the same completion logic as `gm-gather`.
    - `--selinux`: offers the choices `auto`, `policy`, and `ignore`.
  - Options without arguments—`--help/-h`, `--strict-host-key-checking/-S`, `--pack`, `--follow-symlinks`, `--dry-run/-n`, `--verbose/-v`, `--sudo-extract`, `--no-sudo-extract`, and the short toggle `-x`—are listed when a dash-prefixed token is entered.
  - The registration `complete -F _gm_scatter` hooks the completion into `gm-scatter` and `gm-scatter.py`.

Placing these files in the bash-completion directory allows the shell to load them automatically when the commands are first used. The repository's `compl/Makefile.am` defines an installation target that copies the scripts into the bash completion directory chosen at configure time, so running `make install` puts everything in the right place.

## zsh Scripts

- `_gm-gather` / `_gm-gather.zsh`
  - Uses the zsh built-in `compdef` to associate the completion definition with the `gm-gather` command.
  - Relies on zsh's `_arguments` helper to declare option behaviour and produce candidates together with inline help.
  - `--help/-h`: shows the description and proposes the flag.
  - `--strict-host-key-checking/-S`: presents the flag.
  - `--pack`, `--follow-symlinks`, `--dry-run/-n`, `--verbose/-v`, `--sudo-collect`, `--no-sudo-collect`, and `-x`: appear as argument-less options in the list.
  - `--hosts/-H`: invokes `_files` to complete host list files.
  - `--user/-u`, `--ssh-user/-s`: call `_users` to enumerate system accounts.
  - `--port/-P`: suggests `22` as the port candidate.
  - `--key/-K`: reuses `_files` and, via `-W "$HOME/.ssh"`, biases completion toward the `~/.ssh` directory.
  - `--password/-W`: uses `_guard "^.*$" password` to accept any string while effectively disabling suggestions.
  - `--timeout/-T`: offers `(30 45 60 90 120)`.
  - `--parallel/-j`: offers `(1 2 3 4 8 16 32 64 128 256)`.
  - Positional arguments (`*:argument:_files`): call `_files` so both files and directories are suggested.

- `_gm-scatter` / `_gm-scatter.zsh`
  - Also employs `compdef` and `_arguments`, but adds the `--selinux` option with the `(auto policy ignore)` choice list.
  - `--help/-h`, `--strict-host-key-checking/-S`, `--pack`, `--follow-symlinks`, `--dry-run/-n`, `--verbose/-v`, `--sudo-extract`, `--no-sudo-extract`, and `-x` are surfaced as flag candidates.
  - `--hosts/-H`, `--user/-u`, `--ssh-user/-s`, `--port/-P`, `--key/-K`, `--password/-W`, `--timeout/-T`, and `--parallel/-j` share the same completion behaviour as the gather variant.

To activate the zsh completions, place the files somewhere on zsh's completion search path and run `autoload -U compinit && compinit` (the `compinit` command initialises the completion system). `compl/Makefile.am` provides a target that copies the scripts into the zsh completion directory specified when configuring the build, so `make install` deploys them automatically.

## Usage Checklist

1. During `./configure`, pass `--with-bash-completion-dir` and/or `--with-zsh-completion-dir` to choose the installation paths for the scripts.
2. Run `make install` to copy the completion files into the selected directories.
3. If completion is enabled in your shell configuration, opening a new shell makes the completions available immediately. You can also source the files manually for quick testing.

If you change the set of supported options, update the option arrays in the bash scripts and the `_arguments` declarations in the zsh scripts accordingly.
