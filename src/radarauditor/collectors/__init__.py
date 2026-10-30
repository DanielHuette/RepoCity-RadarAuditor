"""Data collectors: each reads one public source and returns a typed snapshot.

Context: Run by the worker for a COLLECT job; results are merged into one RawAudit.
Input: Customer, locations, keywords.
Output: Snapshot models.
"""
