/**
 * CoinGecko CDN logo URLs for all POLARIS monitored assets.
 *
 * CANONICAL SOURCE — do not duplicate this constant anywhere in src/.
 * Keys use base symbol without quote suffix (e.g. "BTC" not "BTCUSDT").
 *
 * URLs use CoinGecko `coin-images.coingecko.com` paths (current CDN). Older
 * `assets.coingecko.com` filenames frequently return HTTP 403 after CDN migrations.
 *
 * Before launch: run `npm run verify:logos` to check every URL returns 200.
 * The <AssetLogo> component falls back to a monogram on 404 — a wrong URL
 * degrades gracefully but should still be fixed.
 *
 * Ticker migrations: see session FE-13 notes for MATIC/POL, FTM/S, RNDR/RENDER.
 * Verify against live Bitget USDT-Futures contract list before paper trading.
 */

export const ASSET_LOGOS: Record<string, string> = {

  // ── Core 3 (always on) ──────────────────────────────────────────────────
  BTC: "https://coin-images.coingecko.com/coins/images/1/small/bitcoin.png?1696501400",
  ETH: "https://coin-images.coingecko.com/coins/images/279/small/ethereum.png?1696501628",
  SOL: "https://coin-images.coingecko.com/coins/images/4128/small/solana.png?1718769756",

  // ── Large caps ─────────────────────────────────────────────────────────
  BNB: "https://coin-images.coingecko.com/coins/images/825/small/bnb-icon2_2x.png?1696501970",
  XRP: "https://coin-images.coingecko.com/coins/images/44/small/xrp-symbol-white-128.png?1696501442",
  ADA: "https://coin-images.coingecko.com/coins/images/975/small/cardano.png?1696502090",
  DOGE: "https://coin-images.coingecko.com/coins/images/5/small/dogecoin.png?1696501409",
  AVAX: "https://coin-images.coingecko.com/coins/images/12559/small/Avalanche_Circle_RedWhite_Trans.png?1696512369",
  DOT: "https://coin-images.coingecko.com/coins/images/12171/small/polkadot.jpg?1766533446",
  LINK: "https://coin-images.coingecko.com/coins/images/877/small/Chainlink_Logo_500.png?1760023405",
  LTC: "https://coin-images.coingecko.com/coins/images/2/small/litecoin.png?1696501400",
  BCH: "https://coin-images.coingecko.com/coins/images/780/small/bitcoin-cash-circle.png?1696501932",
  XMR: "https://coin-images.coingecko.com/coins/images/69/small/monero_logo.png?1696501460",
  XLM: "https://coin-images.coingecko.com/coins/images/100/small/fmpFRHHQ_400x400.jpg?1735231350",
  ETC: "https://coin-images.coingecko.com/coins/images/453/small/ethereum-classic-logo.png?1696501717",
  TRX: "https://coin-images.coingecko.com/coins/images/1094/small/photo_2026-04-13_09-59-16.png?1776048311",
  ZEC: "https://coin-images.coingecko.com/coins/images/486/small/circle-zcash-color.png?1696501740",
  EOS: "https://coin-images.coingecko.com/coins/images/738/small/CG_EOS_Icon.png?1731705232",

  // ── Layer 2 / Scaling ──────────────────────────────────────────────────
  // MATIC/POL: `POL` resolves via alias → `MATIC` (same underlying logo).
  ARB: "https://coin-images.coingecko.com/coins/images/16547/small/arb.jpg?1721358242",
  OP: "https://coin-images.coingecko.com/coins/images/25244/small/Token.png?1774456081",
  MATIC: "https://coin-images.coingecko.com/coins/images/4713/small/polygon.png?1698233745",
  IMX: "https://coin-images.coingecko.com/coins/images/17233/small/immutableX-symbol-BLK-RGB.png?1696516787", // VERIFY
  STRK: "https://coin-images.coingecko.com/coins/images/26433/small/starknet.png?1696525507", // VERIFY
  ZK: "https://coin-images.coingecko.com/coins/images/38043/small/ZKTokenBlack.png?1718614502", // VERIFY
  MANTA: "https://coin-images.coingecko.com/coins/images/34289/small/manta.jpg?1704468717", // VERIFY

  // ── Ecosystem / Smart contract platforms ──────────────────────────────
  ATOM: "https://coin-images.coingecko.com/coins/images/1481/small/cosmos_hub.png?1696502525",
  NEAR: "https://coin-images.coingecko.com/coins/images/10365/small/near.jpg?1696510367",
  APT: "https://coin-images.coingecko.com/coins/images/26455/small/Aptos-Network-Symbol-Black-RGB-1x.png?1761789140",
  SUI: "https://coin-images.coingecko.com/coins/images/26375/small/sui-ocean-square.png?1727791290",
  ALGO: "https://coin-images.coingecko.com/coins/images/4380/small/download.png?1696504978",
  ICP: "https://coin-images.coingecko.com/coins/images/14495/small/Internet_Computer_logo.png?1696514180",
  VET: "https://coin-images.coingecko.com/coins/images/1167/small/VET.png?1742383283",
  HBAR: "https://coin-images.coingecko.com/coins/images/3688/small/hbar.png?1696504364", // VERIFY
  FLOW: "https://coin-images.coingecko.com/coins/images/13446/small/5f6294c0c7a8cda55cb1c936_Flow_Wordmark.png?1696513210", // VERIFY
  EGLD: "https://coin-images.coingecko.com/coins/images/12335/small/egld-token-logo.png?1696512162", // VERIFY
  THETA: "https://coin-images.coingecko.com/coins/images/2538/small/theta-token-logo.png?1696503349",
  TON: "https://coin-images.coingecko.com/coins/images/17980/small/photo_2024-09-10_17.09.00.jpeg?1725963446",
  MINA: "https://coin-images.coingecko.com/coins/images/15628/small/JM4_vQ34_400x400.png?1696515261", // VERIFY

  // ── DeFi ──────────────────────────────────────────────────────────────
  UNI: "https://coin-images.coingecko.com/coins/images/12504/small/uniswap-logo.png?1720676669",
  AAVE: "https://coin-images.coingecko.com/coins/images/12645/small/aave-token-round.png?1720472354",
  MKR: "https://coin-images.coingecko.com/coins/images/1364/small/Mark_Maker.png?1696502423",
  CRV: "https://coin-images.coingecko.com/coins/images/12124/small/Curve.png?1696511967",
  SNX: "https://coin-images.coingecko.com/coins/images/3406/small/SNX.png?1696504103",
  COMP: "https://coin-images.coingecko.com/coins/images/10775/small/COMP.png?1696510737",
  SUSHI: "https://coin-images.coingecko.com/coins/images/12271/small/512x512_Logo_no_chop.png?1696512101",
  LDO: "https://coin-images.coingecko.com/coins/images/13573/small/Lido_DAO.png?1696513326",
  DYDX: "https://coin-images.coingecko.com/coins/images/32594/small/dydx.png?1698673495",
  GMX: "https://coin-images.coingecko.com/coins/images/18323/small/arbit.png?1696517814", // VERIFY
  PENDLE: "https://coin-images.coingecko.com/coins/images/15069/small/Pendle_Logo_Normal-03.png?1696514728", // VERIFY
  GRT: "https://coin-images.coingecko.com/coins/images/13397/small/Graph_Token.png?1696513159",
  "1INCH": "https://coin-images.coingecko.com/coins/images/13469/small/1inch-logo.jpeg?1759404663",
  ENA: "https://coin-images.coingecko.com/coins/images/36530/small/ethena.png?1711701436", // VERIFY
  ONDO: "https://coin-images.coingecko.com/coins/images/26580/small/ONDO.png?1696525656", // VERIFY

  // ── Infrastructure / Oracle / Storage ─────────────────────────────────
  FIL: "https://coin-images.coingecko.com/coins/images/12817/small/filecoin.png?1696512609",
  AR: "https://coin-images.coingecko.com/coins/images/4343/small/oRt6SiEN_400x400.jpg?1696504946",
  PYTH: "https://coin-images.coingecko.com/coins/images/31924/small/pyth.png?1701245725", // VERIFY
  QNT: "https://coin-images.coingecko.com/coins/images/3370/small/5ZOu7brX_400x400.jpg?1696504070",

  // ── Gaming / Metaverse ────────────────────────────────────────────────
  SAND: "https://coin-images.coingecko.com/coins/images/12129/small/sandbox_logo.jpg?1696511971",
  MANA: "https://coin-images.coingecko.com/coins/images/878/small/decentraland-mana.png?1696502010",
  AXS: "https://coin-images.coingecko.com/coins/images/13029/small/axie_infinity_logo.png?1696512817",
  GALA: "https://coin-images.coingecko.com/coins/images/12493/small/GALA_token_image_-_200PNG.png?1709725869",
  APE: "https://coin-images.coingecko.com/coins/images/24383/small/APECOIN.png?1756551529",
  PIXEL: "https://coin-images.coingecko.com/coins/images/35100/small/pixel-icon.png?1708339519", // VERIFY

  // ── AI / Data ─────────────────────────────────────────────────────────
  RENDER: "https://coin-images.coingecko.com/coins/images/11636/small/rndr.png?1696511529", // VERIFY
  FET: "https://coin-images.coingecko.com/coins/images/5681/small/ASI.png?1719827289",
  AGIX: "https://coin-images.coingecko.com/coins/images/2138/small/singularitynet.png?1696503103",
  OCEAN: "https://coin-images.coingecko.com/coins/images/3687/small/ocean-protocol-logo.jpg?1696504363",
  TAO: "https://coin-images.coingecko.com/coins/images/28452/small/ARUsPeNQ_400x400.jpeg?1696527447", // VERIFY
  W: "https://coin-images.coingecko.com/coins/images/35087/small/W_Token_%283%29.png?1758122686", // VERIFY

  // ── DEX / Perps ───────────────────────────────────────────────────────
  JUP: "https://coin-images.coingecko.com/coins/images/34188/small/jup.png?1704266489",
  BLUR: "https://coin-images.coingecko.com/coins/images/28453/small/blur.png?1696527448", // VERIFY

  // ── Liquid staking ────────────────────────────────────────────────────
  ETHFI: "https://coin-images.coingecko.com/coins/images/35958/small/etherfi.jpeg?1710254562", // VERIFY
  REZ: "https://coin-images.coingecko.com/coins/images/37327/small/renzo_200x200.png?1714025012", // VERIFY

  // ── Cross-chain / Bridge ──────────────────────────────────────────────
  AXL: "https://coin-images.coingecko.com/coins/images/27277/small/V-65_xQ1_400x400.jpeg?1696526329", // VERIFY
  RUNE: "https://coin-images.coingecko.com/coins/images/6595/small/THORChain_Circle_Gradient__with_Lightning_Bolt_-_Square_Transparent_Background_200px.png?1768635632",

  // ── Inscription / BRC-20 ─────────────────────────────────────────────
  ORDI: "https://coin-images.coingecko.com/coins/images/30162/small/ordi.png?1696529082", // VERIFY
  FLOKI: "https://coin-images.coingecko.com/coins/images/16746/small/PNG_image.png?1696516318",

  // ── Social / Meme ─────────────────────────────────────────────────────
  SHIB: "https://coin-images.coingecko.com/coins/images/11939/small/shiba.png?1696511800",
  PEPE: "https://coin-images.coingecko.com/coins/images/29850/small/pepe-token.jpeg?1696528776",
  WIF: "https://coin-images.coingecko.com/coins/images/33566/small/dogwifhat.jpg?1702499428",
  BONK: "https://coin-images.coingecko.com/coins/images/28600/small/bonk.jpg?1696527587",
  NOT: "https://coin-images.coingecko.com/coins/images/33453/small/rFmThDiD_400x400.jpg?1701876350", // VERIFY

  // ── Layer 1 misc ──────────────────────────────────────────────────────
  SEI: "https://coin-images.coingecko.com/coins/images/28205/small/Sei_Logo_-_Transparent.png?1696527207",
  TIA: "https://coin-images.coingecko.com/coins/images/31967/small/tia.jpg?1696530772",
  INJ: "https://coin-images.coingecko.com/coins/images/12882/small/Other_200x200.png?1738782212",
  STX: "https://coin-images.coingecko.com/coins/images/2069/small/Stacks_Logo_png.png?1709979332",
  DYM: "https://coin-images.coingecko.com/coins/images/34182/small/dym.png?1704253006", // VERIFY
  PORTAL: "https://coin-images.coingecko.com/coins/images/35436/small/portal.jpeg?1708590254", // VERIFY
  ALT: "https://coin-images.coingecko.com/coins/images/34608/small/Logomark_200x200.png?1715107868", // VERIFY

  // ── Governance / DAO ─────────────────────────────────────────────────
  ENS: "https://coin-images.coingecko.com/coins/images/19785/small/ENS.jpg?1727872989",

  // ── Misc ──────────────────────────────────────────────────────────────
  WLD: "https://coin-images.coingecko.com/coins/images/31069/small/worldcoin.jpeg?1696529903", // VERIFY
  JTO: "https://coin-images.coingecko.com/coins/images/33228/small/jto.png?1701137022", // VERIFY
  FTM: "https://coin-images.coingecko.com/coins/images/4001/small/Fantom_round.png?1696504642", // VERIFY

  // ── atlas/core/asset_universe.py (Binance perpetuals — May 2026) ─────────
  // CoinGecko `small` paths; synced via /coins/markets batch (ids → image, /large/→/small/).
  HYPE: "https://coin-images.coingecko.com/coins/images/50882/small/hyperliquid.jpg?1729431300",
  CC: "https://coin-images.coingecko.com/coins/images/70468/small/Canton-Ticker_%281%29.png?1762826299",
  XAUT: "https://coin-images.coingecko.com/coins/images/10481/small/logo.png?1774627372",
  PAXG: "https://coin-images.coingecko.com/coins/images/9519/small/paxgold.png?1696509604",
  WLFI: "https://coin-images.coingecko.com/coins/images/50767/small/wlfi.png?1756438915",
  ASTER: "https://coin-images.coingecko.com/coins/images/69040/small/_ASTER.png?1757326782",
  SKY: "https://coin-images.coingecko.com/coins/images/39925/small/sky.jpg?1724827980",
  MORPHO: "https://coin-images.coingecko.com/coins/images/29837/small/Morpho-token-icon.png?1726771230",
  KAS: "https://coin-images.coingecko.com/coins/images/25751/small/kaspa-icon-exchanges.png?1696524837",
  STABLE: "https://coin-images.coingecko.com/coins/images/69242/small/stable-logotype-framed-square-light.png?1762753913",
  JST: "https://coin-images.coingecko.com/coins/images/11095/small/JUST.jpg?1696511035",
  PUMP: "https://coin-images.coingecko.com/coins/images/67164/small/pump.jpg?1751949376",
  VVV: "https://coin-images.coingecko.com/coins/images/54023/small/VVV_Token_Transparent.png?1741856877",
  DEXE: "https://coin-images.coingecko.com/coins/images/12713/small/DEXE_token_logo.png?1696512514",
  UB: "https://coin-images.coingecko.com/coins/images/69108/small/unibase.png?1757501820",
  DASH: "https://coin-images.coingecko.com/coins/images/19/small/dash-logo.png?1696501423",
  PENGU: "https://coin-images.coingecko.com/coins/images/52622/small/PUDGY_PENGUINS_PENGU_PFP.png?1733809110",
  TRUMP: "https://coin-images.coingecko.com/coins/images/53746/small/trump.png?1737171561",
  NIGHT: "https://coin-images.coingecko.com/coins/images/71015/small/midnight.png?1765193080",
  VIRTUAL: "https://coin-images.coingecko.com/coins/images/34057/small/LOGOMARK.png?1708356054",
  CAKE: "https://coin-images.coingecko.com/coins/images/12632/small/pancakeswap-cake-logo_%281%29.png?1696512440",
  BILL: "https://coin-images.coingecko.com/coins/images/68464/small/billions.png?1755828007",
  KITE: "https://coin-images.coingecko.com/coins/images/35343/small/_KITE.png?1708317581",
  CHZ: "https://coin-images.coingecko.com/coins/images/8834/small/CHZ_Token_updated.png?1696508986",
  AERO: "https://coin-images.coingecko.com/coins/images/31745/small/token.png?1696530564",
  EDGE: "https://coin-images.coingecko.com/coins/images/102172616/small/edgex.png?1774332310",
  XTZ: "https://coin-images.coingecko.com/coins/images/976/small/Tezos-logo.png?1696502091",
  SIREN: "https://coin-images.coingecko.com/coins/images/54479/small/siren.png?1739960056",
  SUN: "https://coin-images.coingecko.com/coins/images/12424/small/RSFOmQ.png?1696512245",
  SPX: "https://coin-images.coingecko.com/coins/images/31401/small/centeredcoin_%281%29.png?1737048493",
  CFX: "https://coin-images.coingecko.com/coins/images/13079/small/3vuYMbjN.png?1696512867",
  MON: "https://coin-images.coingecko.com/coins/images/38927/small/mon.png?1766029057",
  SKYAI: "https://coin-images.coingecko.com/coins/images/55294/small/1.png?1745517593",
  ZRO: "https://coin-images.coingecko.com/coins/images/28206/small/ftxG9_TJ_400x400.jpeg?1696527208",
  GWEI: "https://coin-images.coingecko.com/coins/images/71375/small/ethgas_token_200.png?1769055039",
  BSV: "https://coin-images.coingecko.com/coins/images/6799/small/BSV.png?1696507128",
  JASMY: "https://coin-images.coingecko.com/coins/images/13876/small/JASMY200x200.jpg?1696513620",
  LAB: "https://coin-images.coingecko.com/coins/images/70014/small/lab.png?1760352822",
  KAIA: "https://coin-images.coingecko.com/coins/images/39901/small/KAIA.png?1724734368",
  PIEVERSE: "https://coin-images.coingecko.com/coins/images/68773/small/pieverse.png?1756546685",
  IOTA: "https://coin-images.coingecko.com/coins/images/692/small/IOTA_Thumbnail_%281%29.png?1743772896",
  SYRUP: "https://coin-images.coingecko.com/coins/images/51232/small/_syrup_token_logo.png?1747292046",
  XPL: "https://coin-images.coingecko.com/coins/images/66489/small/Plasma-symbol-green-1.png?1755142558",
  LIT: "https://coin-images.coingecko.com/coins/images/71121/small/lighter.png?1765888098",
  AKT: "https://coin-images.coingecko.com/coins/images/12785/small/akash-logo.png?1696512580",
  NEO: "https://coin-images.coingecko.com/coins/images/480/small/NEO_512_512.png?1696501735",
  FF: "https://coin-images.coingecko.com/coins/images/69121/small/ff.png?1757573403",
  TWT: "https://coin-images.coingecko.com/coins/images/11085/small/Trust.png?1696511026",
  FARTCOIN: "https://coin-images.coingecko.com/coins/images/50891/small/fart.jpg?1729503972",
  GRASS: "https://coin-images.coingecko.com/coins/images/40094/small/Grass.jpg?1725697048",
  IP: "https://coin-images.coingecko.com/coins/images/54035/small/Transparent_bg.png?1738075331",
  WAL: "https://coin-images.coingecko.com/coins/images/54914/small/Walrus_Token_Full_Color_200x200.png?1772722407",
  BEAT: "https://coin-images.coingecko.com/coins/images/70428/small/audiera.png?1761964064",
  CVX: "https://coin-images.coingecko.com/coins/images/15585/small/convex.png?1696515221",
  GENIUS: "https://coin-images.coingecko.com/coins/images/71660/small/genius.png?1768804210",
  RAVE: "https://coin-images.coingecko.com/coins/images/70544/small/coin.jpeg?1762452940",
  CFG: "https://coin-images.coingecko.com/coins/images/15380/small/centrifuge.PNG?1696515027",
  BAT: "https://coin-images.coingecko.com/coins/images/677/small/basic-attention-token.png?1696501867",
  EIGEN: "https://coin-images.coingecko.com/coins/images/37441/small/eigencloud.jpg?1751003565",
  SFP: "https://coin-images.coingecko.com/coins/images/13905/small/sfp.png?1696513647",
  RIVER: "https://coin-images.coingecko.com/coins/images/69318/small/river-token.png?1758523089",
  GLM: "https://coin-images.coingecko.com/coins/images/542/small/Golem_Submark_Positive_RGB.png?1696501761",
  TAG: "https://coin-images.coingecko.com/coins/images/53206/small/IMG_3196.png?1735664645",
  FLUID: "https://coin-images.coingecko.com/coins/images/14688/small/Logo_1_%28brighter%29.png?1734430693",
  ATH: "https://coin-images.coingecko.com/coins/images/36179/small/logogram_circle_dark_green_vb_green_%281%29.png?1718232706",
  SAHARA: "https://coin-images.coingecko.com/coins/images/66681/small/Token_Logo_3x.png?1750362133",
  BANANAS31: "https://coin-images.coingecko.com/coins/images/52230/small/Banana_token_image.png?1732801941",
  RSR: "https://coin-images.coingecko.com/coins/images/8365/small/RSR_Blue_Circle_1000.png?1721777856",
  IRYS: "https://coin-images.coingecko.com/coins/images/70808/small/irys.png?1764757341",
  CHIP: "https://coin-images.coingecko.com/coins/images/102171777/small/CHIP_Token_Logo_Large.png?1776777444",
  SENT: "https://coin-images.coingecko.com/coins/images/70508/small/SENTIENT-Icon-BlushForce-L.png?1762267532",
  ZEN: "https://coin-images.coingecko.com/coins/images/691/small/Horizen2.0-logo_icon-on-yellow_%281%29.png?1751696763",
  MEGA: "https://coin-images.coingecko.com/coins/images/69995/small/9fcb2fa4-b240-46e2-9016-c4f6101a139d.jpeg?1778485816",
  KAITO: "https://coin-images.coingecko.com/coins/images/54411/small/Qm4DW488_400x400.jpg?1739552780",
  AWE: "https://coin-images.coingecko.com/coins/images/8713/small/awe-network.jpg?1747816016",
  LPT: "https://coin-images.coingecko.com/coins/images/7137/small/badge-logo-circuit-green.png?1719357686",
};
/** Maps legacy tickers still seen in streams / rotation stubs to canonical logo keys. */
const LOGO_SYMBOL_ALIASES: Readonly<Record<string, string>> = {
  POL: "MATIC",
  RNDR: "RENDER",
};

/**
 * Look up a logo URL from any common symbol form.
 *
 * Accepts: "BTC", "btc", "BTCUSDT", "BTC/USDT", "BTC-USDT", "BTC:USDT"
 *
 * Returns the CDN URL if found, otherwise undefined.
 * Callers should handle undefined — <AssetLogo> renders a monogram fallback.
 */
export function getLogoUrl(symbol: string): string | undefined {
  const clean = symbol
    .replace(/[/:-]?USDT$/i, "") // strip USDT, /USDT, -USDT, :USDT
    .replace(/[/:-].*$/, "") // strip any remaining suffix
    .toUpperCase();

  const canonical = LOGO_SYMBOL_ALIASES[clean] ?? clean;
  const url = ASSET_LOGOS[canonical];
  // Return undefined (not empty string) when not found.
  // Empty string would cause <img> to load the current page URL.
  return url || undefined;
}
