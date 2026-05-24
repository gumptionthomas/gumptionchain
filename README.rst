CancelChain
###########

CancelChain is an open-source python project that implements a custom blockchain ledger. The ledger protocol allows for the assigning of tokens to subjects (utf-8 strings of less than 80 characters) as indications of either opposition or support. Opposition entries are allowed to be rescinded later. Support is forever.

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

Install CancelChain using pip:

.. code-block:: console

  $ pip install cancelchain

It is recommended that a `python virtual environment`_ is used for `all <https://realpython.com/python-virtual-environments-a-primer/#avoid-system-pollution>`__ `the <https://realpython.com/python-virtual-environments-a-primer/#sidestep-dependency-conflicts>`__ `usual <https://realpython.com/python-virtual-environments-a-primer/#minimize-reproducibility-issues>`__ `reasons <https://realpython.com/python-virtual-environments-a-primer/#dodge-installation-privilege-lockouts>`_.

For development on the project itself, use `uv`_ to manage the environment and dependencies:

.. code-block:: console

  $ git clone https://github.com/gumptionthomas/cancelchain.git
  $ cd cancelchain
  $ uv sync --group dev
  $ uv run cancelchain --help

Configure
---------

Create a `python-dotenv`_ ``.env`` file. The ``cancelchain`` command loads a ``.env`` file in the current working directory by default.  See `dotenv documentation`_ to locate the file elsewhere. The following ``cancelchain`` command examples assume that the ``.env`` file is loaded by default.

A minimal ``.env`` configuration file:

.. code-block:: console

  # Flask Settings
  FLASK_APP=cancelchain
  FLASK_SECRET_KEY=0b6ceaa3b10d3e7a5dc53194

  # Flask-SQLAlchemy Settings
  FLASK_SQLALCHEMY_DATABASE_URI=sqlite:///cc.sqlite

The `FLASK_SECRET_KEY`_ value should be a unique random string.

See the `Configuration Documentation`_ for more configuration settings.


Initialize
----------

Create a local database by running the `init command`_:

.. code-block:: console

  $ cancelchain init

The `FLASK_SQLALCHEMY_DATABASE_URI`_ value in the example configuration above specifies a `SQLite`_ database called ``cc.sqlite`` with a file path relative to the ``cancelchain`` `instance folder`_.


Import
------

Download the most recent export of `CancelChain data`_. This `JSON Lines`_ file is updated at every blockchain epoch (2016 blocks or approximately every two weeks).

Run the `import command`_, passing it the location of the downloaded file:

.. code-block:: console

  $ cancelchain import path/to/cancelchain.jsonl

This command could take a while to run depending on your computer and the number of blocks imported. A progress bar will display with estimated time remaining. You can run the ``import`` command multiple times and it will only import new blocks that are not yet in the database.


Run
---

Run the ``cancelchain`` application by issuing the ``run`` command:

.. code-block:: console

  $ cancelchain run

Open `http://localhost:5000 <http://localhost:5000>`_ in a browser to explore the local copy of the blockchain.

Home Page (Current Chain)
^^^^^^^^^^^^^^^^^^^^^^^^^

.. image:: https://github.com/gumptionthomas/cancelchain/blob/7a4fab66dfe6026e56c79df3e147b1ecbdbb6158/readme-assets/browser-chain.png?raw=true
   :width: 500pt

Block Page
^^^^^^^^^^

.. image:: https://github.com/gumptionthomas/cancelchain/blob/7a4fab66dfe6026e56c79df3e147b1ecbdbb6158/readme-assets/browser-block.png?raw=true
   :width: 500pt

Transaction Page
^^^^^^^^^^^^^^^^

.. image:: https://github.com/gumptionthomas/cancelchain/blob/7a4fab66dfe6026e56c79df3e147b1ecbdbb6158/readme-assets/browser-txn.png?raw=true
   :width: 500pt

Running the ``cancelchain`` application also exposes a set of web service endpoints that comprise the communications layer of the blockchain. See the  `API Documentation`_ for more information.

There are other ``cancelchain`` commands for interacting with the blockchain. See the `Command Line Interface Documentation`_ for more information or run ``cancelchain --help``.


Joining The CancelChain Network
===============================

The CancelChain is run by a permissioned network of nodes. A CancelChain instance requires `miller`_ or `transactor`_ role `API access`_ to a node in the network in order to have locally milled blocks or submitted transactions propagate to the official CancelChain.

`The Cancel Button`_ allows `reader`_ role `API access`_ to any account that completes at least one transaction on the blockchain:

1) `Register for an account`_.
2) Submit a successful transaction for any subject. Access won't be granted until the sentiment transaction successfully completes.
3) Click `Download Account Key`_ on the `account page`_ to download the account's key (`PEM`_) file.
4) Create a directory called ``wallets`` and copy the downloaded key file into it.
5) Add the following settings to the ``.env`` configuration file. Replace ``CCTheCancelButtonAddressCC`` with the address on the `account page`_ and ``/path/to/wallet`` with the path to the ``wallets`` directory created above:

  .. code-block:: console

    # CancelChain Settings
    CC_NODE_HOST=http://CCTheCancelButtonAddressCC@localhost:5000
    CC_PEERS=["https://CCTheCancelButtonAddressCC@thecancelbutton.com"]
    CC_DEFAULT_COMMAND_HOST=https://CCTheCancelButtonAddressCC@thecancelbutton.com
    CC_WALLET_DIR=/path/to/wallets

6) Restart to load the new configuration.

See `Configuration Documentation`_ for more detailed information about these settings.

The `reader`_ role `API access`_ allows the `sync command`_ to update to the most recent peer block data:

.. code-block:: console

  $ cancelchain sync

This command could take a while to run depending on your computer, internet access, and the number of blocks synchronized. A progress bar will display with estimated time remaining. You can run the `sync command`_ multiple times and it will only synchronize new blocks that are not yet in the database.

Reader access also allows querying data (i.e. subject counts and balances) using the CLI. See `Command Line Interface Documentation`_ for more information.

If you would like to be granted other `API access`_ to a node in the CancelChain network, send an email to contact@cancelchain.org including what kind of role you'd like (e.g. `reader`_, `transactor`_, or `miller`_) and how you intend to use it (e.g. research, business, non-profit, hobby).

See the `documentation`_ for some potential development ideas.


.. _account page: https://thecancelbutton.com/account
.. _API access: https://docs.cancelchain.org/en/latest/api.html#api-roles
.. _API Documentation: https://docs.cancelchain.org/en/latest/api.html
.. _Blog: https://blog.cancelchain.org
.. _CancelChain data: https://storage.googleapis.com/blocks.cancelchain.org/cancelchain.jsonl
.. _FLASK_SECRET_KEY: https://docs.cancelchain.org/en/latest/usage.html#SECRET_KEY
.. _FLASK_SQLALCHEMY_DATABASE_URI: https://docs.cancelchain.org/en/latest/usage.html#SQLALCHEMY_DATABASE_URI
.. _Command Line Interface Documentation: https://docs.cancelchain.org/en/latest/usage.html#command-line-interface
.. _Configuration Documentation: https://docs.cancelchain.org/en/latest/usage.html#configuration
.. _documentation: https://docs.cancelchain.org
.. _Documentation: https://docs.cancelchain.org
.. _dotenv documentation: https://docs.cancelchain.org/en/latest/usage.html#dotenv
.. _Download Account Key: https://thecancelbutton.com/pem
.. _import command: https://docs.cancelchain.org/en/latest/usage.html#import
.. _init command: https://docs.cancelchain.org/en/latest/usage.html#init
.. _instance folder: https://flask.palletsprojects.com/en/2.2.x/config/#instance-folders
.. _JSON Lines: https://jsonlines.org/
.. _miller: https://docs.cancelchain.org/en/latest/api.html#miller
.. _PEM: https://en.wikipedia.org/wiki/Privacy-Enhanced_Mail
.. _Project Home Page: https://cancelchain.org
.. _python virtual environment: https://docs.python.org/3/library/venv.html
.. _python-dotenv: https://pypi.org/project/python-dotenv/
.. _reader: https://docs.cancelchain.org/en/latest/api.html#reader
.. _Register for an account: https://thecancelbutton.com/register
.. _SQLite: https://sqlite.org/index.html
.. _sync command: https://docs.cancelchain.org/en/latest/usage.html#sync
.. _The Cancel Button: https://thecancelbutton.com
.. _transactor: https://docs.cancelchain.org/en/latest/api.html#transactor
.. _uv: https://docs.astral.sh/uv/
