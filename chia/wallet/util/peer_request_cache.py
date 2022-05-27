import asyncio
from typing import Optional

from chia.protocols.wallet_protocol import CoinState, RespondSESInfo
from chia.types.blockchain_format.reward_chain_block import RewardChainBlock
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.types.header_block import HeaderBlock
from chia.util.hash import std_hash
from chia.util.ints import uint32, uint64
from chia.util.lru_cache import LRUCache


class PeerRequestCache:
    _blocks: LRUCache  # height -> HeaderBlock
    _block_requests: LRUCache  # (start, end) -> RequestHeaderBlocks
    _ses_requests: LRUCache  # height -> Ses request
    _states_validated: LRUCache  # coin state hash -> last change height, or None for reorg
    _timestamps: LRUCache  # block height -> timestamp
    _blocks_validated: LRUCache  # header_hash -> height
    _block_signatures_validated: LRUCache  # header_hash -> height

    def __init__(self):
        self._blocks = LRUCache(100)
        self._block_requests = LRUCache(100)
        self._ses_requests = LRUCache(100)
        self._states_validated = LRUCache(1000)
        self._timestamps = LRUCache(1000)
        self._blocks_validated = LRUCache(1000)
        self._block_signatures_validated = LRUCache(1000)

    def get_block(self, height: uint32) -> Optional[HeaderBlock]:
        return self._blocks.get(height)

    def add_to_blocks(self, header_block: HeaderBlock) -> None:
        self._blocks.put(header_block.height, header_block)
        if header_block.is_transaction_block:
            assert header_block.foliage_transaction_block is not None
            if self._timestamps.get(header_block.height) is None:
                self._timestamps.put(header_block.height, header_block.foliage_transaction_block.timestamp)

    def get_block_request(self, start: uint32, end: uint32) -> Optional[asyncio.Task]:
        return self._block_requests.get((start, end))

    def add_to_block_requests(self, start: uint32, end: uint32, request: asyncio.Task) -> None:
        self._block_requests.put((start, end), request)

    def get_ses_request(self, height: uint32) -> Optional[RespondSESInfo]:
        return self._ses_requests.get(height)

    def add_to_ses_requests(self, height: uint32, ses: RespondSESInfo) -> None:
        self._ses_requests.put(height, ses)

    def in_states_validated(self, coin_state_hash: bytes32) -> bool:
        return self._states_validated.get(coin_state_hash) is not None

    def add_to_states_validated(self, coin_state: CoinState) -> None:
        cs_height: Optional[uint32] = None
        if coin_state.spent_height is not None:
            cs_height = coin_state.spent_height
        elif coin_state.created_height is not None:
            cs_height = coin_state.created_height
        self._states_validated.put(coin_state.get_hash(), cs_height)

    def get_height_timestamp(self, height: uint32) -> Optional[uint64]:
        return self._timestamps.get(height)

    def add_to_blocks_validated(self, reward_chain_hash: bytes32, height: uint32):
        self._blocks_validated.put(reward_chain_hash, height)

    def in_blocks_validated(self, reward_chain_hash: bytes32) -> bool:
        return self._blocks_validated.get(reward_chain_hash) is not None

    def add_to_block_signatures_validated(self, block: HeaderBlock):
        sig_hash: bytes = self._calculate_sig_hash_from_block(block)
        self._block_signatures_validated.put(sig_hash, block.height)

    def _calculate_sig_hash_from_block(self, block: HeaderBlock) -> bytes32:
        return std_hash(
            bytes(block.reward_chain_block.proof_of_space.plot_public_key)
            + bytes(block.foliage.foliage_block_data)
            + bytes(block.foliage.foliage_block_data_signature)
        )

    def in_block_signatures_validated(self, block: HeaderBlock) -> bool:
        sig_hash: bytes = self._calculate_sig_hash_from_block(block)
        return self._block_signatures_validated.get(sig_hash) is not None

    def clear_after_height(self, height: int):
        # Remove any cached item which relates to an event that happened at a height above height.
        new_blocks = LRUCache(self._blocks.capacity)
        for k, v in self._blocks.cache.items():
            if k <= height:
                new_blocks.put(k, v)
        self._blocks = new_blocks

        new_block_requests = LRUCache(self._block_requests.capacity)
        for k, v in self._block_requests.cache.items():
            if k[0] <= height and k[1] <= height:
                new_block_requests.put(k, v)
        self._block_requests = new_block_requests

        new_ses_requests = LRUCache(self._ses_requests.capacity)
        for k, v in self._ses_requests.cache.items():
            if k <= height:
                new_ses_requests.put(k, v)
        self._ses_requests = new_ses_requests

        new_states_validated = LRUCache(self._states_validated.capacity)
        for k, cs_height in self._states_validated.cache.items():
            if cs_height is not None and cs_height <= height:
                new_states_validated.put(k, cs_height)
        self._states_validated = new_states_validated

        new_timestamps = LRUCache(self._timestamps.capacity)
        for h, ts in self._timestamps.cache.items():
            if h <= height:
                new_timestamps.put(h, ts)
        self._timestamps = new_timestamps

        new_blocks_validated = LRUCache(self._blocks_validated.capacity)
        for hh, h in self._blocks_validated.cache.items():
            if h <= height:
                new_blocks_validated.put(hh, h)
        self._blocks_validated = new_blocks_validated

        new_block_signatures_validated = LRUCache(self._block_signatures_validated.capacity)
        for sig_hash, h in self._block_signatures_validated.cache.items():
            if h <= height:
                new_block_signatures_validated.put(sig_hash, h)
        self._block_signatures_validated = new_block_signatures_validated


async def can_use_peer_request_cache(
    coin_state: CoinState, peer_request_cache: PeerRequestCache, fork_height: Optional[uint32]
):
    if not peer_request_cache.in_states_validated(coin_state.get_hash()):
        return False
    if fork_height is None:
        return True
    if coin_state.created_height is None and coin_state.spent_height is None:
        # Performing a reorg
        return False
    if coin_state.created_height is not None and coin_state.created_height > fork_height:
        return False
    if coin_state.spent_height is not None and coin_state.spent_height > fork_height:
        return False
    return True
