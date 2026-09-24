"""Sphinx configuration for qsiplan."""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# autodoc must import this checkout, not whichever qsiplan is installed.
sys.path.insert(0, os.path.dirname(HERE))
# Local extension: tables built from the live registries.
sys.path.insert(0, os.path.join(HERE, '_ext'))

import qsiplan  # noqa: E402

project = 'qsiplan'
author = 'The PennLINC developers'
release = qsiplan.__version__
version = release

extensions = [
    'myst_nb',
    'sphinx.ext.autodoc',
    'sphinx.ext.autosummary',
    'sphinx.ext.napoleon',
    'sphinx.ext.intersphinx',
    'sphinx.ext.viewcode',
    'sphinxarg.ext',
    'qsiplan_tables',
]

templates_path = ['_templates']
exclude_patterns = ['_build', '**.ipynb_checkpoints', '.jupyter_cache', 'Thumbs.db', '.DS_Store']

html_theme = 'sphinx_rtd_theme'

autosummary_generate = True
autodoc_typehints = 'description'
autodoc_member_order = 'bysource'

# Notebooks are executed at build time; 'cache' skips unchanged ones.
nb_execution_mode = 'cache'
nb_execution_raise_on_error = True
nb_execution_timeout = 300

myst_enable_extensions = ['colon_fence', 'deflist']

intersphinx_mapping = {
    'python': ('https://docs.python.org/3', None),
    'qsiprep': ('https://qsiprep.readthedocs.io/en/latest/', None),
}
