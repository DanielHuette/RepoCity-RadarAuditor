"""Customer search: businesses of a trade in a city, ranked by signs of a neglected Google profile.

Context: The app asks for a search (public.request_prospect_search); the worker reads the list from
the Places API (search.py), ranks it with config/kundensuche.yaml (criteria.py, assessment.py) and
stores the results; nothing is sent to the businesses.
Input: Trade and city.
Output: Ranked results with one reason sentence and the lawful contact ways per business.
"""
