GumptionChain
#############

GumptionChain is an open-source python project that implements a custom blockchain ledger. The ledger protocol allows for the assigning of tokens to subjects (utf-8 strings of less than 80 characters) as indications of either opposition or support. Opposition entries are allowed to be rescinded later. Support is forever.

* `Project Home Page`_
* `Documentation`_
* `Blog`_


Quick Start
===========

Requirements
------------

Python >= 3.12

Install
-------

Install GumptionChain using pip:

.. code-block:: console

  $ pip install gumptionchain

It is recommended that a `python virtual environment`_ is used for `all <https://realpython.com/python-virtual-environments-a-primer/#avoid-system-pollution>`__ `the <https://realpython.com/python-virtual-environments-a-primer/#sidestep-dependency-conflicts>`__ `usual <https://realpython.com/python-virtual-environments-a-primer/#minimize-reproducibility-issues>`__ `reasons <https://realpython.com/python-virtual-environments-a-primer/#dodge-installation-privilege-lockouts>`_.

For development on the project itself, use `uv`_ to manage the environment and dependencies:

.. code-block:: console

  $ git clone https://github.com/gumptionthomas/gumptionchain.git
  $ cd gumptionchain
  $ uv sync --group dev
  $ uv run gumptionchain --help

Configure
---------

Create a `python-dotenv`_ ``.env`` file. The ``gumptionchain`` command loads a ``.env`` file in the current working directory by default.  See `dotenv documentation`_ to locate the file elsewhere. The following ``gumptionchain`` command examples assume that the ``.env`` file is loaded by default.

A minimal ``.env`` configuration file:

.. code-block:: console

  # Flask Settings
  FLASK_APP=gumptionchain
  FLASK_SECRET_KEY=0b6ceaa3b10d3e7a5dc53194

  # Flask-SQLAlchemy Settings
  FLASK_SQLALCHEMY_DATABASE_URI=sqlite:///gc.sqlite

The `FLASK_SECRET_KEY`_ value should be a unique random string.

See the `Configuration Documentation`_ for more configuration settings.


Initialize
----------

Create a local database by running the `init command`_:

.. code-block:: console

  $ gumptionchain init

The `FLASK_SQLALCHEMY_DATABASE_URI`_ value in the example configuration above specifies a `SQLite`_ database called ``gc.sqlite`` with a file path relative to the ``gumptionchain`` `instance folder`_.


Import
------

The ``import`` command bulk-loads blocks from a `JSON Lines`_ export — for example, a file produced by the ``gumptionchain export`` command on another node.

Run the `import command`_, passing it the location of the export file:

.. code-block:: console

  $ gumptionchain import path/to/gumptionchain.jsonl

This command could take a while to run depending on your computer and the number of blocks imported. A progress bar will display with estimated time remaining. You can run the ``import`` command multiple times and it will only import new blocks that are not yet in the database.


Run
---

Run the ``gumptionchain`` application by issuing the ``run`` command:

.. code-block:: console

  $ gumptionchain run

Open `http://localhost:5000 <http://localhost:5000>`_ in a browser to explore the local copy of the blockchain.

Home Page (Current Chain)
^^^^^^^^^^^^^^^^^^^^^^^^^

.. image:: https://github.com/gumptionthomas/gumptionchain/blob/7a4fab66dfe6026e56c79df3e147b1ecbdbb6158/readme-assets/browser-chain.png?raw=true
   :width: 500pt

Block Page
^^^^^^^^^^

.. image:: https://github.com/gumptionthomas/gumptionchain/blob/7a4fab66dfe6026e56c79df3e147b1ecbdbb6158/readme-assets/browser-block.png?raw=true
   :width: 500pt

Transaction Page
^^^^^^^^^^^^^^^^

.. image:: https://github.com/gumptionthomas/gumptionchain/blob/7a4fab66dfe6026e56c79df3e147b1ecbdbb6158/readme-assets/browser-txn.png?raw=true
   :width: 500pt

Running the ``gumptionchain`` application also exposes a set of web service endpoints that comprise the communications layer of the blockchain. See the  `API Documentation`_ for more information.

There are other ``gumptionchain`` commands for interacting with the blockchain. See the `Command Line Interface Documentation`_ for more information or run ``gumptionchain --help``.


Joining The GumptionChain Network
=================================

The GumptionChain is run by a permissioned network of nodes. A GumptionChain instance requires `miller`_ or `transactor`_ role `API access`_ to a node in the network in order to have locally milled blocks or submitted transactions propagate to the official GumptionChain.

`API access`_ to a node is granted by that node's operator. Once your wallet address is on a node's role allowlist (see below to request access), configure your instance to use that node as a peer. Replace ``GCYourWalletAddressGC`` with your wallet address, ``peer.example.com`` with the host of the node you've been granted access to, and ``/path/to/wallets`` with the path to a directory containing your key (`PEM`_) file:

.. code-block:: console

    # GumptionChain Settings
    GC_NODE_HOST=http://GCYourWalletAddressGC@localhost:5000
    GC_PEERS=["https://GCYourWalletAddressGC@peer.example.com"]
    GC_DEFAULT_COMMAND_HOST=https://GCYourWalletAddressGC@peer.example.com
    GC_WALLET_DIR=/path/to/wallets

Restart to load the new configuration.

See `Configuration Documentation`_ for more detailed information about these settings.

The `reader`_ role `API access`_ allows the `sync command`_ to update to the most recent peer block data:

.. code-block:: console

  $ gumptionchain sync

This command could take a while to run depending on your computer, internet access, and the number of blocks synchronized. A progress bar will display with estimated time remaining. You can run the `sync command`_ multiple times and it will only synchronize new blocks that are not yet in the database.

Reader access also allows querying data (i.e. subject counts and balances) using the CLI. See `Command Line Interface Documentation`_ for more information.

If you would like to be granted other `API access`_ to a node in the GumptionChain network, send an email to contact@gumption.com including what kind of role you'd like (e.g. `reader`_, `transactor`_, or `miller`_) and how you intend to use it (e.g. research, business, non-profit, hobby).

See the `documentation`_ for some potential development ideas.


.. _API access: https://gumption.com/chain/docs/en/latest/api.html#api-roles
.. _API Documentation: https://gumption.com/chain/docs/en/latest/api.html
.. _Blog: https://gumption.com/chain/blog
.. _FLASK_SECRET_KEY: https://gumption.com/chain/docs/en/latest/usage.html#SECRET_KEY
.. _FLASK_SQLALCHEMY_DATABASE_URI: https://gumption.com/chain/docs/en/latest/usage.html#SQLALCHEMY_DATABASE_URI
.. _Command Line Interface Documentation: https://gumption.com/chain/docs/en/latest/usage.html#command-line-interface
.. _Configuration Documentation: https://gumption.com/chain/docs/en/latest/usage.html#configuration
.. _documentation: https://gumption.com/chain/docs
.. _Documentation: https://gumption.com/chain/docs
.. _dotenv documentation: https://gumption.com/chain/docs/en/latest/usage.html#dotenv
.. _import command: https://gumption.com/chain/docs/en/latest/usage.html#import
.. _init command: https://gumption.com/chain/docs/en/latest/usage.html#init
.. _instance folder: https://flask.palletsprojects.com/en/2.2.x/config/#instance-folders
.. _JSON Lines: https://jsonlines.org/
.. _miller: https://gumption.com/chain/docs/en/latest/api.html#miller
.. _PEM: https://en.wikipedia.org/wiki/Privacy-Enhanced_Mail
.. _Project Home Page: https://gumption.com/chain
.. _python virtual environment: https://docs.python.org/3/library/venv.html
.. _python-dotenv: https://pypi.org/project/python-dotenv/
.. _reader: https://gumption.com/chain/docs/en/latest/api.html#reader
.. _SQLite: https://sqlite.org/index.html
.. _sync command: https://gumption.com/chain/docs/en/latest/usage.html#sync
.. _transactor: https://gumption.com/chain/docs/en/latest/api.html#transactor
.. _uv: https://docs.astral.sh/uv/
