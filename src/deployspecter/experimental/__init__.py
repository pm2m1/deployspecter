"""EXPERIMENTAL prototype: real Python application stepping over psycopg 3, mixed-version workloads and Muse proposals.

Not certified Gate A2 evidence. It shares no code path with the frozen Gate 2A supervisor and gives none of its
isolation guarantees: handlers run in-process, as the same OS user, against a disposable local PostgreSQL only.
"""
