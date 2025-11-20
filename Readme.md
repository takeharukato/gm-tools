# gm-tools

## Overview

gm-tools is a toolset providing `gm-gather` and `gm-scatter` to assist with file collection and distribution across multiple hosts. It uses SSH transfer as standard and includes features for operational environments, such as structured log output and internationalization support.

## Prerequisite packages

- Python 3.9 or later
- [python3-paramiko](https://www.paramiko.org/)
- gettext-0.21 or later
- autoconf-2.69 or later
- automake-1.16 or later
- acl-2.2 or later (if using the Access Control List restoration feature)
- attr-2.4 or later (if using extended attribute restoration)
- sudo-1.8 or later
- policycoreutils (if using SELinux context information restoration; requires a version where the `-RF` option is available with the `restorecon` command)

Additionally, bash and the mktemp command (requiring the `-d` option for template-based directory creation) are required. These should be available on standard Linux distributions and BSD-based operating systems.

## Installation

Installation can be performed by the following steps.

```:shell
./autogen.sh
./configure
make -j
make install
```

## `make` Targets

- `make` : Builds core modules and scripts
- `make install` : Installs executables, modules, and man pages to the system
- `make check` : Runs the main test suite
- `make dist` : Generates a distribution tarball
- `make clean` : Removes generated files
- `make cloc` : Outputs source size (Requires `cloc` command)
- `make rpm`: Generate RPM packages (Requires `docker` command)
- `make deb`: Generate Debian packages (Requires `docker` command)

In the `docs/sphinx` directory, running the `make docs` command generates the test framework and the program's interface specification (Requires Sphinx package).

In the `po` directory, the following make targets are defined:

- `make update-po`: Regenerates the POT file (`gm-tools.pot`), merges it into the respective language PO files (`*.po`), and updates the `.gmo` (binary dictionary).

- `make gm-tools.pot-update` Regenerates only the POT file (also called internally by `make update-po`).
- `make update-gmo` Rebuilds the .gmo from existing PO files. Useful when you want to regenerate only the mo file after manually editing the PO.
- `make` or `make all` Maintains stamp-po and .gmo files (typically called during a build).
- `make install` Installs the translated `.mo` files.
- `make uninstall` Removes the translated `.mo` files.
- `make clean`, `make distclean`, `make maintainer-clean` Delete generated files (`*.gmo`, `stamp-po`, etc.) according to the cleanup level.

## Copyright Notice

This project is distributed under the BSD 2-Clause License.
See the `LICENSE` file for details.
