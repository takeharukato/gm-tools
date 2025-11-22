Name:           gm-tools
Version:        0.1.0
Release:        1%{?dist}
Summary:        gm-gather / gm-scatter file transfer tools

License:        BSD
URL:            https://github.com/takeharukato/gm-tools
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch

BuildRequires:  python3
BuildRequires:  python3-devel
BuildRequires:  python3-setuptools
BuildRequires:  gettext
BuildRequires:  autoconf
BuildRequires:  automake
BuildRequires:  make
BuildRequires:  gcc

Requires:       python3 >= 3.9
Requires:       python3-paramiko

%description
gm-tools provides gm-gather and gm-scatter commands for file collection
and distribution over SSH, with i18n support.

%prep
%autosetup -n %{name}-%{version}

%build
./autogen.sh
%configure PYTHON=python3
%make_build

%install
rm -rf %{buildroot}
%make_install

%files
%license LICENSE
# Documents
%doc %{_docdir}/gm-tools/common-specJP.md
%doc %{_docdir}/gm-tools/gm-gather-specJP.md
%doc %{_docdir}/gm-tools/gm-scatter-specJP.md
%doc %{_docdir}/gm-tools/Readme-command-completion.md
%doc %{_docdir}/gm-tools/ReadmeJP-command-completion.md
%doc %{_docdir}/gm-tools/config/hostfile.sample

# scripts to invoke
%{_bindir}/gm-gather
%{_bindir}/gm-scatter

# Python modules
%dir %{python3_sitelib}/gm_tools
%{python3_sitelib}/gm_tools/*

# locale
%dir %{_datadir}/locale
%{_datadir}/locale/*/LC_MESSAGES/gm-tools.mo

# shell completions
%{_datadir}/bash-completion/completions/gm-gather.bash
%{_datadir}/bash-completion/completions/gm-scatter.bash
%{_datadir}/zsh/site-functions/_gm-gather
%{_datadir}/zsh/site-functions/_gm-scatter

# man pages
%{_mandir}/man1/gm-gather.1*
%{_mandir}/man1/gm-scatter.1*
%lang(ja) %{_mandir}/ja/man1/gm-gather.1*
%lang(ja) %{_mandir}/ja/man1/gm-scatter.1*

%changelog
* Sat Nov 15 2025 Takeharu Kato <tkato1219@gmail.com> - 0.1.0-1
- Initial RPM package for gm-tools
