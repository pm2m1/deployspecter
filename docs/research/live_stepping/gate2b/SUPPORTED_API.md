# Gate 2B: dependency provenance and the supported LangGraph API

Increment 1 of Gate 2B, an engineering compatibility experiment on a known development target. It isn't a blind
benchmark. LangGraph hasn't run against a database yet.

## Dependency provenance

| Item | Result |
|---|---|
| Environment | A separate virtual environment outside the repository, Python 3.12.14 |
| Inputs | `requirements.in`, constrained to the root lock's versions by `constraints-root-lock.txt` |
| Resolution | `requirements.txt`: 57 distributions with SHA-256 hashes, installed with `--require-hashes` |
| Root project | `pyproject.toml` and `uv.lock` unchanged; DeploySpecter installed editable, with no dependencies of its own |
| `langgraph-checkpoint-postgres` 3.2.0 | All 11 installed files byte-identical to upstream `d4149cc` (Git blob hashes); no repository Python file missing from the wheel |
| `langgraph-checkpoint` 4.3.0 | All 23 installed files identical at `d4149cc` and at tag `checkpoint==4.3.0` (`b7d053b`) |
| Installed files | Every file in all 57 distributions matches its wheel `RECORD` |
| Wheel identity | Every installed wheel matched to exactly one PyPI file and SHA-256, all present in the hashed lock |

**`uuid-utils` 0.19.0.** The earlier lookup used the distribution's metadata name, `uuid_utils`, and failed. Under
`uuid-utils`, two wheels share a tag with the installed one. The installed wheel declares exactly three tags, and only
`uuid_utils-0.19.0-cp310-abi3-macosx_10_12_x86_64.macosx_11_0_arm64.macosx_10_12_universal2.whl` declares that set
(SHA-256 `84929d8c594381c6f1444afa2a25cce9ff7edac99d35e071ebc48275bae83730`, present in the lock).

Details are in `source_comparison.json`.

## The two supported operations, as the pinned source implements them

Both run through `PostgresSaver._cursor()`, which holds the saver's own `threading.Lock`, takes the connection the saver
was given (a `psycopg.Connection`, or a pool), and opens `conn.cursor(binary=True, row_factory=dict_row)`. With no
`pipe` and `pipeline=False`, neither enters pipeline mode.

### `PostgresSaver.prune(thread_ids, strategy="delete")`

- Raises `TypeError` for a bare string, and `ValueError` for an unknown strategy, before any database work.
- Converts the ids to strings. An empty list returns `None` with no cursor and no statement.
- Otherwise opens one cursor and one `cur.connection.transaction()` block, and runs the three statements of
  `PRUNE_DELETE_THREADS_SQL` in order, each with the parameter `(thread_ids,)` as one text array:
  `DELETE FROM checkpoints`, `DELETE FROM checkpoint_blobs`, then `DELETE FROM checkpoint_writes`, each
  `WHERE thread_id = ANY(%s)`.
- Returns `None`. An error in any statement leaves the block with that exception, so the transaction rolls back and
  the exception reaches the caller unchanged.
- **Connection requirement.** On an autocommit connection, the block is a real `BEGIN`…`COMMIT`. On a
  non-autocommit connection inside an open transaction, it would be a savepoint instead. LangGraph's own helper uses
  autocommit, and the experiment will too.

### `PostgresSaver.get_tuple(config)`

- Reads `config["configurable"]["thread_id"]` (a `KeyError` if missing), plus optional `checkpoint_ns` (default `""`)
  and `checkpoint_id`.
- Runs one `SELECT_SQL` query: by id when given, otherwise `ORDER BY checkpoint_id DESC LIMIT 1`. It's a single
  statement, so under READ COMMITTED it sees one snapshot. No transaction block.
- Returns `None` if no row. Otherwise it decodes the blobs and pending writes with the saver's serializer, and returns
  a `CheckpointTuple(config, checkpoint, metadata, parent_config, pending_writes)`.
- Runs a second query (`SELECT_PENDING_SENDS_SQL`) only for a checkpoint with `v < 4` and a parent. The synthetic
  data uses v4 only, so the second query is out of scope unless added deliberately.
- **Serialization.** The adapter builds the saver with
  `JsonPlusSerializer(pickle_fallback=False, allowed_json_modules=None, allowed_msgpack_modules=None)`, so only the
  serializer's safe built-in types decode.

## Unsupported, and refused by name

| Upstream operation | Why |
|---|---|
| `put`, `put_writes` | `_cursor(pipeline=True)` enters `conn.pipeline()`, and both use `executemany`. The engine refuses explicit pipeline blocks |
| `delete_thread` | `_cursor(pipeline=True)` |
| `prune(strategy="keep_latest")` | Different control flow (a refusal query, then per-thread transactions with an isolation level). Not reviewed |
| `setup` | DDL and `CREATE INDEX CONCURRENTLY`. A bootstrap concern, never an actor operation |
| `list`, `get`, `get_delta_channel_history` | Not in scope |
| `from_conn_string` | Opens its own connection, which supervision would flag as unmanaged |
| All `a*` methods, `AsyncPostgresSaver` | Asynchronous; the engine is synchronous only |
| `ConnectionPool`, a `pipe`, a connection already in pipeline mode | Refused when the adapter is built |

## Adapter

`src/deployspecter/live/adapters/langgraph_pg.py`:

- **`PostgresSaverAdapter(conn)`** checks the library version, refuses the connection types above, and builds one real
  `PostgresSaver` with the strict serializer. It forwards `prune_delete(thread_ids)` and `get_tuple(config)` to the
  library's own methods, and refuses everything else.
- **`actor_prune_delete` and `actor_get_tuple`** are the registered entrypoints for supervised actors. They return a
  JSON-safe summary, because results cross a process boundary.
- **Provenance.** The engine records the first frame outside the driver as each operation's source. For these
  operations that is LangGraph's own `prune` or `get_tuple`, by module, function and line.

The adapter imports LangGraph only when it's used. The engine, supervisor and worker don't import it (tested).
