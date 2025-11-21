import os
import sys
from datetime import datetime

ROOT_DIR = os.path.abspath("../..")
sys.path.insert(0, os.path.join(ROOT_DIR, "src"))
sys.path.insert(0, ROOT_DIR)

project = "gm-tools Test Framework"
author = "gm-tools contributors"
copyright = f"{datetime.now().year}, gm-tools"
master_doc = "index"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.doctest",
]
autosummary_generate = True
autosummary_imported_members = False

autodoc_default_options = {
    "members": True,
    "private-members": True,
    "undoc-members": False,
    "show-inheritance": True,
}
autodoc_typehints = "description"

napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_include_init_with_doc = True
napoleon_use_param = True
napoleon_use_rtype = True
napoleon_use_ivar = True
napoleon_attr_annotations = True

templates_path = ["_templates"]
exclude_patterns = [
    "_build",
    "design/modules/_config.*",
    "design/modules/__init__.*",
]

html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]

language = "ja"
