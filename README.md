# GumptionChain

GumptionChain is an open-source Python project that implements a custom
proof-of-work blockchain ledger. The ledger lets tokens be assigned to
*subjects* (UTF-8 strings of 1–79 characters) as indications of either
**opposition** or **support**. Either kind of stake can be rescinded later.

Tokens are denominated in **GRIT** (1 GRIT = 100 grains). GumptionChain runs as
both a Flask web application (a browser explorer plus a JSON API) and a
`gumptionchain` command-line tool. The network is **permissioned**: API access
is gated by role (`READER` < `TRANSACTOR` < `MILLER` < `ADMIN`).

## Documentation

- **[HTTP API reference](docs/api-reference.md)** — endpoints, roles, payloads.
- **[CLI reference](docs/cli-reference.md)** — the `gumptionchain` command tree.
- **[Configuration reference](docs/configuration.md)** — `FLASK_*` / `GC_*`
  settings and role allowlists.
- **[API authentication protocol](docs/api-auth-protocol.md)** — the
  `gc-sig-v1` per-request signing scheme.
- Full index: [`docs/`](docs/README.md).

## Quick Start

### Requirements

Python >= 3.12

### Install

Install GumptionChain using pip:

```console
$ pip install gumptionchain
```

It is recommended that a [Python virtual environment](https://docs.python.org/3/library/venv.html)
is used.

For development on the project itself, use [uv](https://docs.astral.sh/uv/) to
manage the environment and dependencies:

```console
$ git clone https://github.com/gumptionthomas/gumptionchain.git
$ cd gumptionchain
$ uv sync --group dev
$ uv run gumptionchain --help
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow and quality
gates.

### Configure

Create a [python-dotenv](https://pypi.org/project/python-dotenv/) `.env` file
in the working directory. A minimal configuration:

```ini
# Flask Settings
FLASK_APP=gumptionchain
FLASK_SECRET_KEY=change-me-to-a-random-string

# Flask-SQLAlchemy Settings
FLASK_SQLALCHEMY_DATABASE_URI=sqlite:///gc.sqlite
```

`FLASK_SECRET_KEY` should be a unique random string. See the
[configuration reference](docs/configuration.md) for all available settings.

### Initialize

Create a local database (this applies all schema migrations):

```console
$ gumptionchain init
```

The example `FLASK_SQLALCHEMY_DATABASE_URI` above specifies a
[SQLite](https://sqlite.org/index.html) database called `gc.sqlite`, relative
to the `gumptionchain`
[instance folder](https://flask.palletsprojects.com/en/stable/config/#instance-folders).

### Import

The `import` command bulk-loads blocks from a [JSON Lines](https://jsonlines.org/)
export — for example, a file produced by `gumptionchain export` on another node:

```console
$ gumptionchain import path/to/gumptionchain.jsonl
```

This can take a while depending on your machine and the number of blocks; a
progress bar shows estimated time remaining. The command is idempotent — run it
again and only new blocks are imported.

### Run

Run the application with the `run` command:

```console
$ gumptionchain run
```

Open [http://localhost:5000](http://localhost:5000) in a browser to explore the
local copy of the blockchain.

#### Home Page (Current Chain)

<img src="https://github.com/gumptionthomas/gumptionchain/blob/7a4fab66dfe6026e56c79df3e147b1ecbdbb6158/readme-assets/browser-chain.png?raw=true" width="500">

#### Block Page

<img src="https://github.com/gumptionthomas/gumptionchain/blob/7a4fab66dfe6026e56c79df3e147b1ecbdbb6158/readme-assets/browser-block.png?raw=true" width="500">

#### Transaction Page

<img src="https://github.com/gumptionthomas/gumptionchain/blob/7a4fab66dfe6026e56c79df3e147b1ecbdbb6158/readme-assets/browser-txn.png?raw=true" width="500">

Running the application also exposes the JSON API that forms the communications
layer of the blockchain — see the [API reference](docs/api-reference.md). For
the other commands, see the [CLI reference](docs/cli-reference.md) or run
`gumptionchain --help`.

## Joining the GumptionChain Network

GumptionChain is run by a permissioned network of nodes. To have locally milled
blocks or submitted transactions propagate to the official chain, a node needs
[miller](docs/configuration.md#role-allowlists) or
[transactor](docs/configuration.md#role-allowlists) role access to a node in
the network.

API access is granted by a node's operator. Once your signing-key address is on
a node's role allowlist, configure that node as a peer. Replace
`GCYourAddressGC` with your signing-key address, `peer.example.com` with the
node's host, and `/path/to/signing_keys` with the directory containing your key
([PEM](https://en.wikipedia.org/wiki/Privacy-Enhanced_Mail)) file:

```ini
# GumptionChain Settings
GC_NODE_HOST=http://GCYourAddressGC@localhost:5000
GC_PEERS=["https://GCYourAddressGC@peer.example.com"]
GC_DEFAULT_COMMAND_HOST=https://GCYourAddressGC@peer.example.com
GC_SIGNING_KEY_DIR=/path/to/signing_keys
```

Restart to load the new configuration. See the
[configuration reference](docs/configuration.md) for details.

With `READER` access, the [`sync` command](docs/cli-reference.md) updates your
chain to the most recent peer block data:

```console
$ gumptionchain sync
```

Like `import`, `sync` is idempotent and only fetches blocks you don't yet have.
Reader access also allows querying data (subject totals and balances) via the
CLI.

To request access to a node in the GumptionChain network, email
**thomas@gumption.com** with the role you'd like (reader, transactor, or
miller) and how you intend to use it (e.g. research, business, non-profit,
hobby).

## License

GumptionChain is released under the [MIT License](LICENSE).
