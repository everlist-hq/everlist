# x402 Wire Format — C1 Research

Researched 2026-09-09 from: docs.x402.org, x402-foundation/x402 spec v2 (GitHub), Coinbase/Eco/Stripe references, ERC-3009 docs. **Verified against official sources; asset-support claims beyond USDC-on-EVM are hypotheses until C3.**

## 1. Protocol flow (4 steps)

```
1. client  -> GET /resource                        (no payment)
2. server  -> 402 Payment Required + payment terms (accepts[]: what/whom/how-much)
3. client  -> signs payment payload (EIP-3009 for 'exact' USDC), retries
             GET /resource + X-PAYMENT: <base64(JSON payload)>
4. server/facilitator -> verifies + settles on-chain -> 200 OK
             + X-PAYMENT-RESPONSE: {success, transaction hash, network}
```

## 2. Literal 402 response (v1 wire format, from official docs)

```http
HTTP/1.1 402 Payment Required
Content-Type: application/json

{
  "x402Version": 1,
  "accepts": [{
    "scheme": "exact",
    "network": "base",
    "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    "maxAmountRequired": "1000",
    "payTo": "0xMerchant...",
    "resource": "https://api.example.com/premium/events",
    "description": "Premium event feed",
    "maxTimeoutSeconds": 300,
    "extra": { "name": "USD Coin", "version": "2" }
  }]
}
```

Key fields: `scheme` ("exact" = fixed amount), `network` (base, base-sepolia, ...), `asset` (USDC contract address), `maxAmountRequired` (**integer, asset's smallest unit** — USDC has 6 decimals: $0.001 → "1000"), `payTo` (recipient address), `resource` (URL being paid for).

**v2 transport note:** spec v2 moves the PaymentRequired object into a Base64-encoded `PAYMENT-REQUIRED` header (same schema), with `x402Version: 2` and new schemes (`exact`, `upto`, batch settlement). v1 inline-JSON 402 bodies remain the widely deployed form.

## 3. Payment payload (client → server, X-PAYMENT header)

Header value = `base64(JSON)`:

```json
{
  "x402Version": 1,
  "scheme": "exact",
  "network": "base-sepolia",
  "payload": {
    "signature": "0x<65-byte EIP-712 signature, base64 or hex per impl>",
    "authorization": {
      "from": "0xPayerAddress",
      "to": "0xMerchantAddress",
      "value": "1000",
      "validAfter": 0,
      "validBefore": 1730000000,
      "nonce": "0x<32-byte-random>"
    }
  }
}
```

The `authorization` object is signed as **EIP-712 typed data** against the USDC contract domain:

```js
domain = { name: "USD Coin", version: "2", chainId: 84532 /* base-sepolia */,
           verifyingContract: "0x036CbD53842c5426634e7929541eC2318f3dCF7e" }
types  = TransferWithAuthorization:
         from(address) to(address) value(uint256)
         validAfter(uint256) validBefore(uint256) nonce(bytes32)
```

This is **ERC-3009 transferWithAuthorization**: the client signs a gasless transfer; the facilitator/server submits `transferWithAuthorization` on-chain to settle. Private key never leaves the client. Error codes include: `invalid_exact_evm_payload_authorization_valid_before` (expired), `invalid_exact_evm_payload_authorization_value` (insufficient amount).

## 4. Verification steps (server side, per docs)

1. Decode base64 X-PAYMENT → JSON
2. Check `scheme`/`network`/`x402Version` match one of the offers in the earlier 402
3. Check authorization: `to == payTo`, `value >= maxAmountRequired`, `validAfter <= now < validBefore`, nonce unused
4. Verify EIP-712 signature recovers to `from`
5. Settle (below) or delegate to facilitator

## 5. Settlement + who pays fees

- **Self-settling server:** submits `transferWithAuthorization` itself (must hold gas).
- **Facilitator:** POST payload to facilitator `/verify` → returns validity; `/settle` → submits on-chain, returns tx hash. Server returns resource + `X-PAYMENT-RESPONSE: {"success":true,"transaction":"0x...","network":"base-sepolia"}`.
- **Fees:** the **facilitator charges the resource server** (merchant side) a fee per settlement, not the client; the client pays only the asset amount plus no gas (ERC-3009 is gasless for the payer). Self-settling servers pay their own gas.

## 6. Testnet + faucet

- Network: **base-sepolia** (chainId 84532), USDC: `0x036CbD53842c5426634e7929541eC2318f3dCF7e`
- Faucet: Coinbase Developer Platform faucet / `faucet.circle.com` (USDC test tokens + base gas)
- C3a will validate against this network.

## 7. Network / scheme matrix (official + hypothesis)

| Network | chainId | Native USDC | x402 'exact' | Our assets on this rail |
| --- | --- | --- | --- | --- |
| base | 8453 | ✅ official | ✅ | **USDC ✅ verified; ETH via native-transfer scheme — hypothesis** |
| base-sepolia | 84532 | ✅ testnet | ✅ | USDC test — C3a target |
| Other EVM (Polygon, Arbitrum...) | varies | ✅ | ✅ per network | hypothesis |
| Cardano (ADA) | — | n/a | ❌ not in spec | **hypothesis: needs custom scheme/adapter — NOT part of x402 core** |
| Fetch (FET) | — | n/a | ❌ | **hypothesis: custom adapter; FET stays distribution-channel per round-5 decision** |

**Honest finding for the manifest:** x402 core covers EVM + USDC (and v2 adds more schemes). ADA/FET acceptance will need custom payment schemes or off-x402 adapters — they cannot ride the standard 'exact' scheme. This updates our earlier manifest assumption and must be reflected in C2's capabilities block.

## 8. Implications for our hub (C2)

- `GET /premium/events` returns 402 with `accepts[]` offering `exact`/`base-sepolia` USDC in **SIMULATED** mode (stub verification labeled in body + manifest `capabilities.premium.status`).
- Production mode (later, C3a): real EIP-3009 verification via facilitator.
