"""Outbox and webhook delivery for the later customer-management system.

Context: Every relevant change writes an event; a sender delivers them to registered endpoints.
Input: Events table.
Output: HTTP calls.
"""
