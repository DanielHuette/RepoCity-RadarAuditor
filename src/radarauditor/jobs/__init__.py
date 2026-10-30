"""Job queue: the worker loop, the hand-off to Claude Code and the reading of incoming mail.

Context: Jobs are rows in Supabase; the worker runs COLLECT, SCORE, REPORT, SERP_RETRY, OPTIMIZE and
APPLY, ANALYSE goes to Claude Code via the hand-off, and while idle Claude Code reads the mails the
CRM queued for triage.
Input: Jobs table, crm.mail_triage.
Output: Updated audits and documents, classified mails.
"""
