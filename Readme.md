# gm-tools

## Overview

gm-tools is a toolset providing `gm-gather` and `gm-scatter` to assist with file collection and distribution across multiple hosts. It features bulk transfer of multiple files via SSH or compressed archives, and automatically restores metadata for distributed files.

## Prerequisite Packages

- Python 3.9 or later
- [python3-paramiko](https://www.paramiko.org/)
- gettext-0.21 or later
- autoconf-2.69 or later
- automake-1.16 or later
- acl-2.2 or later (if using the Access Control List restoration feature)
- attr-2.4 or later (if using the extended attribute restoration feature)
- sudo-1.8 or later
- policycoreutils (if using the SE Linux context information restoration feature; requires a version where the `-RF` option is available with the `restorecon` command)

Additionally, bash and the mktemp command (requiring the `-d` option for creating directories with specified templates) are needed. These should be available on standard Linux distributions and BSD-based OSes.

## Installation

Installation is possible using the following steps.

```:shell
./autogen.sh
./configure
make
make install
```

## make Targets

- `make` : Builds core modules and scripts
- `make install` : Installs executables, modules, and man pages to the system
- `make check` : Runs the main test suite
- `make dist` : Generates a distribution tarball
- `make clean` : Deletes generated files
- `make cloc` : Outputs source size (requires the `cloc` command)
- `make rpm`: Generates an RPM package (requires the `docker` command)
- `make deb`: Generates a Debian package (requires the `docker` command)

In the `docs/sphinx` directory, running the `make docs` command generates the test framework and the program's interface specification (requires the Sphinx package).

In the `po` directory, the following make targets are defined:

- `make update-po`: Regenerates the POT file (`gm-tools.pot`), merges it into the respective language PO files (`*.po`), and updates the `.gmo` (binary dictionary).

- `make gm-tools.pot-update`: Regenerates only the POT file (also called internally by `make update-po`).
- `make update-gmo` Rebuilds the .gmo from existing PO files. Useful when you want to regenerate only the mo file after manually editing the PO.
- `make` or `make all` Maintains stamp-po and .gmo (typically called during a build).
- `make install` Installs the translated `.mo` files.
- `make uninstall` removes the translated `.mo` files.
- `make clean`, `make distclean`, `make maintainer-clean` remove generated files (`*.gmo`, `stamp-po`, etc.) according to the cleanup level.

## Example Usages

### Creating a Host File

In this example, both `localhost` and `vmlinux.local` are set as target remote hosts. This example assumes that each remote host allows SSH login without a password using the same username as the current user. It further assumes the current user's username is `user` and that the same user exists on the remote host.

Create a host file named `hostfile` as follows:

```:plaintext
localhost
vmlinux.local
```

### Treatment of Literal Names In `SRC` Positional Argument

When gm-gather and gm-scatter detect the following meta characters used in Python regular expressions within the positional argument `SRC`, they treat the argument as a file or directory name specified with a regular expression.

- `^`, `$`, `*`, `+`, `?`, `\`
- `{`, `}`
- `[`,`]`, `|`
- `(`, `)`

However, gm-gather and gm-scatter do not use character '.' to detect regular expressions in the `SRC` parameters. Because the character '.' is commonly used as a separator to denote file extensions. You can use '.' without escaping when specifying filenames as literal values.

### Retrieving Files

#### Example of Retrieving Files With Literal Names

To collect the `.zshrc.mine` file located under the home directory on the remote hosts and store it in the `dest` directory, use `gm-gather` as follows:

```:shell
gm-gather   '~/.zshrc.mine' dest
```

An example execution is as follows:

```:shell
$  gm-gather '~/.zshrc.mine' dest
timestamp="2025-11-24T09:41:19.310+09:00" level="INFO" host="localhost" op="gather" phase="start" trial="0" processed="0" total="1" msg="host start"
timestamp="2025-11-24T09:41:19.310+09:00" level="INFO" host="vmlinux.local" op="gather" phase="start" trial="0" processed="0" total="1" msg="host start"
timestamp="2025-11-24T09:41:19.314+09:00" level="INFO" host="localhost" op="gather" phase="done" trial="1" processed="1" total="1" warnings="0" errors="0" duration="0.0" msg="host done"
timestamp="2025-11-24T09:41:19.314+09:00" level="INFO" host="vmlinux.local" op="gather" phase="done" trial="1" processed="1" total="1" warnings="0" errors="0" duration="0.0" msg="host done"
timestamp="2025-11-24T09:41:19.315+09:00" level="INFO" host="-" op="gather" phase="done" trial="2" processed="2" total="2" warnings="0" errors="0" msg="summary"
$ tree -a dest
dest
|-- localhost
|   `-- home
|       `-- user
|           `-- .zshrc.mine
`-- vmlinux.local
    `-- home
        `-- user
            `-- .zshrc.mine

6 directories, 2 files
```

#### Retrieving Files Using Regular Expressions

#### Retrieving Files Without Privilege Escalation

To collect files starting with `.zsh` under the remote host's home directory and store them in the `dest` directory, use `gm-gather` as follows:

```:shell
gm-gather '~/\.zsh.*' dest
```

An example execution is as follows:

```:shell
$ gm-gather '~/\.zsh.*' dest
timestamp="2025-11-23T01:14:33.209+09:00" level="INFO" host="localhost" op="gather" phase="start" trial="0" processed="0" total="4" msg="host start"
timestamp="2025-11-23T01:14:33.210+09:00" level="INFO" host="vmlinux.local" op="gather" phase="start" trial="0" processed="0" total="4" msg="host start"
timestamp="2025-11-23T01:14:33.214+09:00" level="DEBUG" host="localhost" op="gather" phase="processing" trial="1" processed="1" total="4" seq="1" msg="processing"
timestamp="2025-11-23T01:14:33.214+09:00" level="DEBUG" host="vmlinux.local" op="gather" phase="processing" trial="1" processed="1" total="4" seq="1" msg="processing"
timestamp="2025-11-23T01:14:33.218+09:00" level="DEBUG" host="localhost" op="gather" phase="processing" trial="2" processed="2" total="4" seq="2" msg="processing"
timestamp="2025-11-23T01:14:33.218+09:00" level="DEBUG" host="vmlinux.local" op="gather" phase="processing" trial="2" processed="2" total="4" seq="2" msg="processing"
timestamp="2025-11-23T01:14:33.221+09:00" level="DEBUG" host="localhost" op="gather" phase="processing" trial="3" processed="3" total="4" seq="3" msg="processing"
timestamp="2025-11-23T01:14:33.222+09:00" level="DEBUG" host="vmlinux.local" op="gather" phase="processing" trial="3" processed="3" total="4" seq="3" msg="processing"
timestamp="2025-11-23T01:14:33.225+09:00" level="DEBUG" host="localhost" op="gather" phase="processing" trial="4" processed="4" total="4" seq="4" msg="processing"
timestamp="2025-11-23T01:14:33.225+09:00" level="INFO" host="localhost" op="gather" phase="done" trial="4" processed="4" total="4" warnings="0" errors="0" duration="0.0" msg="host done"
timestamp="2025-11-23T01:14:33.225+09:00" level="DEBUG" host="vmlinux.local" op="gather" phase="processing" trial="4" processed="4" total="4" seq="4" msg="processing"
timestamp="2025-11-23T01:14:33.225+09:00" level="INFO" host="vmlinux.local" op="gather" phase="done" trial="4" processed="4" total="4" warnings="0" errors="0" duration="0.0" msg="host done"
timestamp="2025-11-23T01:14:33.226+09:00" level="INFO" host="-" op="gather" phase="done" trial="8" processed="8" total="8" warnings="0" errors="0" msg="summary"
$ tree -a dest
dest
|-- localhost
|   `-- home
|       `-- user
|           |-- .zshrc
|           |-- .zshrc.lmod
|           |-- .zshrc.mine
|           `-- .zshrc.proxy
`-- vmlinux.local
    `-- home
        `-- user
            |-- .zshrc
            |-- .zshrc.lmod
            |-- .zshrc.mine
            `-- .zshrc.proxy

6 directories, 8 files
```

#### Retrieving Files With Privilege Escalation

After logging into the remote host, using the administrator account (`root`), to collect files starting with '/etc/host' and store them in the `dest` directory, specify the options as follows:

```:shell
gm-gather -u root --ssh-user=user --pack '/etc/^host.*' dest
```

The meaning of each option is as follows:

- Specify `-u root` to retrieve files under `/etc`
- Since root cannot ssh login directly, log in once as `user`, then use sudo to become the privileged user and retrieve the files
- Use `--pack` to attempt retrieval including symbolic links
- Use `-v` to display detailed logs
- Specify the retrieval file pattern '/etc/^host.*' in `SRC`
- Specify the output destination directory as `dest`

An example of executing the above command is as follows.

```:shell
$ gm-gather -u root --ssh-user=user --pack -v '/etc/^host.*' dest
timestamp="2025-11-24T04:18:52.171+09:00" level="INFO" host="localhost" op="gather" phase="start" trial="0" processed="0" total="3" msg="host start"
timestamp="2025-11-24T04:18:52.171+09:00" level="INFO" host="vmlinux.local" op="gather" phase="start" trial="0" processed="0" total="5" msg="host start"
timestamp="2025-11-24T04:18:52.576+09:00" level="DEBUG" host="vmlinux.local" op="gather" phase="processing" trial="1" processed="1" total="5" seq="1" msg="processing"
timestamp="2025-11-24T04:18:52.576+09:00" level="DEBUG" host="vmlinux.local" op="gather" phase="processing" trial="2" processed="2" total="5" seq="2" msg="processing"
timestamp="2025-11-24T04:18:52.576+09:00" level="DEBUG" host="vmlinux.local" op="gather" phase="processing" trial="3" processed="3" total="5" seq="3" msg="processing"
timestamp="2025-11-24T04:18:52.576+09:00" level="DEBUG" host="vmlinux.local" op="gather" phase="processing" trial="4" processed="4" total="5" seq="4" msg="processing"
timestamp="2025-11-24T04:18:52.576+09:00" level="DEBUG" host="vmlinux.local" op="gather" phase="processing" trial="5" processed="5" total="5" seq="5" msg="processing"
timestamp="2025-11-24T04:18:52.669+09:00" level="DEBUG" host="localhost" op="gather" phase="processing" trial="1" processed="1" total="3" seq="1" msg="processing"
timestamp="2025-11-24T04:18:52.669+09:00" level="DEBUG" host="localhost" op="gather" phase="processing" trial="2" processed="2" total="3" seq="2" msg="processing"
timestamp="2025-11-24T04:18:52.669+09:00" level="DEBUG" host="localhost" op="gather" phase="processing" trial="3" processed="3" total="3" seq="3" msg="processing"
timestamp="2025-11-24T04:18:52.669+09:00" level="INFO" host="localhost" op="gather" phase="done" trial="3" processed="3" total="3" warnings="0" errors="0" duration="0.5" msg="host done"
timestamp="2025-11-24T04:18:52.669+09:00" level="INFO" host="vmlinux.local" op="gather" phase="done" trial="5" processed="5" total="5" warnings="0" errors="0" duration="0.5" msg="host done"
timestamp="2025-11-24T04:18:52.670+09:00" level="INFO" host="-" op="gather" phase="done" trial="8" processed="8" total="8" warnings="0" errors="0" msg="summary"
$ tree -a dest
dest
|-- localhost
|   `-- etc
|       |-- host.conf
|       |-- hostname
|       `-- hosts
`-- vmlinux.local
    `-- etc
        |-- host.conf
        |-- hostname
        |-- hosts
        |-- hosts.allow
        `-- hosts.deny

4 directories, 8 files
```

### Scattering Files

#### Scattering Files Using Literal Names

Example of File Scatter Using Literal Names
To scatter sample.txt in the current directory on localhost into a directory named /tmp/dest-scatter on the remote hosts, use gm-scatter as follows:

```:shell
gm-scatter './sample.txt'  /tmp/dest-scatter
```

The execution example would be as follows:

```:shell
$ gm-scatter './sample.txt' /tmp/dest-scatter
timestamp="2025-11-24T11:15:25.229+09:00" level="INFO" host="localhost" op="scatter" phase="start" trial="0" processed="0" total="1" msg="host start"
timestamp="2025-11-24T11:15:25.230+09:00" level="INFO" host="vmlinux.local" op="scatter" phase="start" trial="0" processed="0" total="1" msg="host start"
timestamp="2025-11-24T11:15:25.793+09:00" level="INFO" host="localhost" op="scatter" phase="done" trial="1" processed="1" total="1" warnings="0" errors="0" duration="0.6" msg="host done"
timestamp="2025-11-24T11:15:25.793+09:00" level="INFO" host="vmlinux.local" op="scatter" phase="done" trial="1" processed="1" total="1" warnings="0" errors="0" duration="0.6" msg="host done"
timestamp="2025-11-24T11:15:25.794+09:00" level="INFO" host="-" op="scatter" phase="done" trial="2" processed="2" total="2" warnings="0" errors="0" msg="summary"
$ tree -a /tmp/dest-scatter
/tmp/dest-scatter
`-- sample.txt
0 directories, 1 file
$ ssh vmlinux.local -- tree -a /tmp/dest-scatter
/tmp/dest-scatter
`-- sample.txt

1 directory, 1 file
```

#### Scattering Files Using Using Regular Expressions

To scatter files starting with `host` from the localhost's current directory into a directory named `dest-scatter` directly under the remote host's home directory, use `gm-scatter` as follows.

```:shell
gm-scatter './host.*' ~/dest-scatter
```

If only `hostfile` exists in the current directory as a file starting with `host`, the execution example would be as follows:

```:shell
$ gm-scatter './host.*' ~/dest-scatter
$ tree -a dest-scatter
dest-scatter
`-- home
    `-- user
        `-- hostfile

2 directories, 1 file
$ ssh vmlinux.local -- tree -a dest-scatter
dest-scatter
`-- home
    `-- user
        `-- hostfile

2 directories, 1 file
```

## Copyright Notice

Copyright 2025 Takeharu KATO.

This project is distributed under the BSD 2-Clause License.
See the `LICENSE` file for details.
