# Installation

qsiplan is a pure-Python package on PyPI. It requires Python 3.11 or newer.

```bash
pip install qsiplan
```

To keep it out of your project environments, install it as a standalone
command with pipx:

```bash
pipx install qsiplan
```

qsiplan does not need qsiprep, FSL, TORTOISE, or any container. It reads
BIDS sidecars, `.bval` files, and NIfTI headers, and never runs a
preprocessing tool. qsiprep depends on qsiplan for its own grouping, so a
qsiprep installation already includes it.
