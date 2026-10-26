`revelio.pine` preserves the supplied indicator text with normalized LF line endings.
`provenance.json` records original-byte and normalized-text SHA-256 hashes.
`revelio.original.base64` preserves the original attachment bytes, including CRLF.
Decode it with Python's `base64.b64decode` to recover the exact original source.
No external TradingView parity verification has been performed.
