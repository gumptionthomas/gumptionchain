from __future__ import annotations

from typing import Any

# `message` may be a string, bytes, a list of messages, or a mapping
# (marshmallow `validate()` returns dicts like `{'field': ['msg', ...]}`,
# and other modules re-raise nested errors as
# `InvalidBlockError({f'Transaction {txid}': e.messages})`).
# `messages` preserves the original structure so JSON-serializing the
# error in api.py keeps the field-level detail intact.
Message = str | bytes | list[Any] | dict[str, Any]


class CCError(Exception):
    def __init__(self, message: Message | None = None) -> None:
        # Use `is None` instead of truthy check so empty containers
        # ({}, [], "", b"") aren't silently replaced with the class name —
        # an intentionally-empty validation-error dict, for example, is
        # a valid message payload.
        msg: Message = self.__class__.__name__ if message is None else message
        super().__init__(msg)
        self.messages: Message
        if isinstance(msg, (str, bytes)):
            self.messages = [msg]
        else:
            self.messages = msg


class InvalidWalletError(CCError):
    pass


class InvalidKeyError(InvalidWalletError):
    pass


class NoPrivateKeyError(InvalidWalletError):
    pass


class InvalidTransactionError(CCError):
    pass


class DuplicateMinedTransactionError(InvalidTransactionError):
    pass


class InvalidTransactionIdError(InvalidTransactionError):
    pass


class InvalidSignatureError(InvalidTransactionError):
    pass


class FutureTransactionError(InvalidTransactionError):
    pass


class ExpiredTransactionError(InvalidTransactionError):
    pass


class OutOfOrderTransactionError(InvalidTransactionError):
    pass


class UnsealedTransactionError(InvalidTransactionError):
    pass


class MissingWalletError(InvalidTransactionError):
    pass


class InsufficientFundsError(InvalidTransactionError):
    pass


class ImbalancedTransactionError(InvalidTransactionError):
    pass


class MissingInflowOutflowError(InvalidTransactionError):
    pass


class InvalidInflowOutflowError(InvalidTransactionError):
    pass


class InflowOutflowAddressMismatchError(InvalidTransactionError):
    pass


class SpentTransactionError(InvalidTransactionError):
    pass


class InvalidCoinbaseError(InvalidTransactionError):
    pass


class InvalidCoinbaseErrorRewardError(InvalidCoinbaseError):
    pass


class MismatchedCoinbaseError(InvalidCoinbaseError):
    pass


class InvalidBlockError(CCError):
    pass


class DuplicateGenesisError(InvalidBlockError):
    pass


class InvalidBlockHashError(InvalidBlockError):
    pass


class InvalidPreviousHashError(InvalidBlockError):
    pass


class InvalidMerkleRootError(InvalidBlockError):
    pass


class MissingCoinbaseError(InvalidBlockError):
    pass


class SealedBlockError(InvalidBlockError):
    pass


class UnlinkedBlockError(InvalidBlockError):
    pass


class FutureBlockError(InvalidBlockError):
    pass


class InvalidProofError(InvalidBlockError):
    pass


class OutOfOrderBlockError(InvalidBlockError):
    pass


class InvalidBlockIndexError(InvalidBlockError):
    pass


class InvalidTargetError(InvalidBlockError):
    pass


class MissingBlockError(InvalidBlockError):
    pass


class InvalidChainError(CCError):
    pass


class EmptyChainError(InvalidChainError):
    pass


class MissingPreviousBlockError(InvalidChainError):
    pass


class InvalidRoleConfigError(CCError):
    pass


class MempoolFullError(CCError):
    pass
