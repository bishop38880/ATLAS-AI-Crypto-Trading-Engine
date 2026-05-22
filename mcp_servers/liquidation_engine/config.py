from decimal import Decimal
from typing import Final

# OnChainIntelligenceAgent - Forced Seller Orderbook & Liquidation Cascades
# Configuration for EVM and Solana Protocols

# EVM: Aave V3 + Multicall3
AAVE_V3_POOL_DATA_PROVIDER: Final[str] = "0x7B4EBb512CF17670f9957375667d4E9562787820"
MULTICALL3_ADDRESS: Final[str] = "0xcA11bde05977b3631167028862bE2a173976CA11"

# Solana: Kamino Finance
KAMINO_PROGRAM_ID: Final[str] = "KLend2g6S6YED967TL676LgSLTig77J7T8VKKpUCtE"

# Minimal ABIs
AAVE_DATA_PROVIDER_ABI: Final[list] = [
    {
        "inputs": [{"internalType": "address", "name": "user", "type": "address"}],
        "name": "getUserAccountData",
        "outputs": [
            {"internalType": "uint256", "name": "totalCollateralBase", "type": "uint256"},
            {"internalType": "uint256", "name": "totalDebtBase", "type": "uint256"},
            {"internalType": "uint256", "name": "availableBorrowsBase", "type": "uint256"},
            {"internalType": "uint256", "name": "currentLiquidationThreshold", "type": "uint256"},
            {"internalType": "uint256", "name": "ltv", "type": "uint256"},
            {"internalType": "uint256", "name": "healthFactor", "type": "uint256"}
        ],
        "stateMutability": "view",
        "type": "function"
    }
]

MULTICALL3_ABI: Final[list] = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "target", "type": "address"},
                    {"internalType": "bytes", "name": "callData", "type": "bytes"}
                ],
                "internalType": "struct Multicall3.Call[]",
                "name": "calls",
                "type": "tuple[]"
            }
        ],
        "name": "aggregate",
        "outputs": [
            {"internalType": "uint256", "name": "blockNumber", "type": "uint256"},
            {"internalType": "bytes[]", "name": "returnData", "type": "bytes[]"}
        ],
        "stateMutability": "payable",
        "type": "function"
    }
]

# Seed Whale Addresses (Aave Mainnet/Arbitrum)
EVM_WHALES: Final[list[str]] = [
    "0x55FE002aef0AC2d71f4216222f5e92Ec1d746533",
    "0x6B19830887413642131Ff2065851410119e79878",
    "0x981C06C788d77e4C0f803c683868E426466f8e7b",
    "0x4296720D235C7A537c02bB846c4A31780824E3a1",
    "0x789b7A74092E6b92D46927a41B64654D2B84D233"
]

# Seed Kamino Obligation Addresses (Solana)
SOL_OBLIGATIONS: Final[list[str]] = [
    "9uQ3YxZc4y7X7F8VKKpUCtE9uQ3YxZc4y7X7F8VKKp",  # Mock
    "7X7F8VKKpUCtE9uQ3YxZc4y7X7F8VKKpUCtE9uQ3Y",  # Mock
]

# Precision Constants
RAY: Final[Decimal] = Decimal("10") ** 27
WAD: Final[Decimal] = Decimal("10") ** 18
BASE_CURRENCY_UNIT: Final[Decimal] = Decimal("10") ** 8  # Aave totalCollateralBase is in 8 decimals (USD)
