# GumptionChain

GumptionChain is an open-source python project that implements a custom blockchain ledger. The ledger protocol allows for the assigning of tokens to subjects (utf-8 strings of less than 80 characters) as indications of either opposition or support. Either kind of entry may be rescinded later.

- [Project Home Page](https://gumption.com/chain)
- [Documentation](https://gumption.com/chain/docs)
- [Blog](https://gumption.com/chain/blog)

## Quick Start

### Requirements

Python >= 3.12

### Install

Install GumptionChain using pip:

```console
$ pip install gumptionchain
```

It is recommended that a [python virtual environment](https://docs.python.org/3/library/venv.html) is used for [all](https://realpython.com/python-virtual-environments-a-primer/#avoid-system-pollution) [the](https://realpython.com/python-virtual-environments-a-primer/#sidestep-dependency-conflicts) [usual](https://realpython.com/python-virtual-environments-a-primer/#minimize-reproducibility-issues) [reasons](https://realpython.com/python-virtual-environments-a-primer/#dodge-installation-privilege-lockouts).

For development on the project itself, use [uv](https://docs.astral.sh/uv/) to manage the environment and dependencies:

```console
$ git clone https://github.com/gumptionthomas/gumptionchain.git
$ cd gumptionchain
$ uv sync --group dev
$ uv run gumptionchain --help
```

### Configure

Create a [python-dotenv](https://pypi.org/project/python-dotenv/) `.env` file. The `gumptionchain` command loads a `.env` file in the current working directory by default. See [dotenv documentation](https://gumption.com/chain/docs/en/latest/usage.html#dotenv) to locate the file elsewhere. The following `gumptionchain` command examples assume that the `.env` file is loaded by default.

A minimal `.env` configuration file:

```console
# Flask Settings
FLASK_APP=gumptionchain
FLASK_SECRET_KEY=0b6ceaa3b10d3e7a5dc53194

# Flask-SQLAlchemy Settings
FLASK_SQLALCHEMY_DATABASE_URI=sqlite:///gc.sqlite
```

The [FLASK_SECRET_KEY](https://gumption.com/chain/docs/en/latest/usage.html#SECRET_KEY) value should be a unique random string.

See the [Configuration Documentation](https://gumption.com/chain/docs/en/latest/usage.html#configuration) for more configuration settings.

### Initialize

Create a local database by running the [init command](https://gumption.com/chain/docs/en/latest/usage.html#init):

```console
$ gumptionchain init
```

The [FLASK_SQLALCHEMY_DATABASE_URI](https://gumption.com/chain/docs/en/latest/usage.html#SQLALCHEMY_DATABASE_URI) value in the example configuration above specifies a [SQLite](https://sqlite.org/index.html) database called `gc.sqlite` with a file path relative to the `gumptionchain` [instance folder](https://flask.palletsprojects.com/en/2.2.x/config/#instance-folders).

### Import

The `import` command bulk-loads blocks from a [JSON Lines](https://jsonlines.org/) export — for example, a file produced by the `gumptionchain export` command on another node.

Run the [import command](https://gumption.com/chain/docs/en/latest/usage.html#import), passing it the location of the export file:

```console
$ gumptionchain import path/to/gumptionchain.jsonl
```

This command could take a while to run depending on your computer and the number of blocks imported. A progress bar will display with estimated time remaining. You can run the `import` command multiple times and it will only import new blocks that are not yet in the database.

### Run

Run the `gumptionchain` application by issuing the `run` command:

```console
$ gumptionchain run
```

Open [http://localhost:5000](http://localhost:5000) in a browser to explore the local copy of the blockchain.

#### Home Page (Current Chain)

<img src="https://github.com/gumptionthomas/gumptionchain/blob/7a4fab66dfe6026e56c79df3e147b1ecbdbb6158/readme-assets/browser-chain.png?raw=true" width="500">

#### Block Page

<img src="https://github.com/gumptionthomas/gumptionchain/blob/7a4fab66dfe6026e56c79df3e147b1ecbdbb6158/readme-assets/browser-block.png?raw=true" width="500">

#### Transaction Page

<img src="https://github.com/gumptionthomas/gumptionchain/blob/7a4fab66dfe6026e56c79df3e147b1ecbdbb6158/readme-assets/browser-txn.png?raw=true" width="500">

Running the `gumptionchain` application also exposes a set of web service endpoints that comprise the communications layer of the blockchain. See the [API Documentation](https://gumption.com/chain/docs/en/latest/api.html) for more information.

There are other `gumptionchain` commands for interacting with the blockchain. See the [Command Line Interface Documentation](https://gumption.com/chain/docs/en/latest/usage.html#command-line-interface) for more information or run `gumptionchain --help`.

## Joining The GumptionChain Network

The GumptionChain is run by a permissioned network of nodes. A GumptionChain instance requires [miller](https://gumption.com/chain/docs/en/latest/api.html#miller) or [transactor](https://gumption.com/chain/docs/en/latest/api.html#transactor) role [API access](https://gumption.com/chain/docs/en/latest/api.html#api-roles) to a node in the network in order to have locally milled blocks or submitted transactions propagate to the official GumptionChain.

[API access](https://gumption.com/chain/docs/en/latest/api.html#api-roles) to a node is granted by that node's operator. Once your signing_key address is on a node's role allowlist (see below to request access), configure your instance to use that node as a peer. Replace `GCYourSigningKeyAddressGC` with your signing_key address, `peer.example.com` with the host of the node you've been granted access to, and `/path/to/signing_keys` with the path to a directory containing your key ([PEM](https://en.wikipedia.org/wiki/Privacy-Enhanced_Mail)) file:

```console
# GumptionChain Settings
GC_NODE_HOST=http://GCYourSigningKeyAddressGC@localhost:5000
GC_PEERS=["https://GCYourSigningKeyAddressGC@peer.example.com"]
GC_DEFAULT_COMMAND_HOST=https://GCYourSigningKeyAddressGC@peer.example.com
GC_SIGNING_KEY_DIR=/path/to/signing_keys
```

Restart to load the new configuration.

See [Configuration Documentation](https://gumption.com/chain/docs/en/latest/usage.html#configuration) for more detailed information about these settings.

The [reader](https://gumption.com/chain/docs/en/latest/api.html#reader) role [API access](https://gumption.com/chain/docs/en/latest/api.html#api-roles) allows the [sync command](https://gumption.com/chain/docs/en/latest/usage.html#sync) to update to the most recent peer block data:

```console
$ gumptionchain sync
```

This command could take a while to run depending on your computer, internet access, and the number of blocks synchronized. A progress bar will display with estimated time remaining. You can run the `sync command` multiple times and it will only synchronize new blocks that are not yet in the database.

Reader access also allows querying data (i.e. subject counts and balances) using the CLI. See [Command Line Interface Documentation](https://gumption.com/chain/docs/en/latest/usage.html#command-line-interface) for more information.

If you would like to be granted other [API access](https://gumption.com/chain/docs/en/latest/api.html#api-roles) to a node in the GumptionChain network, send an email to contact@gumption.com including what kind of role you'd like (e.g. [reader](https://gumption.com/chain/docs/en/latest/api.html#reader), [transactor](https://gumption.com/chain/docs/en/latest/api.html#transactor), or [miller](https://gumption.com/chain/docs/en/latest/api.html#miller)) and how you intend to use it (e.g. research, business, non-profit, hobby).

See the [documentation](https://gumption.com/chain/docs) for some potential development ideas.
