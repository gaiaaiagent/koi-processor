# Contextual Retrieval Bakeoff

- Generated: 2026-03-24T21:12:12.014045+00:00
- Samples: 24 reconstructed parent documents
- Candidates: gpt-4o-mini, gemini-3.1-flash-lite-preview
- Judge: gemini-2.5-flash-lite
- Raw results: `CONTEXTUAL_BAKEOFF_20260324_151212.json`

## Verdict

- Judge wins: `gpt-4o-mini` 21/24, `gemini-3.1-flash-lite-preview` 3/24, `ties` 0/24

## Metrics

| Model | Judge Wins | Mean Cost | Mean Latency | Mean Chars | Mean New Doc Terms | Mean Unsupported Terms |
|---|---:|---:|---:|---:|---:|---:|
| `gpt-4o-mini` | 21 | $0.0007 | 1.83s | 330.5 | 4.62 | 15.08 |
| `gemini-3.1-flash-lite-preview` | 3 | $0.0014 | 1.16s | 191.1 | 3.17 | 7.92 |

## Sample Breakdown

- Kinds: go=10, md=7, other=1, proto=6
- Size buckets: 15k-30k=8, 30k-60k=3, 5k-15k=13

## Notable Samples

### `regen.github:github_regen-ledger_x_ecocredit_marketplace_keeper_msg_buy_direct_test.go#chunk1`

- `gpt-4o-mini`: This chunk is part of a Go test file for the `buyDirect` functionality in the Regen Ledger's ecocredit marketplace module, defining the `buyDirectSuite` struct that holds test data and state for testing the buying process of ecocredits. It includes necessary imports and the setup for the test case, including addresses, balances, and message structures.
- `gemini-3.1-flash-lite-preview`: This chunk defines the `buyDirectSuite` struct and the `TestBuyDirect` function, which initializes the testing environment for the `MsgBuyDirect` message using the Gocuke framework.
- Judge: `gpt-4o-mini` (Snippet A provides more context by explaining the purpose of the `buyDirectSuite` struct and its role in testing the ecocredit marketplace's buy functionality, making it more informative for retrieval.)

### `regen.github:github_regen-ledger_x_data_spec_07_client.md#chunk7`

- `gpt-4o-mini`: This chunk is part of the section detailing the various gRPC endpoints available for querying the `data` module, specifically focusing on the `ResolversByHash` endpoint, which allows users to retrieve resolvers associated with a specific ContentHash. It includes an example of how to use the endpoint and the expected output format.
- `gemini-3.1-flash-lite-preview`: This chunk provides examples of gRPC query endpoints for the `data` module, specifically demonstrating how to retrieve resolvers by their associated content hash or URL.
- Judge: `gpt-4o-mini` (Candidate A is better because it accurately situates the chunk within the document's structure, specifically mentioning the 'ResolversByHash' endpoint and its purpose, while also including the provided example and output for clarity.)

### `regen.github:github_regen-ledger_proto_regen_ecocredit_v1_query.proto#chunk27`

- `gpt-4o-mini`: This chunk is part of the Protocol Buffers (proto) definition for the Regen Ecocredit system, specifically detailing the structure of the `ClassInfo` and `ProjectInfo` messages, which include attributes such as admin, metadata, and identifiers for credit classes and projects. It provides essential information for querying and managing ecocredit classes and projects within the system.
- `gemini-3.1-flash-lite-preview`: This chunk defines the data structures for `ClassInfo` and `ProjectInfo`, which represent the core entities within the `regen.ecocredit.v1` module.
- Judge: `gpt-4o-mini` (Candidate B provides more context by mentioning the Protocol Buffers definition and the specific attributes within the messages, which is more useful for retrieval than Candidate A's more general description.)

### `regen.github:github_regen-ledger_orm_model_ormtable_table_test.go#chunk2`

- `gpt-4o-mini`: This chunk is part of a test function in the Go test file for the `ormtable` package, specifically focusing on debugging and validating the encoding and decoding of entries in a key-value store, as well as ensuring the correctness of pagination functionality in the ORM implementation. It includes the setup for a debug backend and assertions to compare the output against a golden file for consistency.
- `gemini-3.1-flash-lite-preview`: This chunk contains the conclusion of the `TestScenario` function, which validates ORM storage using golden files, and the beginning of `TestPaginationLimitCountTotal`, which tests pagination functionality.
- Judge: `gpt-4o-mini` (Candidate A provides more specific details about the purpose of the code, including debugging, encoding/decoding validation, and pagination, which is more informative than Candidate B's general description.)

### `regen.github:github_regen-ledger_app_app.go#chunk26`

- `gpt-4o-mini`: This chunk is part of the `NewRegenApp` function in the `regen-ledger/app/app.go` file, where it discusses the configuration of the Wasm client keeper and the setup of query plugins for handling specific IBC queries within the Regen application. It also includes the initialization of a virtual machine for the light client functionality.
- `gemini-3.1-flash-lite-preview`: This chunk details the configuration and initialization of the `WasmClientKeeper` within the `NewRegenApp` function, including the setup of query plugins and the underlying Wasm virtual machine for IBC light clients.
- Judge: `gemini-3.1-flash-lite-preview` (Snippet B is better because it is more concise and directly addresses the core function of the chunk: configuring the WasmClientKeeper and its associated components.)

### `regen.github:github_regen-ledger_x_ecocredit_module_module.go#chunk10`

- `gpt-4o-mini`: This chunk is part of the implementation of the `Module` struct in the ecocredit module, detailing methods related to the simulation of the module, including the generation of randomized genesis states and the registration of store decoders, as well as the handling of weighted operations during simulation. It follows the `BeginBlock` method, which manages the state at the beginning of each block.
- `gemini-3.1-flash-lite-preview`: This chunk contains the `BeginBlock` implementation and the beginning of the `AppModuleSimulation` interface methods, which define how the ecocredit module handles block initialization and simulation operations.
- Judge: `gemini-3.1-flash-lite-preview` (Candidate A accurately describes the chunk's content, including the BeginBlock implementation and the start of simulation methods, providing concise and relevant context.)

### `regen.github:github_regen-ledger_x_data_client_query.go#chunk1`

- `gpt-4o-mini`: This chunk is part of the implementation of various query commands for the data module in the Regen Ledger client, specifically detailing the addition of commands for querying attestations and resolvers, as well as converting between IRIs and ContentHashes. It follows the definition of the main command structure and precedes the specific command for querying data anchors by IRI.
- `gemini-3.1-flash-lite-preview`: This chunk concludes the registration of query commands in the `QueryCmd` function and begins the implementation of the `QueryAnchorByIRICmd` command for the `x/data` module.
- Judge: `gpt-4o-mini` (Snippet A provides better context by listing the specific commands being added and their purpose within the data module's client implementation.)

### `regen.github:github_regen-ledger_app_client_cli_genaccounts.go#chunk1`

- `gpt-4o-mini`: This chunk is part of the `AddGenesisAccountCmd` function in the `genaccounts.go` file, which defines a Cobra command for adding a new genesis account to the `genesis.json` file in a Cosmos SDK application, including options for specifying account details and vesting parameters.
- `gemini-3.1-flash-lite-preview`: This chunk contains the beginning of the `AddGenesisAccountCmd` function, which defines the CLI command for adding accounts to the genesis file. It includes the command's metadata and the initial logic for parsing the account address or key name from the user input.
- Judge: `gpt-4o-mini` (Snippet B provides more useful document-level context by mentioning the file name and the overall purpose of the command, including vesting parameters, which aids retrieval.)
