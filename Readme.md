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

## Copyright Notice

Copyright 2025 Takeharu KATO.

This project is distributed under the BSD 2-Clause License.
See the `LICENSE` file for details.
