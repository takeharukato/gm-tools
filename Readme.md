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

## Usage Examples

### Creating a Host File

In this example, both `localhost` and `vmlinux.local` are set as target remote hosts. This example assumes that each remote host allows SSH login without a password using the same username as the current user. It further assumes the current user's username is `user` and that the same user exists on the remote host.

Create a host file named `hostfile` as follows:

```:plaintext
localhost
vmlinux.local
```

### Example of Retrieving Files Using Regular Expressions

To retrieve files starting with `.zsh` under the home directory of a remote host, use `gm-gather` as follows:

```:shell
gm-gather “~/\.zsh.*” dest
```

An example execution is as follows:

```:shell
$ gm-gather “~/\.zsh.*” dest
timestamp=“2025-11-23T01:14:33.209+09:00” level="INFO" host="localhost" op="gather" phase="start" trial="0" processed="0" total="4" msg="host start"
timestamp=“2025-11-23T01:14:33.210+09:00” level="INFO" host="vmlinux.local" op="gather" phase="start" trial="0" processed="0" total="4" msg="host start"
timestamp=“2025-11-23T01:14:33.214+09:00” level="DEBUG" host="localhost" op="gather" phase="processing" trial="1" processed="1" total="4" seq="1" msg="processing"
timestamp=“2025-11-23T01:14:33.214+09:00” level="DEBUG" host="vmlinux.local" op="gather" phase="processing" trial="1" processed="1" total="4" seq="1" msg="processing"
timestamp=“2025-11-23T01:14:33.218+09:00” level="DEBUG" host="localhost" op="gather" phase="processing" trial="2" processed="2" total="4" seq="2" msg="processing"
timestamp=“2025-11-23T01:14:33.218+09:00” level="DEBUG" host="vmlinux.local" op="gather" phase="processing" trial="2" processed="2" total="4" seq="2" msg="processing"
timestamp=“2025-11-23T01:14:33.221+09:00” level="DEBUG" host="localhost" op="gather" phase="processing" trial="3" processed="3" total="4" seq="3" msg="processing"
timestamp=“2025-11-23T01:14:33.222+09:00” level="DEBUG" host="vmlinux.local" op="gather" phase="processing" trial="3" processed="3" total="4" seq="3" msg="processing"
timestamp=“2025-11-23T01:14:33.225+09:00” level="DEBUG" host="localhost" op="gather" phase="processing" trial="4" processed="4" total="4" seq="4" msg="processing"
timestamp=“2025-11-23T01:14:33.225+09:00” level="INFO" host="localhost" op="gather" phase="done" trial="4" processed="4" total="4" warnings="0" errors="0" duration="0.0" msg="host done"
timestamp=“2025-11-23T01:14:33.225+09:00” level="DEBUG" host="vmlinux.local" op="gather" phase="processing" trial="4" processed="4" total="4" seq="4" msg="processing"
timestamp=“2025-11-23T01:14:33.225+09:00” level="INFO" host="vmlinux.local" op="gather" phase="done" trial="4" processed="4" total="4" warnings="0" errors="0" duration="0.0" msg="host done"
timestamp=“2025-11-23T01:14:33.226+09:00” level="INFO" host="-" op="gather" phase="done" trial="8" processed="8" total="8" warnings="0" errors="0" msg="summary"
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

### Example of File Distribution Using Regular Expressions

To scatter files starting with `host` from the localhost's current directory into a directory named `dest-scatter` directly under the remote host's home directory, use `gm-scatter` as follows.

```:shell
gm-scatter ‘./host.*’ ~/dest-scatter
```

If only `hostfile` exists in the current directory as a file starting with `host`, the execution example would be as follows:

```:shell
$ gm-scatter ‘./host.*’ ~/dest-scatter
$ tree -a dest-scatter
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
