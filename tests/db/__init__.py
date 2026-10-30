"""Helpers that run the Supabase migrations against a throwaway PostgreSQL.

Context: Shared by the migration tests that need a real database. | Input: PostgreSQL binaries. |
Output: Cluster, database and project builders.
"""
