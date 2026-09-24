"""TikTok's official Display API: the user's own posts' public stats, read-only.

Used to fill the performance log without copying numbers by hand. It gives
views, likes, comments and shares per video; watch time is not in this API
(only in the Business API, which needs a Business account), so that stays a
manual column. Nothing here posts, follows, comments or scrapes.
"""
