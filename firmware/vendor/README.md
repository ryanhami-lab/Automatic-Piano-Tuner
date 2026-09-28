# Vendored tokenizer

`jsmn.h` is pinned to zserge/jsmn commit
`25647e692c7906b96ffd2b05ca54c097948e879c`.

Upstream: https://github.com/zserge/jsmn

SHA-256: `c04533e9181e1e33baceb0f55ac449b05145bb936e8c68cc77dfe0d8277514fb`.
The original MIT license is preserved as `LICENSE.jsmn`.

The core uses exactly 64 tokens and separately validates the flat schema,
punctuation, number grammar, character set, field uniqueness, and limits.
Tokenization alone does not admit a command.
